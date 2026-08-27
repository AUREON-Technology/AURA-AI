import 'package:drift/drift.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:uuid/uuid.dart';

import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/features/chat/data/chat_repository.dart';

/// Persists live-voice turns into the existing Drift chat tables via the same
/// conversation id the kernel uses (`start.session_id`).
class LiveVoiceHistory {
  LiveVoiceHistory(this._ref);

  final Ref _ref;
  final _uuid = const Uuid();

  AppDatabase get _db => _ref.read(databaseProvider);

  Future<void> appendTurn({
    required String conversationId,
    required String role,
    required String content,
  }) async {
    if (content.trim().isEmpty) return;
    final id = _uuid.v4();
    await _db.into(_db.messages).insert(
          MessagesCompanion.insert(
            id: id,
            conversationId: conversationId,
            role: role,
            content: content,
            createdAt: DateTime.now(),
            updatedAt: Value(DateTime.now()),
          ),
        );
    _ref.read(activeConversationIdProvider.notifier).setSession(conversationId);
  }
}

final liveVoiceHistoryProvider = Provider<LiveVoiceHistory>((ref) {
  return LiveVoiceHistory(ref);
});
