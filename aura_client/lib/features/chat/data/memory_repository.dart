import 'dart:convert';
import 'package:drift/drift.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;
import '../../../core/database/database.dart';
import '../../../core/database/database_provider.dart';
import '../../../core/config/config.dart';
import '../../auth/presentation/auth_provider.dart';
import '../../../core/security/device_validation_service.dart';

class MemoryItem {
  final String id;
  final String content;

  MemoryItem({required this.id, required this.content});
}

class MemoryRepository {
  final AppDatabase _db;
  final Ref _ref;
  static const String _backendUrl = "${AppConfig.backendUrl}/api/memory";

  MemoryRepository(this._db, this._ref);

  // Watch local cache of memories
  Stream<List<UserMemory>> watchLocalMemories() {
    return (_db.select(_db.userMemories)
          ..orderBy([(m) => OrderingTerm(expression: m.createdAt, mode: OrderingMode.desc)]))
        .watch();
  }

  // Fetch all memories from backend and sync to local Drift database
  Future<List<MemoryItem>> syncMemories() async {
    try {
      final token = await _ref.read(authProvider.notifier).getIdToken();
      final headers = <String, String>{};
      if (token != null) {
        headers['Authorization'] = 'Bearer $token';
      }
      headers.addAll(DeviceValidationService.getAttestationHeaders());
      final response = await http.get(Uri.parse("$_backendUrl/list"), headers: headers);
      if (response.statusCode == 200) {
        final List list = jsonDecode(response.body);
        final items = list.map((item) {
          return MemoryItem(
            id: item['id'].toString(),
            content: item['content'] ?? '',
          );
        }).toList();

        // Sync to local DB: Clear and overwrite cache
        await _db.transaction(() async {
          await _db.delete(_db.userMemories).go();
          for (final item in items) {
            await _db.into(_db.userMemories).insertOnConflictUpdate(
              UserMemoriesCompanion.insert(
                id: item.id,
                fact: item.content,
                createdAt: DateTime.now(),
              ),
            );
          }
        });

        return items;
      } else {
        throw Exception("Failed to fetch memories from server: ${response.statusCode}");
      }
    } catch (e) {
      debugPrint("Memory sync failed: $e. Falling back to local cache.");
      final cached = await _db.select(_db.userMemories).get();
      return cached.map((c) => MemoryItem(id: c.id, content: c.fact)).toList();
    }
  }

  // Asynchronously trigger backend fact extraction from recent conversation messages
  Future<void> triggerMemoryExtraction(List<Message> recentMessages) async {
    if (recentMessages.isEmpty) return;

    try {
      final token = await _ref.read(authProvider.notifier).getIdToken();
      final payload = recentMessages.map((m) => {
        "role": m.role,
        "content": m.content,
      }).toList();

      final headers = {"Content-Type": "application/json"};
      if (token != null) {
        headers['Authorization'] = 'Bearer $token';
      }
      headers.addAll(DeviceValidationService.getAttestationHeaders());

      final response = await http.post(
        Uri.parse("$_backendUrl/extract"),
        headers: headers,
        body: jsonEncode({"messages": payload}),
      );

      if (response.statusCode == 200) {
        debugPrint("Successfully extracted memories: ${response.body}");
        // Trigger a sync to update local Drift DB cache
        await syncMemories();
      }
    } catch (e) {
      debugPrint("Trigger memory extraction failed: $e");
    }
  }

  // Delete memory fact from both server and local Drift cache
  Future<void> deleteMemory(String memoryId) async {
    // 1. Delete locally
    await (_db.delete(_db.userMemories)..where((m) => m.id.equals(memoryId))).go();

    // 2. Delete remotely
    try {
      final token = await _ref.read(authProvider.notifier).getIdToken();
      final headers = <String, String>{};
      if (token != null) {
        headers['Authorization'] = 'Bearer $token';
      }
      headers.addAll(DeviceValidationService.getAttestationHeaders());
      final pointId = int.tryParse(memoryId);
      if (pointId != null) {
        final response = await http.delete(Uri.parse("$_backendUrl/delete/$pointId"), headers: headers);
        if (response.statusCode != 200) {
          debugPrint("Failed to delete remote memory point $pointId: ${response.body}");
        }
      }
    } catch (e) {
      debugPrint("Failed to delete remote memory: $e");
    }
  }

  // Manually add memory fact to server and local Drift cache
  Future<void> saveMemory(String fact) async {
    // Save locally first
    final id = (fact.hashCode & 0xFFFFFFFFFFFFFFFF).toString();
    await _db.into(_db.userMemories).insertOnConflictUpdate(
      UserMemoriesCompanion.insert(
        id: id,
        fact: fact,
        createdAt: DateTime.now(),
      ),
    );

    // Save remotely
    try {
      final token = await _ref.read(authProvider.notifier).getIdToken();
      final headers = {"Content-Type": "application/json"};
      if (token != null) {
        headers['Authorization'] = 'Bearer $token';
      }
      headers.addAll(DeviceValidationService.getAttestationHeaders());
      final response = await http.post(
        Uri.parse("$_backendUrl/save"),
        headers: headers,
        body: jsonEncode({"fact": fact}),
      );
      if (response.statusCode != 200) {
        debugPrint("Failed to save memory remotely: ${response.body}");
      }
    } catch (e) {
      debugPrint("Save memory remotely failed: $e");
    }
  }
}

final memoryRepositoryProvider = Provider<MemoryRepository>((ref) {
  final db = ref.watch(databaseProvider);
  return MemoryRepository(db, ref);
});

// Watch local database memories stream
final localMemoriesStreamProvider = StreamProvider.autoDispose<List<UserMemory>>((ref) {
  final repo = ref.watch(memoryRepositoryProvider);
  return repo.watchLocalMemories();
});
