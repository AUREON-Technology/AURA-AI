import 'dart:convert';
import 'package:drift/drift.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;
import 'package:aura_client/core/network/idempotency.dart';
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/config/backend_url_provider.dart';
import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/network/aura_api_error.dart';
import 'package:aura_client/core/security/device_validation_service.dart';
import 'package:aura_client/core/sync/remote_deletes.dart';
import 'package:aura_client/core/sync/sync_payloads.dart';
import 'package:uuid/uuid.dart';

class MemoryItem {
  final String id;
  final String content;
  final String kind;
  final String? subject;
  final double? confidence;
  final String? source;

  MemoryItem({
    required this.id,
    required this.content,
    this.kind = 'semantic',
    this.subject,
    this.confidence,
    this.source,
  });
}

class MemoryRepository {
  final AppDatabase _db;
  final Ref _ref;
  final http.Client? _http;
  static const _uuid = Uuid();
  MemoryRepository(this._db, this._ref, {http.Client? httpClient}) : _http = httpClient;

  String get _backendUrl => '${_ref.read(backendUrlProvider)}/api/v3/memory';

  Future<Map<String, String>> _headers(String? token, {bool jsonBody = false}) async {
    final headers = <String, String>{};
    if (jsonBody) headers['Content-Type'] = 'application/json';
    if (token != null) headers['Authorization'] = 'Bearer $token';
    headers.addAll(
      await DeviceValidationService.getAttestationHeaders(
        backendUrl: _ref.read(backendUrlProvider),
      ),
    );
    return headers;
  }

  Future<http.Response> _send(
    Future<http.Response> Function(Map<String, String> headers) send, {
    bool write = false,
  }) async {
    final tokens = _ref.read(tokenManagerProvider);
    // One key per logical write, shared by the 401 re-send below (audit FSA-046).
    final idempotencyKey = write ? newIdempotencyKey() : null;
    Future<http.Response> once() async {
      final token = await tokens.getValidAccessToken();
      final headers = await _headers(token, jsonBody: true);
      if (idempotencyKey != null) headers[idempotencyKeyHeader] = idempotencyKey;
      return send(headers);
    }

    var response = await once();
    if (response.statusCode == 401) {
      debugPrint("[MemoryRepository] 401 — invalidating access token and retrying once");
      tokens.invalidateAccessToken();
      response = await once();
    }
    return response;
  }

  /// Watch local cache of memories with pagination
  Stream<List<UserMemory>> watchLocalMemories({int limit = 100, int offset = 0}) {
    return (_db.select(_db.userMemories)
          ..orderBy([(m) => OrderingTerm(expression: m.createdAt, mode: OrderingMode.desc)])
          ..limit(limit, offset: offset))
        .watch();
  }

