// The `/api/v3/sync` push payload contract, client half (audit F-13).
//
// `test/fixtures/sync_payload_contract.json` is generated from the backend
// models and asserted equal to them by
// `aura_backend/tests/unit/test_sync_payload_contract.py`. Here every
// `SyncPayloads` builder is checked against it: no key the server would
// silently ignore, and every key the server cannot insert a row without.
// Memories broke exactly this way (`content_enc` sent, `fact_enc NOT NULL`
// expected), so every saved memory was a row the server could never accept.

import 'dart:convert';
import 'dart:io';

import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/security/encryption_service.dart';
import 'package:aura_client/core/sync/sync_payloads.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late Map<String, dynamic> contract;

  setUpAll(() async {
    await EncryptionService.init();
    contract = jsonDecode(File('test/fixtures/sync_payload_contract.json').readAsStringSync())
        as Map<String, dynamic>;
  });

  final now = DateTime(2026, 10, 28, 9);

  // Every builder, with every optional field populated so that a key sent
  // only sometimes is still checked.
  final payloads = <String, Map<String, dynamic> Function()>{
    'tasks': () => SyncPayloads.task(Task(
          id: 't',
          title: 'title',
          description: 'description',
          priority: 'high',
          dueDate: now,
          isCompleted: true,
          googleTaskId: 'g',
          category: 'personal',
          createdAt: now,
          isSynced: false,
          updatedAt: now,
        )),
    'calendar_events': () => SyncPayloads.calendarEvent(CalendarEvent(
          id: 'c',
          title: 'title',
          description: 'description',
          startTime: now,
          endTime: now,
          googleEventId: 'g',
          isAllDay: false,
          createdAt: now,
          isSynced: false,
          updatedAt: now,
        )),
    'finance_records': () => SyncPayloads.financeRecord(FinanceRecord(
          id: 'f',
          type: 'expense',
          amount: 12.5,
          category: 'food',
          description: 'description',
          date: now,
          createdAt: now,
          isSynced: false,
          updatedAt: now,
        )),
    'health_records': () => SyncPayloads.healthRecord(HealthRecord(
          id: 'h',
          recordDate: now,
          weight: 70,
          waterIntakeMl: 500,
          sleepMinutes: 420,
          caloriesConsumed: 2000,
          exerciseMinutes: 30,
          createdAt: now,
          isSynced: false,
          updatedAt: now,
        )),
    'messages': () => SyncPayloads.message(Message(
          id: 'm',
          conversationId: 'conv',
          role: 'user',
          content: 'hello',
          modelUsed: 'model',
          tokensUsed: 3,
          createdAt: now,
          citationsJson: '[]',
          toolCallsJson: '[]',
          agentId: 'orchestrator',
          updatedAt: now,
        )),
    'projects': () => SyncPayloads.project(Project(
          id: 'p',
          title: 'title',
          description: 'description',
          status: 'active',
          targetDate: now,
          createdAt: now,
          isSynced: false,
          updatedAt: now,
        )),
    'goals': () => SyncPayloads.goal(Goal(
          id: 'g',
          title: 'title',
          description: 'description',
          projectId: 'p',
          category: 'health',
          status: 'active',
          priority: 'medium',
          targetDate: now,
          progressPct: 10,
          createdAt: now,
          isSynced: false,
          updatedAt: now,
        )),
    'memories': () => SyncPayloads.userMemory('I live in Cairo'),
  };

  test('every syncable table has a payload builder', () {
    expect(payloads.keys.toSet(), contract.keys.toSet());
  });

  for (final entry in payloads.entries) {
    test('${entry.key} payload matches the server contract', () {
      final spec = contract[entry.key] as Map<String, dynamic>;
      final columns = (spec['columns'] as List).cast<String>().toSet();
      final required = (spec['required'] as List).cast<String>().toSet();
      final keys = entry.value().keys.toSet();
      expect(keys.difference(columns), isEmpty, reason: 'keys the server would ignore');
      expect(required.difference(keys), isEmpty, reason: 'columns the server cannot insert without');
    });
  }

  test('a memory travels as fact_enc ciphertext', () {
    final payload = SyncPayloads.userMemory('I live in Cairo');
    expect(payload.keys, ['fact_enc']);
    expect(EncryptionService.decryptText(payload['fact_enc'] as String), 'I live in Cairo');
  });
}
