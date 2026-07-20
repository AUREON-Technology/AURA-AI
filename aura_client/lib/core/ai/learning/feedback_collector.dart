import 'dart:convert';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:drift/drift.dart';
import 'package:uuid/uuid.dart';
import '../../database/database.dart';
import '../../database/database_provider.dart';

class FeedbackCollector {
  final AppDatabase _db;
  final _uuid = const Uuid();

  FeedbackCollector(this._db);

  /// Saves a learning signal into the local database
  Future<void> logFeedback({
    required String prompt,
    required String response,
    required int rating,
    String? correction,
    String? modelUsed,
  }) async {
    await _db.into(_db.learningLogs).insert(
      LearningLogsCompanion.insert(
        id: _uuid.v4(),
        prompt: prompt,
        response: response,
        rating: rating,
        correction: Value(correction),
        modelUsed: Value(modelUsed),
        createdAt: DateTime.now(),
      ),
    );
  }

  /// Lists all logged training feedback records
  Future<List<LearningLog>> getLogs() async {
    return _db.select(_db.learningLogs).get();
  }

  /// Generates a JSON dataset representing local fine-tuning training pairs
  Future<String> exportDatasetAsJson() async {
    final logs = await getLogs();
    final list = logs.map((log) => {
      "prompt": log.prompt,
      "response": log.response,
      "rating": log.rating,
      "correction": log.correction,
      "model": log.modelUsed,
      "timestamp": log.createdAt.toIso8601String(),
    }).toList();
    return jsonEncode(list);
  }

  /// Deletes a specific learning feedback log
  Future<void> deleteLog(String logId) async {
    await (_db.delete(_db.learningLogs)..where((l) => l.id.equals(logId))).go();
  }
}

final feedbackCollectorProvider = Provider<FeedbackCollector>((ref) {
  final db = ref.watch(databaseProvider);
  return FeedbackCollector(db);
});
