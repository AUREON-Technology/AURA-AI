import 'package:flutter/foundation.dart';
import 'dart:convert';
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
  TextColumn get content => text().map(const EncryptedTextConverter())();
  TextColumn get modelUsed => text().nullable()();
  IntColumn get tokensUsed => integer().nullable()();
  DateTimeColumn get createdAt => dateTime()();
  // JSON-encoded List<String> of citation labels from the v3 kernel's
  // `citations`/`done` SSE frames (aura_core/kernel/kernel.py). Encrypted
  // like `content` since a citation label can reveal what a user searched
  // for.
  TextColumn get citationsJson => text().map(const EncryptedTextConverter()).nullable()();
  // JSON-encoded List<Map> of {name, arguments, settled} built from
  // `tool_calls` SSE frames as they arrive; `settled` flips to true when
  // the turn's `done` frame lands. Encrypted since tool arguments can
  // carry user-provided data.
  TextColumn get toolCallsJson => text().map(const EncryptedTextConverter()).nullable()();
  // Which persona (aura_core/prompts/library.py's AGENT_PROMPT_IDS)
  // actually answered this message, recorded at send time. Distinct from
  // `modelUsed` above, which this codebase already uses to store the
  // selected agent id rather than a model identifier -- left as-is to
  // avoid an unrelated cleanup; this column is the correctly-purposed one
  // for new code to read.
  TextColumn get agentId => text().nullable()();
  // Cursor-sync clock (AURA-014). Tombstones are not stored locally —
  // a pull row with `deleted_at` deletes this row, same as tasks.
  DateTimeColumn get updatedAt => dateTime().nullable()();
  // Server turn id from the stream `done` frame — required to POST feedback.
  TextColumn get turnId => text().nullable()();
  // Encrypted JSON: confidence, degraded, needs_confirmation, tainted,
  // guards_complete, revised, steps, correlation_id, usage.
  TextColumn get turnMetaJson => text().map(const EncryptedTextConverter()).nullable()();

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
  BoolColumn get isSynced => boolean().withDefault(const Constant(false))();
  DateTimeColumn get updatedAt => dateTime().nullable()();

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
  TextColumn get prompt => text().map(const EncryptedTextConverter())();
  TextColumn get response => text().map(const EncryptedTextConverter())();
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

/// V2 generalized mutation queue (Doc/V2_BLUEPRINT.md section 5). Replaces the
/// per-table `isSynced` boolean + `PendingDeletes` combo: every create/
/// update/delete of a syncable record enqueues one row here, encoding the
/// full mutation (table, op, id, JSON payload matching the backend's
/// Postgres column names, and the client's clock at mutation time for LWW).
/// `SyncClient.push()` drains this table in one batched call to
/// `POST /api/v3/sync` instead of five bespoke per-table HTTP loops.
class OutboxEntries extends Table {
  TextColumn get id => text()();
  TextColumn get targetTable => text()();
  TextColumn get recordId => text()();
  TextColumn get operation => text()(); // "upsert" | "delete"
  TextColumn get payloadJson => text().nullable()();
  DateTimeColumn get clientUpdatedAt => dateTime()();
  DateTimeColumn get createdAt => dateTime()();

  @override
  Set<Column> get primaryKey => {id};
}

/// Small key/value store for sync bookkeeping -- currently just the last
/// server cursor returned by `GET /api/v3/sync`, so the next pull only
/// requests what changed since then.
class SyncMetadata extends Table {
  TextColumn get key => text()();
  TextColumn get value => text()();

  @override
  Set<Column> get primaryKey => {key};
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

@DriftDatabase(tables: [Tasks, CalendarEvents, FinanceRecords, HealthRecords, Messages, AgentConfigurations, UserMemories, PendingDeletes, LearningLogs, OfflineModelWeights, TelemetryLogs, OutboxEntries, SyncMetadata])
class AppDatabase extends _$AppDatabase {
  AppDatabase() : super(_openConnection());

  /// In-memory / test executor. Production uses [_openConnection].
  AppDatabase.forTesting(super.e);

  @override
  int get schemaVersion => 10;

  Future<List<TelemetryLog>> getTelemetry() => select(telemetryLogs).get();
  Future<void> insertTelemetry(TelemetryLogsCompanion log) => into(telemetryLogs).insert(log);
  Stream<List<TelemetryLog>> watchTelemetry() => (select(telemetryLogs)
        ..orderBy([(t) => OrderingTerm(expression: t.createdAt, mode: OrderingMode.desc)])
        ..limit(50))
      .watch();

