import 'dart:async';
import 'package:drift/drift.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:cloud_firestore/cloud_firestore.dart';
import 'package:firebase_core/firebase_core.dart';
import '../../core/database/database.dart';
import '../../core/database/database_provider.dart';
import '../../features/auth/presentation/auth_provider.dart';

enum SyncState {
  synced,
  syncing,
  offline,
  error,
}

class SyncEngine extends Notifier<SyncState> {
  late AppDatabase _db;
  FirebaseFirestore? _firestore;
  Timer? _syncTimer;

  @override
  SyncState build() {
    _db = ref.watch(databaseProvider);
    
    if (Firebase.apps.isNotEmpty) {
      try {
        _firestore = FirebaseFirestore.instance;
        
        // Start periodic background sync every 60 seconds
        _syncTimer = Timer.periodic(const Duration(seconds: 60), (_) {
          synchronize();
        });

        ref.onDispose(() {
          _syncTimer?.cancel();
        });

        // Run initial sync
        Future.microtask(() => synchronize());
        
        return SyncState.synced;
      } catch (e) {
        debugPrint("[SyncEngine] Failed to initialize Firestore: $e");
      }
    } else {
      debugPrint("[SyncEngine] Firebase is not initialized. Sync running in offline mode.");
    }

    return SyncState.offline;
  }

  /// Helper to map a table name to the correct nested user collection reference.
  CollectionReference<Map<String, dynamic>> _getCollection(String tableName, String userId) {
    String sub;
    switch (tableName) {
      case 'tasks':
        sub = 'tasks';
        break;
      case 'calendar_events':
        sub = 'calendar';
        break;
      case 'finance_records':
        sub = 'finance';
        break;
      case 'health_records':
        sub = 'health';
        break;
      case 'user_memories':
      case 'memories':
        sub = 'memories';
        break;
      default:
        sub = tableName;
    }
    return _firestore!.collection('users').doc(userId).collection(sub);
  }

  Future<void> synchronize() async {
    if (state == SyncState.syncing) return;

    final authState = ref.read(authProvider);
    final userId = authState.value?.id;
    if (userId == null || userId.isEmpty) {
      debugPrint("[SyncEngine] Sync suspended: user not authenticated.");
      state = SyncState.offline;
      return;
    }

    state = SyncState.syncing;
    debugPrint("[SyncEngine] Starting synchronization process for user: $userId...");

    try {
      // 1. Check network connectivity (ping subcollection)
      final isOnline = await _checkConnectivity(userId);
      if (!isOnline) {
        debugPrint("[SyncEngine] Device is offline. Sync suspended.");
        state = SyncState.offline;
        return;
      }

      // 2. Sync Deletions first
      await _syncPendingDeletions(userId);

      // 3. Sync Tables
      await _syncTasksTable(userId);
      await _syncCalendarEventsTable(userId);
      await _syncFinanceRecordsTable(userId);
      await _syncHealthRecordsTable(userId);
      await _syncUserMemoriesTable(userId);

      debugPrint("[SyncEngine] Sync completed successfully!");
      state = SyncState.synced;
    } catch (e) {
      debugPrint("[SyncEngine] Sync failed with error: $e");
      state = SyncState.error;
    }
  }

  Future<bool> _checkConnectivity(String userId) async {
    if (_firestore == null) return false;
    try {
      // Small timeout connection test to user's nested tasks collection
      await _getCollection('tasks', userId).limit(1).get().timeout(const Duration(seconds: 4));
      return true;
    } catch (e) {
      return false;
    }
  }

  Future<void> _syncPendingDeletions(String userId) async {
    final pendingDeletes = await _db.select(_db.pendingDeletes).get();
    if (pendingDeletes.isEmpty) return;

    debugPrint("[SyncEngine] Syncing ${pendingDeletes.length} pending deletions...");
    for (final del in pendingDeletes) {
      try {
        await _getCollection(del.targetTable, userId).doc(del.recordId).delete();
        // Remove delete log locally
        await (_db.delete(_db.pendingDeletes)
              ..where((t) => t.recordId.equals(del.recordId) & t.targetTable.equals(del.targetTable)))
            .go();
      } catch (e) {
        debugPrint("[SyncEngine] Failed to sync delete for ${del.recordId} in ${del.targetTable}: $e");
      }
    }
  }

