// Offline queue for agent task submissions (inventory row 91, ADR-041).
//
// The real outbox, repository and AuraApiClient run against a MockClient that behaves like the
// gateway's idempotency contract: the first request with a `client_request_id` creates a task
// (202), a repeat answers 200 `idempotent_replay` with the same task, and the tests count the
// tasks the "server" ended up with -- a double submit would show as two.

import 'dart:convert';

import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/network/aura_api_client.dart';
import 'package:aura_client/features/auth/presentation/auth_provider.dart';
import 'package:aura_client/features/engineering/data/agent_task_outbox.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';

class _Token extends TokenManager {
  _Token(super.ref);
  @override
  Future<String?> getValidAccessToken() async => 'jwt';
}

class _SignedIn extends AuthNotifier {
  _SignedIn(this.userId);
  final String userId;

  @override
  Future<UserProfile?> build() async => UserProfile(id: userId, email: '$userId@example.com', displayName: userId);

  @override
  Future<String?> getIdToken() async => null;
}

/// A gateway stand-in. [mode] decides how the next request is answered; every request is kept.
class _Gateway {
  /// ok | offline | lostAnswer | error500 | rate429 | refuse422 | refuse403 | conflict409 |
  /// inProgress409
  String mode = 'ok';
  final List<http.Request> requests = [];

  /// client_request_id -> task id: the tasks the server really created.
  final Map<String, String> tasks = {};
  final List<String> order = [];

  http.Client client() => MockClient((request) async {
        requests.add(request);
        final body = jsonDecode(request.body) as Map<String, dynamic>;
        final key = body['client_request_id'] as String;
        switch (mode) {
          case 'offline':
            throw http.ClientException('Connection refused', request.url);
          case 'error500':
            return http.Response('{"error":"internal_error","message":"boom"}', 500);
          case 'rate429':
            return http.Response(
              '{"error":"task_queue_full","message":"busy","retryable":true}',
              429,
              headers: {'retry-after': '120'},
            );
          case 'refuse422':
            return http.Response('{"error":"validation_error","message":"goal is not valid"}', 422);
          case 'refuse403':
            return http.Response('{"error":"consent_required","message":"Engineer consent needed"}', 403);
          case 'conflict409':
            return http.Response('{"error":"idempotency_key_reused","message":"different request"}', 409);
          case 'inProgress409':
            return http.Response(
              '{"error":"idempotency_in_progress","message":"still being accepted","retryable":true}',
              409,
            );
        }
        final replay = tasks.containsKey(key);
        if (!replay) {
          tasks[key] = 'task-${tasks.length + 1}';
          order.add(body['goal'] as String);
        }
        if (mode == 'lostAnswer') {
          // The server accepted it; the response never reached the phone.
          throw http.ClientException('connection reset', request.url);
        }
        return http.Response(
          jsonEncode({
            'task': {
              'id': tasks[key],
              'goal': body['goal'],
              'persona': 'orchestrator',
              'status': 'queued',
              'created_at': '2026-11-23T10:00:00Z',
            },
            'queue': {'position': 1},
            'idempotent_replay': replay,
          }),
          replay ? 200 : 202,
        );
      });

  List<String> get sentKeys =>
      requests.map((r) => (jsonDecode(r.body) as Map<String, dynamic>)['client_request_id'] as String).toList();
}

class _Harness {
  _Harness(this.gateway, {String user = 'user-1', DateTime Function()? clock}) {
    container = ProviderContainer(
      retry: (_, _) => null,
      overrides: [
        authProvider.overrideWith(() => _SignedIn(user)),
        tokenManagerProvider.overrideWith((ref) => _Token(ref)),
        auraApiClientProvider.overrideWith((ref) => AuraApiClient(ref, httpClient: gateway.client())),
        if (clock != null) agentTaskOutboxClockProvider.overrideWithValue(clock),
      ],
    );
  }

  final _Gateway gateway;
  late final ProviderContainer container;

  AgentTaskOutbox get outbox => container.read(agentTaskOutboxProvider.notifier);
  AgentTaskOutboxState get state => container.read(agentTaskOutboxProvider);

  Future<void> start() async {
    await container.read(authProvider.future);
    container.read(agentTaskOutboxProvider);
    await outbox.ready;
  }