  /// Enqueues one mutation for the next `SyncClient.push()`. [payload] must
  /// use the backend's Postgres column names (see
  /// infrastructure/db/models.py) -- callers are responsible for encrypting
  /// any sensitive field with [EncryptionService.encryptText] before
  /// putting it in the map, since the server only ever stores ciphertext.
  Future<void> enqueueOutbox({
    required String table,
    required String recordId,
    required String operation,
    Map<String, dynamic>? payload,
  }) async {
    final now = DateTime.now();
    await into(outboxEntries).insert(
      OutboxEntriesCompanion.insert(
        id: '${table}_${recordId}_${now.microsecondsSinceEpoch}',
        targetTable: table,
        recordId: recordId,
        operation: operation,
        payloadJson: Value(payload != null ? jsonEncode(payload) : null),
        clientUpdatedAt: now,
        createdAt: now,
      ),
    );
  }

  Future<String?> getSyncCursor() async {
    final row = await (select(syncMetadata)..where((t) => t.key.equals('cursor'))).getSingleOrNull();
    return row?.value;
  }

  Future<void> setSyncCursor(String cursor) async {
    await into(syncMetadata).insertOnConflictUpdate(
      SyncMetadataCompanion.insert(key: 'cursor', value: cursor),
    );
  }

  /// Watermark for the Notification Center's unread badge -- reuses the
  /// same key/value table as the sync cursor rather than adding a
  /// dedicated table for one timestamp.
  Future<DateTime?> getNotificationsLastRead() async {
    final row = await (select(syncMetadata)..where((t) => t.key.equals('notifications_last_read'))).getSingleOrNull();
    return row != null ? DateTime.tryParse(row.value) : null;
  }

  Future<void> setNotificationsLastRead(DateTime time) async {
    await into(syncMetadata).insertOnConflictUpdate(
      SyncMetadataCompanion.insert(key: 'notifications_last_read', value: time.toIso8601String()),
    );
  }

  /// True when decrypted rows still start with `gcm:`, meaning the current
  /// key could not unwrap ciphertext (TypeConverter returns the raw blob).
  Future<bool> _hasUndecryptedCiphertext() async {
    try {
      final allMessages = await (select(messages)..limit(5)).get();
      if (allMessages.any((msg) => msg.content.startsWith('gcm:'))) return true;

      final allTasks = await (select(tasks)..limit(5)).get();
      if (allTasks.any((t) => t.title.startsWith('gcm:'))) return true;

      final allLogs = await (select(learningLogs)..limit(5)).get();
      if (allLogs.any((l) => l.prompt.startsWith('gcm:'))) return true;
    } catch (e) {
      debugPrint("[Database] Integrity check encountered error: $e");
    }
    return false;
  }

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
          if (from < 6) {
            await m.addColumn(userMemories, userMemories.isSynced);
            await m.addColumn(userMemories, userMemories.updatedAt);
          }
          if (from < 7) {
            await m.createTable(outboxEntries);
            await m.createTable(syncMetadata);
          }
          if (from < 8) {
            await m.addColumn(messages, messages.citationsJson);
            await m.addColumn(messages, messages.toolCallsJson);
            await m.addColumn(messages, messages.agentId);
          }
          if (from < 9) {
            await m.addColumn(messages, messages.updatedAt);
          }
          if (from < 10) {
            await m.addColumn(messages, messages.turnId);
            await m.addColumn(messages, messages.turnMetaJson);
          }
        },
        beforeOpen: (details) async {
          // Perform a decryption integrity check (detect key mismatch/loss from backup restore)
          var needsReset = await _hasUndecryptedCiphertext();
          if (needsReset && await EncryptionService.tryRestoreBackupKey()) {
            needsReset = await _hasUndecryptedCiphertext();
            if (!needsReset) {
              await EncryptionService.persistCurrentKey();
              debugPrint("[Database] Decryption restored via encryption-key backup. No wipe.");
            } else {
              EncryptionService.revertBackupKeyRestore();
              debugPrint("[Database] Backup encryption key also failed to decrypt rows.");
            }
          }

          if (needsReset) {
            debugPrint(
              "[Database] Decryption key mismatch or key loss detected. "
              "Clearing unreadable content tables; preserving outboxEntries, "
              "syncMetadata, and pendingDeletes so unsynced mutations are not destroyed.",
            );
            await transaction(() async {
              await delete(tasks).go();
              await delete(calendarEvents).go();
              await delete(financeRecords).go();
              await delete(healthRecords).go();
              await delete(messages).go();
              await delete(agentConfigurations).go();
              await delete(userMemories).go();
              await delete(learningLogs).go();
              await delete(offlineModelWeights).go();
              await delete(telemetryLogs).go();
            });
            debugPrint("[Database] Unreadable content tables cleared. Outbox and sync cursor kept.");
          }

          if (details.versionBefore != null && details.versionBefore! < 6) {
            // Re-encrypt existing cleartext rows transparently
            try {
              final allMessages = await select(messages).get();
              for (final msg in allMessages) {
                await update(messages).replace(msg);
              }
              final allLogs = await select(learningLogs).get();
              for (final log in allLogs) {
                await update(learningLogs).replace(log);
              }
            } catch (e) {
              debugPrint("[DatabaseMigration] Error encrypting existing tables: $e");
            }
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