  // ----------------------------------------------------
  // Tasks Sync Logic
  // ----------------------------------------------------
  Future<void> _syncTasksTable(String userId) async {
    final localTasks = await _db.select(_db.tasks).get();
    final localMap = {for (var t in localTasks) t.id: t};

    // Fetch remote tasks
    final remoteSnapshot = await _getCollection('tasks', userId).get();
    final remoteMap = <String, Map<String, dynamic>>{};
    for (var doc in remoteSnapshot.docs) {
      remoteMap[doc.id] = doc.data();
    }

    // 1. Process local updates and inserts
    for (final local in localTasks) {
      final remote = remoteMap[local.id];

      if (remote == null) {
        if (!local.isSynced) {
          // Upload local insert
          await _getCollection('tasks', userId).doc(local.id).set(local.toJson());
          await _markTaskSynced(local.id);
        }
      } else {
        if (!local.isSynced) {
          // Compare timestamps (LWW)
          final remoteUpdateStr = remote['updatedAt'] as String?;
          final remoteUpdatedAt = remoteUpdateStr != null ? DateTime.parse(remoteUpdateStr) : null;
          final localUpdatedAt = local.updatedAt;

          if (localUpdatedAt != null && (remoteUpdatedAt == null || localUpdatedAt.isAfter(remoteUpdatedAt))) {
            // Local is newer: upload
            await _getCollection('tasks', userId).doc(local.id).set(local.toJson());
            await _markTaskSynced(local.id);
          } else {
            // Remote is newer or equal: download
            final remoteTask = Task.fromJson(remote);
            await _db.update(_db.tasks).replace(remoteTask.copyWith(isSynced: true));
          }
        }
      }
    }

    // 2. Process remote inserts and deletions
    for (final remoteEntry in remoteMap.entries) {
      final remoteId = remoteEntry.key;
      final remoteData = remoteEntry.value;
      final local = localMap[remoteId];

      if (local == null) {
        // Check if deleted locally
        final isDeleted = await _isRecordDeletedLocally(remoteId, 'tasks');
        if (!isDeleted) {
          // Remote insert
          final remoteTask = Task.fromJson(remoteData);
          await _db.into(_db.tasks).insertOnConflictUpdate(remoteTask.copyWith(isSynced: true));
        }
      } else if (local.isSynced) {
        // Local is synced, check if remote has updates
        final remoteUpdateStr = remoteData['updatedAt'] as String?;
        final remoteUpdatedAt = remoteUpdateStr != null ? DateTime.parse(remoteUpdateStr) : null;
        final localUpdatedAt = local.updatedAt;

        if (remoteUpdatedAt != null && (localUpdatedAt == null || remoteUpdatedAt.isAfter(localUpdatedAt))) {
          final remoteTask = Task.fromJson(remoteData);
          await _db.update(_db.tasks).replace(remoteTask.copyWith(isSynced: true));
        }
      }
    }

    // 3. Process remote deletes (if synced locally but missing remotely)
    for (final local in localTasks) {
      if (local.isSynced && !remoteMap.containsKey(local.id)) {
        // Delete locally
        await (_db.delete(_db.tasks)..where((t) => t.id.equals(local.id))).go();
      }
    }
  }

