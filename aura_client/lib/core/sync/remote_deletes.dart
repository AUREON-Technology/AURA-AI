import 'package:drift/drift.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;
import 'package:aura_client/core/network/idempotency.dart';

import '../auth/token_manager.dart';
import '../config/backend_url_provider.dart';
import '../database/database.dart';
import '../database/database_provider.dart';
import '../security/device_validation_service.dart';

/// Server-side state a delete has to reach that the sync outbox does not
/// carry: a memory in the server's memory store (what the assistant recalls
/// from), and a chat session's transcript (what it re-reads every turn).
enum RemoteDeleteKind {
  memory('memory'),
  chatSession('chat_session');

  /// `PendingDeletes.targetTable` value. Distinct from every sync table name.
  final String table;
  const RemoteDeleteKind(this.table);
}

/// Makes "forget this memory" and "delete this chat" reach the server even
/// when the device is offline or the call fails (audit F-07).
///
/// A delete used to be one fire-and-forget request whose failure was only
/// `debugPrint`ed, so the server kept recalling the memory, and kept
/// re-injecting the deleted conversation, indefinitely. Now the delete is
/// recorded in `PendingDeletes` first, attempted at once, and retried on
/// every sync until the server confirms it. 404 counts as done: there is
/// nothing left to delete.
class RemoteDeletes {
  final AppDatabase _db;
  final Ref _ref;
  final http.Client? _http;
  RemoteDeletes(this._db, this._ref, {http.Client? httpClient}) : _http = httpClient;

  /// Records the delete durably, then tries it now. Returns whether the
  /// server has confirmed it.
  Future<bool> request(RemoteDeleteKind kind, String id) async {
    await _db.into(_db.pendingDeletes).insertOnConflictUpdate(
          PendingDeletesCompanion.insert(recordId: id, targetTable: kind.table, deletedAt: DateTime.now()),
        );
    return _attempt(kind, id);
  }

  /// Retries every delete the server has not confirmed yet.
  Future<void> flush() async {
    for (final kind in RemoteDeleteKind.values) {
      for (final id in await pendingIds(kind)) {
        await _attempt(kind, id); // a failure stays pending for the next sync
      }
    }
  }

  /// Ids with a delete the server has not confirmed. A list fetched from the
  /// server meanwhile must not bring these back.
  Future<Set<String>> pendingIds(RemoteDeleteKind kind) async {
    final rows = await (_db.select(_db.pendingDeletes)..where((t) => t.targetTable.equals(kind.table))).get();
    return rows.map((r) => r.recordId).toSet();
  }

  Future<bool> _attempt(RemoteDeleteKind kind, String id) async {
    try {
      // Stable across retries (audit FSA-046): the server then replays its first answer for
      // this delete instead of answering 404 to the retry of a delete it already did.
      final response = await _delete(
        Uri.parse(_url(kind, id)),
        idempotencyKey: stableIdempotencyKey('delete-${kind.name}', id),
      );
      if (response == null) return false;
      // 200: deleted. 404: never reached the server, or already gone. 422: an
      // id the server can never accept (a pre-UUID local memory id), so
      // retrying cannot help either.
      if (response.statusCode == 200 || response.statusCode == 404 || response.statusCode == 422) {
        await (_db.delete(_db.pendingDeletes)
              ..where((t) => t.targetTable.equals(kind.table) & t.recordId.equals(id)))
            .go();
        return true;
      }
      debugPrint("[RemoteDeletes] ${kind.table} $id not deleted yet: ${response.statusCode}");
      return false;
    } catch (e) {
      debugPrint("[RemoteDeletes] ${kind.table} $id not deleted yet: $e");
      return false;
    }
  }

  String _url(RemoteDeleteKind kind, String id) {
    final base = _ref.read(backendUrlProvider);
    final encoded = Uri.encodeComponent(id);
    return switch (kind) {
      RemoteDeleteKind.memory => '$base/api/v3/memory/$encoded',
      RemoteDeleteKind.chatSession => '$base/api/v3/sessions/$encoded',
    };
  }

  Future<http.Response?> _delete(Uri uri, {required String idempotencyKey}) async {
    final tokens = _ref.read(tokenManagerProvider);
    Future<http.Response?> once() async {
      final token = await tokens.getValidAccessToken();
      if (token == null) return null;
      final headers = <String, String>{
        "Authorization": "Bearer $token",
        idempotencyKeyHeader: idempotencyKey,
      };
      headers.addAll(await DeviceValidationService.getAttestationHeaders(backendUrl: _ref.read(backendUrlProvider)));
      final client = _http;
      final sent = client != null ? client.delete(uri, headers: headers) : http.delete(uri, headers: headers);
      return sent.timeout(const Duration(seconds: 20));
    }

    var response = await once();
    if (response?.statusCode == 401) {
      tokens.invalidateAccessToken();
      response = await once();
    }
    return response;
  }
}

final remoteDeletesProvider = Provider<RemoteDeletes>((ref) {
  return RemoteDeletes(ref.watch(databaseProvider), ref);
});
