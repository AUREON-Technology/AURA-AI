import 'dart:convert';
import 'package:drift/drift.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/config/config.dart';
import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/security/device_validation_service.dart';
import 'package:aura_client/core/security/deterministic_id.dart';

class MemoryItem {
  final String id;
  final String content;

  MemoryItem({required this.id, required this.content});
}

class MemoryRepository {
  final AppDatabase _db;
  final Ref _ref;
  final http.Client? _http;
  static const String _backendUrl = "${AppConfig.backendUrl}/api/v3/memory";

  MemoryRepository(this._db, this._ref, {http.Client? httpClient}) : _http = httpClient;

  Future<Map<String, String>> _headers(String? token, {bool jsonBody = false}) async {
    final headers = <String, String>{};
    if (jsonBody) headers['Content-Type'] = 'application/json';
    if (token != null) headers['Authorization'] = 'Bearer $token';
    headers.addAll(await DeviceValidationService.getAttestationHeaders());
    return headers;
  }

  Future<http.Response> _send(Future<http.Response> Function(Map<String, String> headers) send) async {
    final tokens = _ref.read(tokenManagerProvider);
    Future<http.Response> once() async {
      final token = await tokens.getValidAccessToken();
      return send(await _headers(token, jsonBody: true));
    }

    var response = await once();
    if (response.statusCode == 401) {
      debugPrint("[MemoryRepository] 401 — invalidating access token and retrying once");
      tokens.invalidateAccessToken();
      response = await once();
    }
    return response;
  }

  // Watch local cache of memories
  Stream<List<UserMemory>> watchLocalMemories() {
    return (_db.select(_db.userMemories)
          ..orderBy([(m) => OrderingTerm(expression: m.createdAt, mode: OrderingMode.desc)]))
        .watch();
  }

  // Load local memories from SQLite cache directly
  Future<List<MemoryItem>> loadLocalMemories() async {
    final cached = await _db.select(_db.userMemories).get();
    return cached.map((c) => MemoryItem(id: c.id, content: c.fact)).toList();
  }

  List<MemoryItem> _parseMemories(String body) {
    final decoded = jsonDecode(body);
    final list = decoded is Map<String, dynamic> ? decoded['memories'] : decoded;
    if (list is! List) return [];
    return list.map((item) {
      final map = item is Map<String, dynamic> ? item : <String, dynamic>{};
      return MemoryItem(
        id: map['id']?.toString() ?? '',
        content: map['content']?.toString() ?? '',
      );
    }).where((item) => item.id.isNotEmpty).toList();
  }

  // Fetch all memories from backend and sync to local Drift database
  Future<List<MemoryItem>> syncMemories() async {
    try {
      final uri = Uri.parse(_backendUrl);
      final response = await _send((headers) {
        final client = _http;
        return client != null
            ? client.get(uri, headers: headers)
            : http.get(uri, headers: headers);
      });
      if (response.statusCode == 200) {
        final items = _parseMemories(response.body);

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

  // Delete memory fact from both server and local Drift cache
  Future<void> deleteMemory(String memoryId) async {
    await (_db.delete(_db.userMemories)..where((m) => m.id.equals(memoryId))).go();

    try {
      final uri = Uri.parse("$_backendUrl/$memoryId");
      final response = await _send((headers) {
        final client = _http;
        return client != null
            ? client.delete(uri, headers: headers)
            : http.delete(uri, headers: headers);
      });
      if (response.statusCode != 200) {
        debugPrint("Failed to delete remote memory $memoryId: ${response.body}");
      }
    } catch (e) {
      debugPrint("Failed to delete remote memory: $e");
    }
  }

  // Manually add memory fact to server and local Drift cache
  Future<void> saveMemory(String fact) async {
    final id = getDeterministicId(fact);
    await _db.into(_db.userMemories).insertOnConflictUpdate(
      UserMemoriesCompanion.insert(
        id: id,
        fact: fact,
        createdAt: DateTime.now(),
      ),
    );

    try {
      final uri = Uri.parse(_backendUrl);
      final response = await _send((headers) {
        final client = _http;
        final body = jsonEncode({"content": fact});
        return client != null
            ? client.post(uri, headers: headers, body: body)
            : http.post(uri, headers: headers, body: body);
      });
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
