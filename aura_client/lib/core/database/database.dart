import 'dart:io';
import 'package:drift/drift.dart';
import 'package:drift/native.dart';
import 'package:path_provider/path_provider.dart';
import 'package:path/path.dart' as p;

import 'package:aura_client/core/security/encryption_service.dart';

part 'database.g.dart';

// ----------------------------------------------------
// Type Converters
// ----------------------------------------------------

class EncryptedTextConverter extends TypeConverter<String, String> {
  const EncryptedTextConverter();

  @override
  String fromSql(String fromDb) {
    return EncryptionService.decryptText(fromDb);
  }

  @override
  String toSql(String value) {
    return EncryptionService.encryptText(value);
  }
}

// ----------------------------------------------------
// DB Tables
// ----------------------------------------------------

class Tasks extends Table {
  TextColumn get id => text()();
  TextColumn get title => text().withLength(min: 1, max: 200).map(const EncryptedTextConverter())();
  TextColumn get description => text().map(const EncryptedTextConverter()).nullable()();
  TextColumn get priority => text()(); // low, medium, high
  DateTimeColumn get dueDate => dateTime().nullable()();
  BoolColumn get isCompleted => boolean()();
  TextColumn get googleTaskId => text().nullable()();
  TextColumn get category => text()(); // personal, academic, client, project
  DateTimeColumn get createdAt => dateTime()();
  BoolColumn get isSynced => boolean().withDefault(const Constant(false))();
  DateTimeColumn get updatedAt => dateTime().nullable()();

  @override
  Set<Column> get primaryKey => {id};
}

class CalendarEvents extends Table {
  TextColumn get id => text()();
  TextColumn get title => text().map(const EncryptedTextConverter())();
  TextColumn get description => text().map(const EncryptedTextConverter()).nullable()();
  DateTimeColumn get startTime => dateTime()();
  DateTimeColumn get endTime => dateTime()();
  TextColumn get googleEventId => text().nullable()();
  BoolColumn get isAllDay => boolean()();
  DateTimeColumn get createdAt => dateTime()();
  BoolColumn get isSynced => boolean().withDefault(const Constant(false))();
  DateTimeColumn get updatedAt => dateTime().nullable()();

  @override
  Set<Column> get primaryKey => {id};
}

class FinanceRecords extends Table {
  TextColumn get id => text()();
  TextColumn get type => text()(); // income, expense
  RealColumn get amount => real()();
  TextColumn get category => text()(); // e.g. food, studies, freelance, etc.
  TextColumn get description => text().map(const EncryptedTextConverter()).nullable()();
  DateTimeColumn get date => dateTime()();
  DateTimeColumn get createdAt => dateTime()();
  BoolColumn get isSynced => boolean().withDefault(const Constant(false))();
  DateTimeColumn get updatedAt => dateTime().nullable()();

  @override
  Set<Column> get primaryKey => {id};
}

class HealthRecords extends Table {
  TextColumn get id => text()();
  DateTimeColumn get recordDate => dateTime()();
  RealColumn get weight => real().nullable()();
  IntColumn get waterIntakeMl => integer()();
  IntColumn get sleepMinutes => integer()();
  IntColumn get caloriesConsumed => integer()();
  IntColumn get exerciseMinutes => integer()();
  DateTimeColumn get createdAt => dateTime()();
  BoolColumn get isSynced => boolean().withDefault(const Constant(false))();
  DateTimeColumn get updatedAt => dateTime().nullable()();

  @override
  Set<Column> get primaryKey => {id};
}

class Messages extends Table {
  TextColumn get id => text()();
  TextColumn get conversationId => text()();
  TextColumn get role => text()(); // user, assistant, system
  TextColumn get content => text()();
  TextColumn get modelUsed => text().nullable()();
  IntColumn get tokensUsed => integer().nullable()();
  DateTimeColumn get createdAt => dateTime()();

  @override
  Set<Column> get primaryKey => {id};
}

class AgentConfigurations extends Table {
  TextColumn get agentId => text()(); // orchestrator, task_planner, finance_advisor, health_companion, academic_coach
  TextColumn get systemInstruction => text()();
  DateTimeColumn get updatedAt => dateTime()();