  // ----------------------------------------------------
  // Calendar Events Sync Logic
  // ----------------------------------------------------
  Future<void> _syncCalendarEventsTable(String userId) async {
    final localEvents = await _db.select(_db.calendarEvents).get();
    final localMap = {for (var e in localEvents) e.id: e};

    final remoteSnapshot = await _getCollection('calendar_events', userId).get();
    final remoteMap = <String, Map<String, dynamic>>{};
    for (var doc in remoteSnapshot.docs) {
      remoteMap[doc.id] = doc.data();
    }

    for (final local in localEvents) {
      final remote = remoteMap[local.id];

      if (remote == null) {
        if (!local.isSynced) {
          await _getCollection('calendar_events', userId).doc(local.id).set(local.toJson());
          await _markEventSynced(local.id);
        }
      } else {
        if (!local.isSynced) {
          final remoteUpdateStr = remote['updatedAt'] as String?;
          final remoteUpdatedAt = remoteUpdateStr != null ? DateTime.parse(remoteUpdateStr) : null;
          final localUpdatedAt = local.updatedAt;

          if (localUpdatedAt != null && (remoteUpdatedAt == null || localUpdatedAt.isAfter(remoteUpdatedAt))) {
            await _getCollection('calendar_events', userId).doc(local.id).set(local.toJson());
            await _markEventSynced(local.id);
          } else {
            final remoteEvent = CalendarEvent.fromJson(remote);
            await _db.update(_db.calendarEvents).replace(remoteEvent.copyWith(isSynced: true));
          }
        }
      }
    }

    for (final remoteEntry in remoteMap.entries) {
      final remoteId = remoteEntry.key;
      final remoteData = remoteEntry.value;
      final local = localMap[remoteId];

      if (local == null) {
        final isDeleted = await _isRecordDeletedLocally(remoteId, 'calendar_events');
        if (!isDeleted) {
          final remoteEvent = CalendarEvent.fromJson(remoteData);
          await _db.into(_db.calendarEvents).insertOnConflictUpdate(remoteEvent.copyWith(isSynced: true));
        }
      } else if (local.isSynced) {
        final remoteUpdateStr = remoteData['updatedAt'] as String?;
        final remoteUpdatedAt = remoteUpdateStr != null ? DateTime.parse(remoteUpdateStr) : null;
        final localUpdatedAt = local.updatedAt;

        if (remoteUpdatedAt != null && (localUpdatedAt == null || remoteUpdatedAt.isAfter(localUpdatedAt))) {
          final remoteEvent = CalendarEvent.fromJson(remoteData);
          await _db.update(_db.calendarEvents).replace(remoteEvent.copyWith(isSynced: true));
        }
      }
    }

    for (final local in localEvents) {
      if (local.isSynced && !remoteMap.containsKey(local.id)) {
        await (_db.delete(_db.calendarEvents)..where((t) => t.id.equals(local.id))).go();
      }
    }
  }

  // ----------------------------------------------------
  // Finance Records Sync Logic
  // ----------------------------------------------------
  Future<void> _syncFinanceRecordsTable(String userId) async {
    final localRecords = await _db.select(_db.financeRecords).get();
    final localMap = {for (var r in localRecords) r.id: r};

    final remoteSnapshot = await _getCollection('finance_records', userId).get();
    final remoteMap = <String, Map<String, dynamic>>{};
    for (var doc in remoteSnapshot.docs) {
      remoteMap[doc.id] = doc.data();
    }

    for (final local in localRecords) {
      final remote = remoteMap[local.id];

      if (remote == null) {
        if (!local.isSynced) {
          await _getCollection('finance_records', userId).doc(local.id).set(local.toJson());
          await _markFinanceSynced(local.id);
        }
      } else {
        if (!local.isSynced) {
          final remoteUpdateStr = remote['updatedAt'] as String?;
          final remoteUpdatedAt = remoteUpdateStr != null ? DateTime.parse(remoteUpdateStr) : null;
          final localUpdatedAt = local.updatedAt;

          if (localUpdatedAt != null && (remoteUpdatedAt == null || localUpdatedAt.isAfter(remoteUpdatedAt))) {
            await _getCollection('finance_records', userId).doc(local.id).set(local.toJson());
            await _markFinanceSynced(local.id);
          } else {
            final remoteRecord = FinanceRecord.fromJson(remote);
            await _db.update(_db.financeRecords).replace(remoteRecord.copyWith(isSynced: true));
          }
        }
      }
    }

    for (final remoteEntry in remoteMap.entries) {
      final remoteId = remoteEntry.key;
      final remoteData = remoteEntry.value;
      final local = localMap[remoteId];

      if (local == null) {
        final isDeleted = await _isRecordDeletedLocally(remoteId, 'finance_records');
        if (!isDeleted) {
          final remoteRecord = FinanceRecord.fromJson(remoteData);
          await _db.into(_db.financeRecords).insertOnConflictUpdate(remoteRecord.copyWith(isSynced: true));
        }
      } else if (local.isSynced) {
        final remoteUpdateStr = remoteData['updatedAt'] as String?;
        final remoteUpdatedAt = remoteUpdateStr != null ? DateTime.parse(remoteUpdateStr) : null;
        final localUpdatedAt = local.updatedAt;

        if (remoteUpdatedAt != null && (localUpdatedAt == null || remoteUpdatedAt.isAfter(localUpdatedAt))) {
          final remoteRecord = FinanceRecord.fromJson(remoteData);
          await _db.update(_db.financeRecords).replace(remoteRecord.copyWith(isSynced: true));
        }
      }
    }

    for (final local in localRecords) {
      if (local.isSynced && !remoteMap.containsKey(local.id)) {
        await (_db.delete(_db.financeRecords)..where((t) => t.id.equals(local.id))).go();
      }
    }
  }