  /// Load local memories with pagination
  Future<List<MemoryItem>> loadLocalMemories({int limit = 100, int offset = 0}) async {
    final cached = await (_db.select(_db.userMemories)
          ..orderBy([(m) => OrderingTerm(expression: m.createdAt, mode: OrderingMode.desc)])
          ..limit(limit, offset: offset))
        .get();
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
        kind: map['kind']?.toString() ?? 'semantic',
        subject: map['subject']?.toString(),
        confidence: map['confidence'] is num ? (map['confidence'] as num).toDouble() : null,
        source: map['source']?.toString(),
      );
    }).where((item) => item.id.isNotEmpty).toList();
  }

  RemoteDeletes get _remoteDeletes => RemoteDeletes(_db, _ref, httpClient: _http);

  /// Fetches the server's memories and merges them into the local cache.
  ///
  /// A memory with an unconfirmed delete is neither shown nor re-cached, so a
  /// "forget" that has not reached the server yet does not bring the memory
  /// back (F-07); pending deletes are retried first. When the server's answer
  /// is the complete list (fewer than [limit]), synced local rows it no
  /// longer has were deleted elsewhere and are dropped here too.
  ///
  /// A failed fetch falls back to the local cache. With [throwOnFailure] it
  /// throws instead, for a caller that has to *say* the list may be stale
  /// (the Memory page, audit F-33) rather than quietly show the cache.
  Future<List<MemoryItem>> syncMemories({
    int limit = 100,
    int offset = 0,
    bool throwOnFailure = false,
  }) async {
    try {
      await _remoteDeletes.flush();
      final forgotten = await _remoteDeletes.pendingIds(RemoteDeleteKind.memory);
      final uri = Uri.parse(_backendUrl).replace(queryParameters: {
        "limit": limit.toString(),
        "offset": offset.toString(),
      });
      final response = await _send((headers) {
        final client = _http;
        return client != null
            ? client.get(uri, headers: headers)
            : http.get(uri, headers: headers);
      });
      if (response.statusCode == 200) {
        final items = _parseMemories(response.body).where((item) => !forgotten.contains(item.id)).toList();

        await _db.transaction(() async {
          for (final item in items) {
            await _db.into(_db.userMemories).insertOnConflictUpdate(
              UserMemoriesCompanion.insert(
                id: item.id,
                fact: item.content,
                createdAt: DateTime.now(),
                isSynced: const Value(true),
                updatedAt: Value(DateTime.now()),
              ),
            );
          }
          if (offset == 0 && items.length < limit) {
            final serverIds = items.map((i) => i.id).toList();
            await (_db.delete(_db.userMemories)
                  ..where((m) => m.isSynced.equals(true) & m.id.isNotIn(serverIds)))
                .go();
          }
        });

        return items;
      } else {
        throw AuraApiError.fromResponse(
          statusCode: response.statusCode,
          body: response.body,
          retryAfterHeader: response.headers['retry-after'],
          correlationId: response.headers['x-correlation-id'],
        );
      }
    } catch (e) {
      if (throwOnFailure) rethrow;
      debugPrint("Memory sync failed: $e. Falling back to local cache.");
      final cached = await _db.select(_db.userMemories).get();
      return cached.map((c) => MemoryItem(id: c.id, content: c.fact)).toList();
    }
  }

  /// Forgets a memory locally and on the server.
  ///
  /// Two server-side copies have to go: the memory the assistant recalls
  /// from (`DELETE /api/v3/memory/{id}`, retried until confirmed by
  /// [RemoteDeletes]) and its encrypted sync row (a tombstone through the
  /// outbox, which the server stores without the ciphertext). Both use the
  /// server's own id, which [saveMemory] adopts -- the client used to delete
  /// by a local hash the server had never heard of, got 404, and the memory
  /// kept being recalled in every turn (F-07).
  ///
  /// Returns whether the server confirmed the delete. False means it is
  /// gone here and queued for the server, which the caller should say rather
  /// than imply it is done everywhere (audit F-33).
  Future<bool> deleteMemory(String memoryId) async {
    await (_db.delete(_db.userMemories)..where((m) => m.id.equals(memoryId))).go();
    await _db.enqueueOutbox(table: 'memories', recordId: memoryId, operation: 'delete');
    return _remoteDeletes.request(RemoteDeleteKind.memory, memoryId);
  }

  /// Saves a memory to the server's memory store and the local cache.
  ///
  /// The local row starts under a client UUID and is re-keyed to the id the
  /// server returns, so a later delete names the record the server actually
  /// holds. That id can be an existing memory's: the server folds a restated
  /// fact into the one it already has. The encrypted sync copy is queued
  /// under the final id.
  Future<void> saveMemory(
    String fact, {
    String kind = 'semantic',
    String? subject,
  }) async {
    // A UUID: the sync table's primary key is one, and the decimal
    // `getDeterministicId` hash this used to be was rejected by every push
    // ("id must be a UUID"), leaving the row in the outbox forever (F-13).
    // Random rather than derived from the text: two users saving the same
    // sentence must not collide on one global primary key.
    var id = _uuid.v4();
    await _db.into(_db.userMemories).insertOnConflictUpdate(
      UserMemoriesCompanion.insert(
        id: id,
        fact: fact,
        createdAt: DateTime.now(),
        isSynced: const Value(false),
        updatedAt: Value(DateTime.now()),
      ),
    );

    try {
      final uri = Uri.parse(_backendUrl);
      final response = await _send((headers) {
        final client = _http;
        final body = jsonEncode({
          "content": fact,
          "kind": kind,
          if (subject != null && subject.isNotEmpty) "subject": subject,
        });
        final sent = client != null
            ? client.post(uri, headers: headers, body: body)
            : http.post(uri, headers: headers, body: body);
        return sent.timeout(const Duration(seconds: 20));
      }, write: true);
      final serverId = response.statusCode == 200 ? _serverId(response.body) : null;
      if (serverId == null) {
        debugPrint("Failed to save memory remotely: ${response.statusCode} ${response.body}");
      } else {
        await _db.transaction(() async {
          if (serverId != id) {
            await (_db.delete(_db.userMemories)..where((m) => m.id.equals(id))).go();
          }
          await _db.into(_db.userMemories).insertOnConflictUpdate(
            UserMemoriesCompanion.insert(
              id: serverId,
              fact: fact,
              createdAt: DateTime.now(),
              isSynced: const Value(true),
              updatedAt: Value(DateTime.now()),
            ),
          );
        });
        id = serverId;
      }
    } catch (e) {
      debugPrint("Save memory remotely failed: $e");
    }

    await _db.enqueueOutbox(
      table: 'memories',
      recordId: id,
      operation: 'upsert',
      payload: SyncPayloads.userMemory(fact),
    );
  }

  String? _serverId(String body) {
    try {
      final decoded = jsonDecode(body);
      final id = decoded is Map<String, dynamic> ? decoded['id'] : null;
      return id is String && id.isNotEmpty ? id : null;
    } catch (_) {
      return null;
    }
  }
}

final memoryRepositoryProvider = Provider<MemoryRepository>((ref) {
  final db = ref.watch(databaseProvider);
  return MemoryRepository(db, ref);
});

// Watch local database memories stream with pagination
final localMemoriesStreamProvider = StreamProvider.autoDispose.family<List<UserMemory>, ({int limit, int offset})>((ref, params) {
  final repo = ref.watch(memoryRepositoryProvider);
  return repo.watchLocalMemories(limit: params.limit, offset: params.offset);
});