  @override
  Set<Column> get primaryKey => {agentId};
}

class UserMemories extends Table {
  TextColumn get id => text()();
  TextColumn get fact => text().map(const EncryptedTextConverter())();
  DateTimeColumn get createdAt => dateTime()();

  @override
  Set<Column> get primaryKey => {id};
}

class PendingDeletes extends Table {
  TextColumn get recordId => text()();
  TextColumn get targetTable => text()();
  DateTimeColumn get deletedAt => dateTime()();

  @override
  Set<Column> get primaryKey => {recordId, targetTable};
}

class LearningLogs extends Table {
  TextColumn get id => text()();
  TextColumn get prompt => text()();
  TextColumn get response => text()();
  IntColumn get rating => integer()(); // e.g. 1 to 5, or thumbs up/down
  TextColumn get correction => text().nullable()();
  TextColumn get modelUsed => text().nullable()();
  DateTimeColumn get createdAt => dateTime()();

  @override
  Set<Column> get primaryKey => {id};
}

class OfflineModelWeights extends Table {
  TextColumn get id => text()(); // e.g. phi-3, qwen-7b, llama-8b
  TextColumn get modelName => text()();
  TextColumn get sizeBytes => text()();
  TextColumn get localPath => text().nullable()();
  BoolColumn get isDownloaded => boolean().withDefault(const Constant(false))();
  BoolColumn get isActive => boolean().withDefault(const Constant(false))();
  DateTimeColumn get updatedAt => dateTime()();

  @override
  Set<Column> get primaryKey => {id};
}

class TelemetryLogs extends Table {
  TextColumn get id => text()();
  TextColumn get eventType => text()(); // request, failover, error, rating
  RealColumn get latencyMs => real()();
  TextColumn get agentUsed => text()();
  TextColumn get modelUsed => text()();
  BoolColumn get isOffline => boolean()();
  TextColumn get errorMessage => text().nullable()();
  IntColumn get feedbackRating => integer().nullable()();
  DateTimeColumn get createdAt => dateTime()();

  @override
  Set<Column> get primaryKey => {id};
}

// ----------------------------------------------------
// Database Connection & Class
// ----------------------------------------------------

@DriftDatabase(tables: [Tasks, CalendarEvents, FinanceRecords, HealthRecords, Messages, AgentConfigurations, UserMemories, PendingDeletes, LearningLogs, OfflineModelWeights, TelemetryLogs])
class AppDatabase extends _$AppDatabase {
  AppDatabase() : super(_openConnection());

  @override
  int get schemaVersion => 5;

  Future<List<TelemetryLog>> getTelemetry() => select(telemetryLogs).get();
  Future<void> insertTelemetry(TelemetryLogsCompanion log) => into(telemetryLogs).insert(log);

  @override
  MigrationStrategy get migration => MigrationStrategy(
        onCreate: (m) async {
          await m.createAll();
        },
        onUpgrade: (m, from, to) async {
          if (from < 2) {
            await m.createTable(agentConfigurations);
            await m.createTable(userMemories);
          }
          if (from < 3) {
            await m.addColumn(tasks, tasks.isSynced);
            await m.addColumn(tasks, tasks.updatedAt);
            
            await m.addColumn(calendarEvents, calendarEvents.isSynced);
            await m.addColumn(calendarEvents, calendarEvents.updatedAt);
            
            await m.addColumn(financeRecords, financeRecords.isSynced);
            await m.addColumn(financeRecords, financeRecords.updatedAt);
            
            await m.addColumn(healthRecords, healthRecords.isSynced);
            await m.addColumn(healthRecords, healthRecords.updatedAt);
            
            await m.createTable(pendingDeletes);
          }
          if (from < 4) {
            await m.createTable(learningLogs);
            await m.createTable(offlineModelWeights);
          }
          if (from < 5) {
            await m.createTable(telemetryLogs);
          }
        },
      );
}

LazyDatabase _openConnection() {
  return LazyDatabase(() async {
    final dbFolder = await getApplicationDocumentsDirectory();
    final file = File(p.join(dbFolder.path, 'aura_db.sqlite'));
    return NativeDatabase.createInBackground(file);
  });
}
