import 'dart:convert';

import 'package:drift/drift.dart';
import 'package:flutter/foundation.dart';

import '../database/database.dart';
import '../sync/sync_payloads.dart';
import 'encryption_service.dart';

/// What one [DataKeyMigration.run] did.
class DataKeyMigrationReport {
  /// Local values re-encrypted from the device key to the account data key.
  int rewrapped = 0;

  /// Rows queued for upload, re-encrypted, through the normal outbox.
  int queued = 0;

  /// Values holding another device's ciphertext: re-wrapped, not uploaded.
  int foreign = 0;

  /// `gcm:` values the device key cannot open (genuine local key loss).
  int unreadable = 0;

  /// Outbox payloads re-encrypted.
  int outboxRewrapped = 0;

  /// Raw `gcm:` values still left after the run.
  int remaining = 0;

  bool legacyRetired = false;

  @override
  String toString() =>
      'DataKeyMigrationReport(rewrapped: $rewrapped, queued: $queued, foreign: $foreign, '
      'unreadable: $unreadable, outbox: $outboxRewrapped, remaining: $remaining, '
      'legacyRetired: $legacyRetired)';
}

/// Moves this device from its own legacy key to the account data key
/// (ADR-032). Runs inside a sync, after the pull, when both keys are held.
///
/// For every encrypted column, each raw `gcm:` value is decrypted with the
/// legacy key, re-encrypted with the data key, and written back with a
/// compare-and-swap on the old value. A synced row whose text was fully
/// recovered is queued for upload *in the same transaction*, so:
///
/// * **resumable** -- killed partway, every finished row has both its rewrite
///   and its upload queued, every unfinished row still holds `gcm:` and is
///   found by the next run;
/// * **idempotent** -- a second run finds nothing to do;
/// * **another device's rows are never uploaded.** A value whose inner text is
///   still ciphertext this device cannot read (another device's `gcm:`) is
///   re-wrapped under the data key so the legacy key can be retired, but not
///   pushed: that would overwrite the other device's row with text nobody can
///   read. When that device migrates, the next pull replaces it here.
///
/// When no raw `gcm:` value is left -- in the tables or in queued outbox
/// payloads -- the legacy key is retired.
class DataKeyMigration {
  final AppDatabase _db;

  DataKeyMigration(this._db);

  /// Every encrypted column, by SQL table and column name.
  static const Map<String, List<String>> encryptedColumns = {
    'tasks': ['title', 'description'],
    'calendar_events': ['title', 'description'],
    'finance_records': ['description'],
    'messages': ['content', 'citations_json', 'tool_calls_json', 'turn_meta_json'],
    'user_memories': ['fact'],
    'projects': ['title', 'description'],
    'goals': ['title', 'description'],
    'learning_logs': ['prompt', 'response'],
  };

  static const int _batch = 200;

  /// [stopAfterRows] simulates the app being killed after that many rows.
  Future<DataKeyMigrationReport> run({@visibleForTesting int? stopAfterRows}) async {
    final report = DataKeyMigrationReport();
    if (!EncryptionService.hasDataKey || !EncryptionService.hasLegacyKey) return report;
    var budget = stopAfterRows;

    for (final entry in encryptedColumns.entries) {
      final table = entry.key;
      final columns = entry.value;
      final anyLegacy = columns.map((c) => "$c LIKE 'gcm:%'").join(' OR ');
      var lastId = '';
      while (true) {
        final rows = await _db.customSelect(
          'SELECT id, ${columns.join(', ')} FROM $table '
          'WHERE id > ? AND ($anyLegacy) ORDER BY id LIMIT $_batch',
          variables: [Variable.withString(lastId)],
        ).get();
        if (rows.isEmpty) break;
        for (final row in rows) {
          if (budget != null && budget-- <= 0) return report;
          lastId = row.read<String>('id');
          await _migrateRow(table, columns, row, report);
        }
      }
    }

    await _migrateOutbox(report);
    report.remaining = await remainingLegacyValues();
    if (report.remaining == 0) {
      await EncryptionService.retireLegacyKey();
      report.legacyRetired = true;
    }
    debugPrint('[DataKeyMigration] $report');
    return report;
  }

