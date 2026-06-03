import 'dart:io';
import 'package:drift/drift.dart';
import 'package:drift/native.dart';
import 'package:path_provider/path_provider.dart';
import 'package:path/path.dart' as p;

part 'database.g.dart';

// ----------------------------------------------------
// DB Tables
// ----------------------------------------------------

class Tasks extends Table {
  TextColumn get id => text()();
  TextColumn get title => text().withLength(min: 1, max: 200)();
  TextColumn get description => text().nullable()();
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
  TextColumn get title => text()();
  TextColumn get description => text().nullable()();
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
  TextColumn get description => text().nullable()();
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
  TextColumn get fact => text()();
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

// ----------------------------------------------------
// Database Connection & Class
// ----------------------------------------------------

@DriftDatabase(tables: [Tasks, CalendarEvents, FinanceRecords, HealthRecords, Messages, AgentConfigurations, UserMemories, PendingDeletes])
class AppDatabase extends _$AppDatabase {
  AppDatabase() : super(_openConnection());

  @override
  int get schemaVersion => 3;

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
