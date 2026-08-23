import 'package:drift/drift.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:uuid/uuid.dart';

import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/network/aura_api_client.dart';

class FeedbackRepository {
  final AppDatabase _db;
  final AuraApiClient _api;
  final _uuid = const Uuid();

  FeedbackRepository(this._db, this._api);

  Stream<List<LearningLog>> watchLocal() {
    return (_db.select(_db.learningLogs)
          ..orderBy([
            (t) => OrderingTerm(expression: t.createdAt, mode: OrderingMode.desc),
          ]))
        .watch();
  }

  Future<void> submit({
    required String turnId,
    required int rating,
    String? correction,
    String? prompt,
    String? response,
  }) async {
    await _api.json(
      method: 'POST',
      path: '/api/v3/feedback',
      body: {
        'turn_id': turnId,
        'rating': rating,
        if (correction != null && correction.isNotEmpty) 'correction': correction,
        if (prompt != null && prompt.isNotEmpty) 'prompt': prompt,
        if (response != null && response.isNotEmpty) 'response': response,
      },
    );
    await _db.into(_db.learningLogs).insert(
      LearningLogsCompanion.insert(
        id: _uuid.v4(),
        prompt: prompt ?? '',
        response: response ?? '',
        rating: rating,
        correction: Value(correction),
        modelUsed: const Value('server'),
        createdAt: DateTime.now(),
      ),
    );
  }
}

final feedbackRepositoryProvider = Provider<FeedbackRepository>((ref) {
  return FeedbackRepository(
    ref.watch(databaseProvider),
    ref.watch(auraApiClientProvider),
  );
});

final localFeedbackStreamProvider =
    StreamProvider.autoDispose<List<LearningLog>>((ref) {
  return ref.watch(feedbackRepositoryProvider).watchLocal();
});