  Future<void> _migrateRow(
    String table, List<String> columns, QueryRow row, DataKeyMigrationReport report,
  ) async {
    final id = row.read<String>('id');
    final updates = <String, String>{};
    final originals = <String, String>{};
    var recovered = true;
    for (final column in columns) {
      final raw = row.readNullable<String>(column);
      if (raw == null || !raw.startsWith('gcm:')) continue;
      final plain = EncryptionService.tryDecryptLegacy(raw);
      if (plain == null) {
        report.unreadable++;
        recovered = false;
        continue;
      }
      var inner = plain;
      if (EncryptionService.isEnvelope(inner)) {
        // Pulled before this device had the data key: the server's gcm2 (open
        // it now), or another device's gcm: (cannot -- keep it as it is).
        final opened = EncryptionService.tryDecrypt(inner);
        if (opened != null) {
          inner = opened;
        } else {
          report.foreign++;
          recovered = false;
        }
      }
      updates[column] = EncryptionService.encryptText(inner);
      originals[column] = raw;
    }
    if (updates.isEmpty) return;

    await _db.transaction(() async {
      final sets = updates.keys.map((c) => '$c = ?').join(', ');
      final guards = originals.keys.map((c) => '$c = ?').join(' AND ');
      final changed = await _db.customUpdate(
        'UPDATE $table SET $sets WHERE id = ? AND $guards',
        variables: [
          for (final v in updates.values) Variable.withString(v),
          Variable.withString(id),
          for (final v in originals.values) Variable.withString(v),
        ],
        updateKind: UpdateKind.update,
      );
      if (changed == 0) return; // rewritten meanwhile: the next run looks again
      report.rewrapped += updates.length;
      if (recovered && await _queueUpload(table, id)) report.queued++;
    });
  }

  /// Queues the row's re-encrypted payload, as its repository would. Only
  /// tables the sync protocol carries as rows; memories travel through the
  /// memory API and learning logs never leave the device.
  Future<bool> _queueUpload(String table, String id) async {
    Map<String, dynamic>? payload;
    switch (table) {
      case 'tasks':
        final row = await (_db.select(_db.tasks)..where((t) => t.id.equals(id))).getSingleOrNull();
        if (row != null) payload = SyncPayloads.task(row);
      case 'calendar_events':
        final row = await (_db.select(_db.calendarEvents)..where((t) => t.id.equals(id))).getSingleOrNull();
        if (row != null) payload = SyncPayloads.calendarEvent(row);
      case 'finance_records':
        final row = await (_db.select(_db.financeRecords)..where((t) => t.id.equals(id))).getSingleOrNull();
        if (row != null) payload = SyncPayloads.financeRecord(row);
      case 'messages':
        final row = await (_db.select(_db.messages)..where((t) => t.id.equals(id))).getSingleOrNull();
        if (row != null) payload = SyncPayloads.message(row);
      case 'projects':
        final row = await (_db.select(_db.projects)..where((t) => t.id.equals(id))).getSingleOrNull();
        if (row != null) payload = SyncPayloads.project(row);
      case 'goals':
        final row = await (_db.select(_db.goals)..where((t) => t.id.equals(id))).getSingleOrNull();
        if (row != null) payload = SyncPayloads.goal(row);
    }
    if (payload == null) return false;
    await _db.enqueueOutbox(table: table, recordId: id, operation: 'upsert', payload: payload);
    return true;
  }

  /// Re-encrypts `*_enc` values still sealed with the legacy key in queued
  /// mutations, so retiring that key cannot strand a pending upload.
  Future<void> _migrateOutbox(DataKeyMigrationReport report) async {
    final entries = await (_db.select(_db.outboxEntries)
          ..where((t) => t.payloadJson.like('%"gcm:%')))
        .get();
    for (final entry in entries) {
      final payload = jsonDecode(entry.payloadJson!) as Map<String, dynamic>;
      var touched = false;
      for (final key in payload.keys.toList()) {
        final value = payload[key];
        if (!key.endsWith('_enc') || value is! String || !value.startsWith('gcm:')) continue;
        final plain = EncryptionService.tryDecryptLegacy(value);
        if (plain == null) {
          report.unreadable++;
          continue;
        }
        payload[key] = EncryptionService.encryptText(
          EncryptionService.isEnvelope(plain) ? (EncryptionService.tryDecrypt(plain) ?? plain) : plain,
        );
        touched = true;
      }
      if (!touched) continue;
      await (_db.update(_db.outboxEntries)
            ..where((t) => t.id.equals(entry.id) & t.payloadJson.equals(entry.payloadJson!)))
          .write(OutboxEntriesCompanion(payloadJson: Value(jsonEncode(payload))));
      report.outboxRewrapped++;
    }
  }

  /// Raw `gcm:` values left anywhere local: the condition for retiring the
  /// legacy key.
  Future<int> remainingLegacyValues() async {
    var total = 0;
    for (final entry in encryptedColumns.entries) {
      final anyLegacy = entry.value.map((c) => "$c LIKE 'gcm:%'").join(' OR ');
      final row = await _db.customSelect(
        'SELECT COUNT(*) AS n FROM ${entry.key} WHERE $anyLegacy',
      ).getSingle();
      total += row.read<int>('n');
    }
    final outbox = await _db.customSelect(
      "SELECT COUNT(*) AS n FROM outbox_entries WHERE payload_json LIKE '%\"gcm:%'",
    ).getSingle();
    return total + outbox.read<int>('n');
  }
}