  // ----------------------------------------------------
  // Health Records Sync Logic
  // ----------------------------------------------------
  Future<void> _syncHealthRecordsTable(String userId) async {
    final localRecords = await _db.select(_db.healthRecords).get();
    final localMap = {for (var r in localRecords) r.id: r};

    final remoteSnapshot = await _getCollection('health_records', userId).get();
    final remoteMap = <String, Map<String, dynamic>>{};
    for (var doc in remoteSnapshot.docs) {
      remoteMap[doc.id] = doc.data();
    }

    for (final local in localRecords) {
      final remote = remoteMap[local.id];

      if (remote == null) {
        if (!local.isSynced) {
          await _getCollection('health_records', userId).doc(local.id).set(local.toJson());
          await _markHealthSynced(local.id);
        }
      } else {
        if (!local.isSynced) {
          final remoteUpdateStr = remote['updatedAt'] as String?;
          final remoteUpdatedAt = remoteUpdateStr != null ? DateTime.parse(remoteUpdateStr) : null;
          final localUpdatedAt = local.updatedAt;

          if (localUpdatedAt != null && (remoteUpdatedAt == null || localUpdatedAt.isAfter(remoteUpdatedAt))) {
            await _getCollection('health_records', userId).doc(local.id).set(local.toJson());
            await _markHealthSynced(local.id);
          } else {
            final remoteRecord = HealthRecord.fromJson(remote);
            await _db.update(_db.healthRecords).replace(remoteRecord.copyWith(isSynced: true));
          }
        }
      }
    }

    for (final remoteEntry in remoteMap.entries) {
      final remoteId = remoteEntry.key;
      final remoteData = remoteEntry.value;
      final local = localMap[remoteId];

      if (local == null) {
        final remoteRecord = HealthRecord.fromJson(remoteData);
        await _db.into(_db.healthRecords).insertOnConflictUpdate(remoteRecord.copyWith(isSynced: true));
      } else if (local.isSynced) {
        final remoteUpdateStr = remoteData['updatedAt'] as String?;
        final remoteUpdatedAt = remoteUpdateStr != null ? DateTime.parse(remoteUpdateStr) : null;
        final localUpdatedAt = local.updatedAt;

        if (remoteUpdatedAt != null && (localUpdatedAt == null || remoteUpdatedAt.isAfter(localUpdatedAt))) {
          final remoteRecord = HealthRecord.fromJson(remoteData);
          await _db.update(_db.healthRecords).replace(remoteRecord.copyWith(isSynced: true));
        }
      }
    }
  }

