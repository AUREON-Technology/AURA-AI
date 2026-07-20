import 'package:drift/drift.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:uuid/uuid.dart';
import '../../../core/database/database.dart';
import '../../../core/database/database_provider.dart';
import '../../../core/sync/sync_payloads.dart';

class HealthRepository {
  final AppDatabase _db;
  final _uuid = const Uuid();

  HealthRepository(this._db);

  Future<void> _enqueueUpsert(String id) async {
    final row = await (_db.select(_db.healthRecords)..where((t) => t.id.equals(id))).getSingleOrNull();
    if (row == null) return;
    await _db.enqueueOutbox(table: 'health_records', recordId: id, operation: 'upsert', payload: SyncPayloads.healthRecord(row));
  }

  // Normalize date to starting second of the day
  DateTime _normalizeDate(DateTime date) {
    return DateTime(date.year, date.month, date.day);
  }

  // Watch health record for a specific day
  Stream<HealthRecord?> watchDailyRecord(DateTime date) {
    final target = _normalizeDate(date);
    return (_db.select(_db.healthRecords)..where((t) => t.recordDate.equals(target)))
        .watchSingleOrNull();
  }

  // Watch the last 7 days of records for trend lines
  Stream<List<HealthRecord>> watchWeeklyRecords() {
    final now = DateTime.now();
    final sevenDaysAgo = _normalizeDate(now.subtract(const Duration(days: 7)));
    return (_db.select(_db.healthRecords)
          ..where((t) => t.recordDate.isBiggerOrEqualValue(sevenDaysAgo))
          ..orderBy([
            (t) => OrderingTerm(expression: t.recordDate, mode: OrderingMode.asc),
          ]))
        .watch();
  }

  // Helper to retrieve or initialize today's record
  Future<HealthRecord> _getOrInitializeRecord(DateTime date) async {
    final target = _normalizeDate(date);
    final existing = await (_db.select(_db.healthRecords)..where((t) => t.recordDate.equals(target))).getSingleOrNull();
    
    if (existing != null) {
      return existing;
    }

    final newRecord = HealthRecord(
      id: _uuid.v4(),
      recordDate: target,
      weight: null,
      waterIntakeMl: 0,
      sleepMinutes: 0,
      caloriesConsumed: 0,
      exerciseMinutes: 0,
      createdAt: DateTime.now(),
      isSynced: false,
      updatedAt: DateTime.now(),
    );

    await _db.into(_db.healthRecords).insert(newRecord);
    return newRecord;
  }

  // Increment water intake (ml)
  Future<void> logWater(DateTime date, int ml) async {
    final record = await _getOrInitializeRecord(date);
    await (_db.update(_db.healthRecords)..where((t) => t.id.equals(record.id))).write(
      HealthRecordsCompanion(
        waterIntakeMl: Value(record.waterIntakeMl + ml),
        isSynced: const Value(false),
        updatedAt: Value(DateTime.now()),
      ),
    );
    await _enqueueUpsert(record.id);
  }

  // Log sleep (minutes)
  Future<void> logSleep(DateTime date, int minutes) async {
    final record = await _getOrInitializeRecord(date);
    await (_db.update(_db.healthRecords)..where((t) => t.id.equals(record.id))).write(
      HealthRecordsCompanion(
        sleepMinutes: Value(record.sleepMinutes + minutes),
        isSynced: const Value(false),
        updatedAt: Value(DateTime.now()),
      ),
    );
    await _enqueueUpsert(record.id);
  }

  // Log exercise duration (minutes)
  Future<void> logExercise(DateTime date, int minutes) async {
    final record = await _getOrInitializeRecord(date);
    await (_db.update(_db.healthRecords)..where((t) => t.id.equals(record.id))).write(
      HealthRecordsCompanion(
        exerciseMinutes: Value(record.exerciseMinutes + minutes),
        isSynced: const Value(false),
        updatedAt: Value(DateTime.now()),
      ),
    );
    await _enqueueUpsert(record.id);
  }

  // Log calories consumed (kcal)
  Future<void> logCalories(DateTime date, int kcal) async {
    final record = await _getOrInitializeRecord(date);
    await (_db.update(_db.healthRecords)..where((t) => t.id.equals(record.id))).write(
      HealthRecordsCompanion(
        caloriesConsumed: Value(record.caloriesConsumed + kcal),
        isSynced: const Value(false),
        updatedAt: Value(DateTime.now()),
      ),
    );
    await _enqueueUpsert(record.id);
  }

  // Log weight update (kg / lbs)
  Future<void> logWeight(DateTime date, double weight) async {
    final record = await _getOrInitializeRecord(date);
    await (_db.update(_db.healthRecords)..where((t) => t.id.equals(record.id))).write(
      HealthRecordsCompanion(
        weight: Value(weight),
        isSynced: const Value(false),
        updatedAt: Value(DateTime.now()),
      ),
    );
    await _enqueueUpsert(record.id);
  }
}

final healthRepositoryProvider = Provider<HealthRepository>((ref) {
  final db = ref.watch(databaseProvider);
  return HealthRepository(db);
});

// Watch current day's record stream (defaults to DateTime.now())
final dailyHealthStreamProvider = StreamProvider.family.autoDispose<HealthRecord?, DateTime>((ref, date) {
  final repository = ref.watch(healthRepositoryProvider);
  return repository.watchDailyRecord(date);
});

// Watch 7-day trend records stream
final weeklyHealthStreamProvider = StreamProvider.autoDispose<List<HealthRecord>>((ref) {
  final repository = ref.watch(healthRepositoryProvider);
  return repository.watchWeeklyRecords();
});