  void dispose() => container.dispose();
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() => SharedPreferences.setMockInitialValues({}));

  test('backoff doubles from 5 s and stops at 5 minutes', () {
    expect(agentTaskBackoff(0), Duration.zero);
    expect(agentTaskBackoff(1), const Duration(seconds: 5));
    expect(agentTaskBackoff(2), const Duration(seconds: 10));
    expect(agentTaskBackoff(3), const Duration(seconds: 20));
    expect(agentTaskBackoff(7), const Duration(seconds: 300));
    expect(agentTaskBackoff(40), const Duration(seconds: 300));
  });

  test('online: the task is sent once, with the same id in the body and the Idempotency-Key header', () async {
    final gw = _Gateway();
    final h = _Harness(gw);
    addTearDown(h.dispose);
    await h.start();

    final entry = await h.outbox.enqueue('  fix the login bug  ');

    expect(entry, isNotNull);
    expect(gw.requests, hasLength(1));
    final request = gw.requests.single;
    expect(request.url.path, '/api/v3/agents/tasks');
    expect(request.method, 'POST');
    final body = jsonDecode(request.body) as Map<String, dynamic>;
    expect(body['goal'], 'fix the login bug', reason: 'trimmed');
    expect(body['client_request_id'], entry!.clientRequestId);
    expect(request.headers['Idempotency-Key'], entry.clientRequestId,
        reason: 'the server requires the header and the body field to agree');
    expect(h.state.entries, isEmpty, reason: 'accepted, so nothing is waiting');
    expect(h.state.lastAccepted!.task.id, 'task-1');
    expect(h.state.lastAccepted!.idempotentReplay, isFalse);
    expect(gw.tasks, hasLength(1));
  });

  test('offline: kept as waiting, saved on the device, and replayed with the SAME id once back online', () async {
    final gw = _Gateway()..mode = 'offline';
    final h = _Harness(gw);
    addTearDown(h.dispose);
    await h.start();

    final entry = await h.outbox.enqueue('refactor the parser');

    expect(h.state.entries, hasLength(1));
    expect(h.state.entries.single.isFailed, isFalse);
    expect(h.state.entries.single.attempts, 1);
    expect(h.state.entries.single.nextAttemptAt, isNotNull);
    expect(gw.tasks, isEmpty);
    final prefs = await SharedPreferences.getInstance();
    final saved = jsonDecode(prefs.getString('aura.agent_task_outbox.v1')!) as List;
    expect(saved.single['client_request_id'], entry!.clientRequestId, reason: 'written before the send');

    gw.mode = 'ok';
    await h.outbox.flush(force: true);

    expect(h.state.entries, isEmpty);
    expect(gw.tasks, hasLength(1));
    expect(gw.sentKeys, [entry.clientRequestId, entry.clientRequestId],
        reason: 'the replay reuses the id, which is what makes a double submit impossible');
    final after = jsonDecode(prefs.getString('aura.agent_task_outbox.v1')!) as List;
    expect(after, isEmpty);
  });

  test('the answer was lost after the server accepted it: the replay is a 200 and nothing runs twice', () async {
    final gw = _Gateway()..mode = 'lostAnswer';
    final h = _Harness(gw);
    addTearDown(h.dispose);
    await h.start();

    await h.outbox.enqueue('run the tests');
    expect(gw.tasks, hasLength(1), reason: 'the server did create it');
    expect(h.state.entries, hasLength(1), reason: 'but the phone never heard, so it still waits');

    gw.mode = 'ok';
    await h.outbox.flush(force: true);

    expect(gw.tasks, hasLength(1), reason: 'still one task: the replay was recognised');
    expect(gw.order, ['run the tests']);
    expect(h.state.entries, isEmpty);
    expect(h.state.lastAccepted!.idempotentReplay, isTrue);
    expect(h.state.lastAccepted!.task.id, 'task-1');
  });

  test('order is kept: while the first waits, the second is not sent ahead of it', () async {
    final gw = _Gateway()..mode = 'offline';
    final h = _Harness(gw);
    addTearDown(h.dispose);
    await h.start();

    await h.outbox.enqueue('first');
    await h.outbox.enqueue('second');
    expect(h.state.entries.map((e) => e.goal), ['first', 'second']);
    expect(gw.requests, hasLength(2), reason: 'each enqueue tried only the head of the queue');
    expect(gw.sentKeys.toSet(), hasLength(1), reason: 'and the head both times');

    gw.mode = 'ok';
    await h.outbox.flush(force: true);

    expect(gw.order, ['first', 'second']);
    expect(h.state.entries, isEmpty);
  });

  test('a 5xx keeps the task and backs off; a 429 honours Retry-After', () async {
    var now = DateTime.utc(2026, 11, 23, 10);
    final gw = _Gateway()..mode = 'error500';
    final h = _Harness(gw, clock: () => now);
    addTearDown(h.dispose);
    await h.start();

    await h.outbox.enqueue('deploy the fix');
    var entry = h.state.entries.single;
    expect(entry.isFailed, isFalse);
    expect(entry.nextAttemptAt, now.add(const Duration(seconds: 5)));

    // Not due yet: a periodic flush sends nothing.
    final before = gw.requests.length;
    await h.outbox.flush();
    expect(gw.requests, hasLength(before));

    now = now.add(const Duration(seconds: 6));
    gw.mode = 'rate429';
    await h.outbox.flush();
    expect(gw.requests, hasLength(before + 1));
    entry = h.state.entries.single;
    expect(entry.isFailed, isFalse, reason: '429 is retried, not dropped');
    expect(entry.nextAttemptAt, now.add(const Duration(seconds: 120)), reason: 'Retry-After beats the backoff');

    now = now.add(const Duration(seconds: 121));
    gw.mode = 'ok';
    await h.outbox.flush();
    expect(h.state.entries, isEmpty);
    expect(gw.tasks, hasLength(1));
  });

  test('a 409 that only says the first attempt is still being accepted is retried', () async {
    final gw = _Gateway()..mode = 'inProgress409';
    final h = _Harness(gw);
    addTearDown(h.dispose);
    await h.start();

    await h.outbox.enqueue('index the repo');

    expect(h.state.entries.single.isFailed, isFalse);
    gw.mode = 'ok';
    await h.outbox.flush(force: true);
    expect(h.state.entries, isEmpty);
  });

  for (final refusal in const {
    'refuse422': 'goal is not valid',
    'refuse403': 'Engineer consent needed',
    'conflict409': 'different request',
  }.entries) {
    test('${refusal.key}: refused for good -> kept visible as not sent, with the reason, never retried', () async {
      final gw = _Gateway()..mode = refusal.key;
      final h = _Harness(gw);
      addTearDown(h.dispose);
      await h.start();

      await h.outbox.enqueue('do something');

      final entry = h.state.entries.single;
      expect(entry.isFailed, isTrue);
      expect(entry.lastError, refusal.value);
      expect(gw.requests, hasLength(1));

      gw.mode = 'ok';
      await h.outbox.flush(force: true);
      await h.outbox.flush(force: true);
      expect(gw.requests, hasLength(1), reason: 'a refused entry is not sent again');
      expect(h.state.entries.single.isFailed, isTrue, reason: 'still there until the person dismisses it');
      expect(gw.tasks, isEmpty);

      await h.outbox.dismiss(entry.clientRequestId);
      expect(h.state.entries, isEmpty);
    });
  }

  test('409 key reused is recorded with its error kind', () async {
    final gw = _Gateway()..mode = 'conflict409';
    final h = _Harness(gw);
    addTearDown(h.dispose);
    await h.start();
    await h.outbox.enqueue('x');
    expect(h.state.entries.single.errorKind, 'idempotency_key_reused');
  });

  test('a refused entry does not hold up the ones behind it', () async {
    final gw = _Gateway()..mode = 'refuse422';
    final h = _Harness(gw);
    addTearDown(h.dispose);
    await h.start();
    await h.outbox.enqueue('bad one');
    expect(h.state.entries.single.isFailed, isTrue);

    gw.mode = 'ok';
    await h.outbox.enqueue('good one');

    expect(gw.order, ['good one']);
    expect(h.state.entries.map((e) => (e.goal, e.isFailed)), [('bad one', true)]);
  });

  test('resend as new uses a new id: the old key would only return the old refusal', () async {
    final gw = _Gateway()..mode = 'refuse422';
    final h = _Harness(gw);
    addTearDown(h.dispose);
    await h.start();
    final first = await h.outbox.enqueue('fixed wording');

    gw.mode = 'ok';
    await h.outbox.resendAsNew(first!.clientRequestId);

    expect(h.state.entries, isEmpty);
    expect(gw.tasks.keys.single, isNot(first.clientRequestId));
    expect(gw.order, ['fixed wording']);
  });

  test('the queue survives a restart, and keeps the same id', () async {
    final gw = _Gateway()..mode = 'offline';
    final first = _Harness(gw);
    await first.start();
    final entry = await first.outbox.enqueue('survive the restart');
    first.dispose();

    final second = _Harness(gw);
    addTearDown(second.dispose);
    await second.start();
    expect(second.state.entries.single.clientRequestId, entry!.clientRequestId);
    expect(second.state.entries.single.goal, 'survive the restart');

    gw.mode = 'ok';
    await second.outbox.flush(force: true);
    expect(gw.sentKeys.last, entry.clientRequestId);
    expect(gw.tasks, hasLength(1));
  });

  test("another account's queued tasks are neither shown nor sent", () async {
    final gw = _Gateway()..mode = 'offline';
    final alice = _Harness(gw, user: 'alice');
    await alice.start();
    await alice.outbox.enqueue("alice's task");
    alice.dispose();

    final bob = _Harness(gw, user: 'bob');
    addTearDown(bob.dispose);
    await bob.start();
    gw.mode = 'ok';
    await bob.outbox.flush(force: true);

    expect(bob.state.forOwner('bob'), isEmpty);
    expect(gw.tasks, isEmpty, reason: "bob's session must never send alice's task");
  });

  test('clear (sign-out) forgets everything, in memory and on disk', () async {
    final gw = _Gateway()..mode = 'offline';
    final h = _Harness(gw);
    addTearDown(h.dispose);
    await h.start();
    await h.outbox.enqueue('left behind');

    await h.outbox.clear();

    expect(h.state.entries, isEmpty);
    expect((await SharedPreferences.getInstance()).getString('aura.agent_task_outbox.v1'), isNull);
  });

  test('empty goals and a full queue are refused without a request', () async {
    final gw = _Gateway()..mode = 'offline';
    final h = _Harness(gw);
    addTearDown(h.dispose);
    await h.start();

    expect(await h.outbox.enqueue('   '), isNull);
    expect(gw.requests, isEmpty);

    for (var i = 0; i < kMaxQueuedAgentTasks; i++) {
      expect(await h.outbox.enqueue('task $i'), isNotNull);
    }
    expect(await h.outbox.enqueue('one too many'), isNull);
    expect(h.state.entries, hasLength(kMaxQueuedAgentTasks));
  });
}