  // ----------------------------------------------------
  // User Memories Sync Logic
  // ----------------------------------------------------
  Future<void> _syncUserMemoriesTable(String userId) async {
    final localMemories = await _db.select(_db.userMemories).get();
    final localMap = {for (var m in localMemories) m.id: m};

    final remoteSnapshot = await _getCollection('user_memories', userId).get();
    final remoteMap = <String, Map<String, dynamic>>{};
    for (var doc in remoteSnapshot.docs) {
      remoteMap[doc.id] = doc.data();
    }

    for (final local in localMemories) {
      final remote = remoteMap[local.id];

      if (remote == null) {
        if (!local.isSynced) {
          // Upload local insert
          await _getCollection('user_memories', userId).doc(local.id).set({
            'id': local.id,
            'fact': local.fact, // Already GCM encrypted
            'createdAt': local.createdAt.toIso8601String(),
            'updatedAt': local.updatedAt?.toIso8601String() ?? local.createdAt.toIso8601String(),
          });
          await _markMemorySynced(local.id);
        }
      } else {
        if (!local.isSynced) {
          final remoteUpdateStr = remote['updatedAt'] as String?;
          final remoteUpdatedAt = remoteUpdateStr != null ? DateTime.parse(remoteUpdateStr) : null;
          final localUpdatedAt = local.updatedAt ?? local.createdAt;

          if (remoteUpdatedAt == null || localUpdatedAt.isAfter(remoteUpdatedAt)) {
            // Local is newer: upload
            await _getCollection('user_memories', userId).doc(local.id).set({
              'id': local.id,
              'fact': local.fact,
              'createdAt': local.createdAt.toIso8601String(),
              'updatedAt': localUpdatedAt.toIso8601String(),
            });
            await _markMemorySynced(local.id);
          } else {
            // Remote is newer: download
            final fact = remote['fact'] as String;
            final createdAt = DateTime.parse(remote['createdAt'] as String);
            final updatedAt = DateTime.parse(remote['updatedAt'] as String);
            await _db.update(_db.userMemories).replace(UserMemory(
              id: local.id,
              fact: fact,
              createdAt: createdAt,
              isSynced: true,
              updatedAt: updatedAt,
            ));
          }
        }
      }
    }

    for (final remoteEntry in remoteMap.entries) {
      final remoteId = remoteEntry.key;
      final remoteData = remoteEntry.value;
      final local = localMap[remoteId];

      if (local == null) {
        final isDeleted = await _isRecordDeletedLocally(remoteId, 'user_memories');
        if (!isDeleted) {
          final fact = remoteData['fact'] as String;
          final createdAt = DateTime.parse(remoteData['createdAt'] as String);
          final updatedAt = DateTime.parse(remoteData['updatedAt'] as String);
          await _db.into(_db.userMemories).insertOnConflictUpdate(UserMemory(
            id: remoteId,
            fact: fact,
            createdAt: createdAt,
            isSynced: true,
            updatedAt: updatedAt,
          ));
        }
      } else if (local.isSynced) {
        final remoteUpdateStr = remoteData['updatedAt'] as String?;
        final remoteUpdatedAt = remoteUpdateStr != null ? DateTime.parse(remoteUpdateStr) : null;
        final localUpdatedAt = local.updatedAt ?? local.createdAt;

        if (remoteUpdatedAt != null && remoteUpdatedAt.isAfter(localUpdatedAt)) {
          final fact = remoteData['fact'] as String;
          final createdAt = DateTime.parse(remoteData['createdAt'] as String);
          final updatedAt = DateTime.parse(remoteData['updatedAt'] as String);
          await _db.update(_db.userMemories).replace(UserMemory(
            id: local.id,
            fact: fact,
            createdAt: createdAt,
            isSynced: true,
            updatedAt: updatedAt,
          ));
        }
      }
    }

    // Process remote deletes
    for (final local in localMemories) {
      if (local.isSynced && !remoteMap.containsKey(local.id)) {
        await (_db.delete(_db.userMemories)..where((t) => t.id.equals(local.id))).go();
      }
    }
  }

  // ----------------------------------------------------
  // Sync Status Helpers
  // ----------------------------------------------------
  Future<void> _markTaskSynced(String id) async {
    await (_db.update(_db.tasks)..where((t) => t.id.equals(id))).write(
      const TasksCompanion(isSynced: Value(true)),
    );
  }

  Future<void> _markEventSynced(String id) async {
    await (_db.update(_db.calendarEvents)..where((t) => t.id.equals(id))).write(
      const CalendarEventsCompanion(isSynced: Value(true)),
    );
  }

  Future<void> _markFinanceSynced(String id) async {
    await (_db.update(_db.financeRecords)..where((t) => t.id.equals(id))).write(
      const FinanceRecordsCompanion(isSynced: Value(true)),
    );
  }

  Future<void> _markHealthSynced(String id) async {
    await (_db.update(_db.healthRecords)..where((t) => t.id.equals(id))).write(
      const HealthRecordsCompanion(isSynced: Value(true)),
    );
  }

  Future<void> _markMemorySynced(String id) async {
    await (_db.update(_db.userMemories)..where((t) => t.id.equals(id))).write(
      UserMemoriesCompanion(
        isSynced: const Value(true),
        updatedAt: Value(DateTime.now()),
      ),
    );
  }

  Future<bool> _isRecordDeletedLocally(String id, String table) async {
    final del = await (_db.select(_db.pendingDeletes)
          ..where((t) => t.recordId.equals(id) & t.targetTable.equals(table)))
        .getSingleOrNull();
    return del != null;
  }
}

final syncEngineProvider = NotifierProvider<SyncEngine, SyncState>(() {
  return SyncEngine();
});
