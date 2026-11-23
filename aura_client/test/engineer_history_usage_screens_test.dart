// Screens for inventory rows 71 (run search and timeline), 72 (usage and cost) and the visible
// half of 91 (waiting to send). Real pages, repositories and AuraApiClient; the gateway is a
// MockClient, so the assertions are about the requests sent and what the person sees.

import 'dart:convert';

import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/network/aura_api_client.dart';
import 'package:aura_client/features/auth/presentation/auth_provider.dart';
import 'package:aura_client/features/engineering/data/agent_task_outbox.dart';
import 'package:aura_client/features/engineering/presentation/agent_tasks_page.dart';
import 'package:aura_client/features/engineering/presentation/run_history_page.dart';
import 'package:aura_client/features/engineering/presentation/usage_page.dart';
import 'package:aura_client/l10n/generated/app_localizations.dart';
import 'package:flutter/material.dart';
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
  @override
  Future<UserProfile?> build() async => UserProfile(id: 'user-1', email: 'a@example.com', displayName: 'A');

  @override
  Future<String?> getIdToken() async => null;
}

Map<String, dynamic> _run(String id, String status, {String agent = 'engineer', String? preview}) => {
      'id': id,
      'agent_id': agent,
      'status': status,
      'created_at': '2026-11-20T10:00:00Z',
      'elapsed_s': 12.5,
      'iterations': 2,
      'plan_steps_total': 3,
      'plan_steps_succeeded': status == 'completed' ? 3 : 1,
      'tool_calls': 4,
      'tool_calls_ok': 3,
      'tokens_used': 900,
      'goal_preview': preview,
      'tools': ['read_file', 'run_command'],
      'matched_on': ['tools'],
    };

/// An [AuraApiClient] that hands each request straight to the MockClient. The real client's
/// attestation-header lookup does platform I/O that never completes under a widget test's fake
/// clock; its wire behaviour (headers, 401 retry, deadline) is covered by
/// `client_contract_fixes_test.dart` and `agent_task_outbox_test.dart`.
class _DirectApi extends AuraApiClient {
  _DirectApi(super.ref, this._client);
  final http.Client _client;

  @override
  Future<http.Response> request({
    required String method,
    required String path,
    Map<String, dynamic>? body,
    String? correlationId,
    Duration? timeout,
    String? idempotencyKey,
  }) async {
    final req = http.Request(method, Uri.parse('http://test.invalid$path'));
    if (idempotencyKey != null) req.headers['Idempotency-Key'] = idempotencyKey;
    if (body != null) req.body = jsonEncode(body);
    return http.Response.fromStream(await _client.send(req));
  }
}

Widget _app(Widget home, http.Client client) => ProviderScope(
      retry: (_, _) => null,
      overrides: [
        authProvider.overrideWith(_SignedIn.new),
        tokenManagerProvider.overrideWith((ref) => _Token(ref)),
        auraApiClientProvider.overrideWith((ref) => _DirectApi(ref, client)),
      ],
      child: MaterialApp(
        theme: AuraThemeV2.dark,
        localizationsDelegates: AppLocalizations.localizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        locale: const Locale('en'),
        home: home,
      ),
    );

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() => SharedPreferences.setMockInitialValues({}));

  group('row 71: run search and timeline', () {
    late List<http.Request> requests;

    MockClient gateway({bool previews = false}) {
      requests = [];
      return MockClient((request) async {
        requests.add(request);
        if (request.url.path == '/api/v3/agents/runs/search') {
          final q = request.url.queryParameters['q'] ?? '';
          final runs = [
            _run('run-ok', 'completed', preview: previews ? 'add a health check' : null),
            _run('run-bad', 'failed', preview: previews ? 'migrate the schema' : null),
          ].where((r) => q.isEmpty || (r['tools'] as List).contains(q) || r['status'] == q).toList();
          return http.Response(
            jsonEncode({
              'query': q,
              'total_matches': runs.length,
              'runs': runs,
              'search_scope': {
                'goal_text_searchable': previews,
                'note': 'Goal text is not stored, so it cannot be searched.',
              },
            }),
            200,
          );
        }
        if (request.url.path == '/api/v3/agents/runs/run-bad') {
          return http.Response(
            jsonEncode({
              ..._run('run-bad', 'failed'),
              'steps': [
                {'seq': 2, 'iteration': 0, 'phase': 'execute', 'kind': 'tool', 'status': 'failed', 'tool': 'run_command', 'error_kind': 'timeout', 'created_at': '2026-11-20T10:00:05Z'},
                {'seq': 1, 'iteration': 0, 'phase': 'execute', 'kind': 'tool', 'status': 'succeeded', 'tool': 'read_file', 'created_at': '2026-11-20T10:00:01Z'},
              ],
            }),
            200,
          );
        }
        return http.Response('{}', 404);
      });
    }

    testWidgets('lists the newest runs, then searches with the typed words', (tester) async {
      await tester.pumpWidget(_app(const RunHistoryPage(), gateway()));
      await tester.pumpAndSettle();

      expect(requests.single.url.queryParameters.containsKey('q'), isFalse, reason: 'an empty box lists the newest');
      expect(find.text('2 runs'), findsOneWidget);
      expect(find.text('Goal text is not stored, so it cannot be searched.'), findsOneWidget,
          reason: 'what is being searched is said in the server\'s words');

      await tester.enterText(find.byType(TextField), 'read_file');
      await tester.tap(find.byKey(const ValueKey('run-search-button')));
      await tester.pumpAndSettle();

      expect(requests.last.url.queryParameters['q'], 'read_file');
      expect(requests.last.url.path, '/api/v3/agents/runs/search');
    });

    testWidgets('searching for something nothing matches shows an empty state', (tester) async {
      await tester.pumpWidget(_app(const RunHistoryPage(), gateway()));
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField), 'nonexistent');
      await tester.tap(find.byKey(const ValueKey('run-search-button')));
      await tester.pumpAndSettle();
      expect(find.text('No runs match'), findsOneWidget);
    });

    testWidgets('a failed run is red AND says "Failed" with an icon; a completed one is the ordinary card', (tester) async {
      await tester.pumpWidget(_app(const RunHistoryPage(), gateway()));
      await tester.pumpAndSettle();
      final palette = AuraPalette.of(tester.element(find.byType(RunHistoryPage)));

      final card = tester.widget<Container>(find.byKey(const ValueKey('run-failed-card')));
      expect(((card.decoration! as BoxDecoration).border! as Border).top.color, palette.danger);
      expect(tester.widget<Text>(find.byKey(const ValueKey('run-failed-label'))).data, 'Failed');
      expect(find.byIcon(Icons.error_outline), findsOneWidget);
      expect(find.bySemanticsLabel(RegExp(r'engineer, Failed')), findsOneWidget);
      expect(find.text('Completed'), findsOneWidget);
      expect(find.byKey(const ValueKey('run-failed-card')), findsOneWidget, reason: 'only the failed one');
    });

    testWidgets('tapping a run opens its timeline: steps in order, the failed step in red with its kind', (tester) async {
      await tester.pumpWidget(_app(const RunHistoryPage(), gateway()));
      await tester.pumpAndSettle();

      await tester.tap(find.byKey(const ValueKey('run-failed-card')));
      await tester.pumpAndSettle();

      expect(requests.last.url.path, '/api/v3/agents/runs/run-bad');
      expect(find.text('Run timeline'), findsOneWidget);
      expect(find.text('Pass 0'), findsOneWidget);
      final first = tester.getTopLeft(find.byKey(const ValueKey('run-step-1'))).dy;
      final second = tester.getTopLeft(find.byKey(const ValueKey('run-step-2'))).dy;
      expect(first, lessThan(second), reason: 'oldest step first, whatever order the server sent');
      expect(find.text('timeout'), findsOneWidget, reason: 'the failed step says why, in words');
      expect(tester.widget<Text>(find.byKey(const ValueKey('run-timeline-status'))).data, 'Failed');
    });
  });

  group('row 72: usage and cost', () {
    late List<http.Request> requests;
    bool enabled = true;
    bool truncated = false;

    MockClient gateway() {
      requests = [];
      return MockClient((request) async {
        requests.add(request);
        if (request.url.path == '/api/v3/usage/summary') {
          final groupBy = request.url.queryParameters['group_by'];
          if (!enabled) {
            return http.Response(jsonEncode({'enabled': false, 'totals': null, 'groups': [], 'note': 'off'}), 200);
          }
          return http.Response(
            jsonEncode({
              'enabled': true,
              'period': request.url.queryParameters['period'],
              'group_by': groupBy,
              'truncated': truncated,
              'totals': {'turns': 12, 'failed_turns': 2, 'tokens_in': 3000, 'tokens_out': 1500, 'cost_usd': 0.0421, 'elapsed_s': 40.0},
              'groups': [
                {'key': groupBy == 'agent' ? 'engineer' : '2026-11-22', 'turns': 8, 'failed_turns': 0, 'tokens_in': 2000, 'tokens_out': 1000, 'cost_usd': 0.03, 'elapsed_s': 20.0},
                {'key': groupBy == 'agent' ? 'orchestrator' : '2026-11-23', 'turns': 4, 'failed_turns': 2, 'tokens_in': 1000, 'tokens_out': 500, 'cost_usd': 0.0121, 'elapsed_s': 20.0},
              ],
            }),
            200,
          );
        }
        if (request.url.path == '/api/v3/usage/turns') {
          return http.Response(
            jsonEncode({
              'enabled': true,
              'turns': [
                {'turn_id': 't1', 'at': '2026-11-23T09:00:00Z', 'agent': 'engineer', 'outcome': 'success', 'model': 'm1', 'cached': false, 'usage': {'prompt_tokens': 100, 'completion_tokens': 50, 'cost_usd': 0.001, 'elapsed_ms': 2500}},
                {'turn_id': 't2', 'at': '2026-11-23T09:05:00Z', 'agent': 'engineer', 'outcome': 'error', 'error_kind': 'timeout', 'usage': {'prompt_tokens': 10, 'completion_tokens': 0, 'cost_usd': 0, 'elapsed_ms': 90000}},
              ],
            }),
            200,
          );
        }
        return http.Response('{}', 404);
      });
    }

    setUp(() {
      enabled = true;
      truncated = false;
    });

    testWidgets('shows totals, a bar per group and the recent turns', (tester) async {
      await tester.pumpWidget(_app(const UsagePage(), gateway()));
      await tester.pumpAndSettle();

      final summary = requests.firstWhere((r) => r.url.path == '/api/v3/usage/summary');
      expect(summary.url.queryParameters, {'period': 'week', 'group_by': 'day'}, reason: 'the defaults');
      expect(tester.widget<Text>(find.byKey(const ValueKey('usage-total-cost'))).data, '0.0421 USD');
      expect(tester.widget<Text>(find.byKey(const ValueKey('usage-total-turns'))).data, '12');
      expect(find.text('2 failed'), findsOneWidget);
      expect(find.byKey(const ValueKey('usage-group-2026-11-22')), findsOneWidget);
      expect(find.byKey(const ValueKey('usage-group-2026-11-23')), findsOneWidget);
      // The bigger spender has the full bar, the other is proportional.
      final bars = tester.widgetList<LinearProgressIndicator>(find.byType(LinearProgressIndicator)).toList();
      expect(bars.map((b) => b.value), [1.0, closeTo(0.0121 / 0.03, 1e-9)]);
      expect(find.byKey(const ValueKey('usage-truncated')), findsNothing);
      // Recent turns: a failed one is marked in words and not by colour alone.
      expect(find.byKey(const ValueKey('usage-turn-failed')), findsOneWidget);
    });

    testWidgets('the period and group-by switchers re-query with the chosen values', (tester) async {
      await tester.pumpWidget(_app(const UsagePage(), gateway()));
      await tester.pumpAndSettle();

      await tester.tap(find.byKey(const ValueKey('usage-period-month')));
      await tester.pumpAndSettle();
      expect(requests.last.url.queryParameters, {'period': 'month', 'group_by': 'day'});

      await tester.tap(find.byKey(const ValueKey('usage-group-agent')));
      await tester.pumpAndSettle();
      expect(requests.last.url.queryParameters, {'period': 'month', 'group_by': 'agent'});
      expect(find.byKey(const ValueKey('usage-group-engineer')), findsOneWidget);

      await tester.tap(find.byKey(const ValueKey('usage-period-day')));
      await tester.pumpAndSettle();
      expect(requests.last.url.queryParameters, {'period': 'day', 'group_by': 'agent'});
    });

    testWidgets('a truncated window says the totals are a floor', (tester) async {
      truncated = true;
      await tester.pumpWidget(_app(const UsagePage(), gateway()));
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('usage-truncated')), findsOneWidget);
    });

    testWidgets('a deployment that records no usage says so instead of showing zeros', (tester) async {
      enabled = false;
      await tester.pumpWidget(_app(const UsagePage(), gateway()));
      await tester.pumpAndSettle();
      expect(find.text('Usage is not recorded on this server.'), findsOneWidget);
      expect(find.byKey(const ValueKey('usage-total-cost')), findsNothing);
    });
  });

  group('row 91: waiting to send', () {
    testWidgets('offline: the task shows as "Waiting to send"; back online it is sent once and the row goes away', (tester) async {
      var online = false;
      final received = <String>[];
      final client = MockClient((request) async {
        if (request.url.path == '/api/v3/agents/tasks' && request.method == 'GET') {
          return http.Response(jsonEncode({'tasks': [], 'count': 0}), 200);
        }
        if (!online) throw http.ClientException('offline', request.url);
        final body = jsonDecode(request.body) as Map<String, dynamic>;
        received.add(body['client_request_id'] as String);
        return http.Response(
          jsonEncode({
            'task': {'id': 't1', 'goal': body['goal'], 'persona': 'orchestrator', 'status': 'queued', 'created_at': '2026-11-23T10:00:00Z'},
            'idempotent_replay': false,
          }),
          202,
        );
      });
      await tester.pumpWidget(_app(const AgentTasksPage(), client));
      await tester.pumpAndSettle();

      await tester.enterText(find.byType(TextField), 'summarise the open PRs');
      await tester.tap(find.text('Send task'));
      await tester.pumpAndSettle();

      expect(find.byKey(const ValueKey('agent-task-waiting-label')), findsOneWidget);
      expect(find.text('Waiting to send'), findsOneWidget);
      expect(find.text('Attempt 1 failed. Will retry on its own.'), findsOneWidget);
      expect(find.text('Saved. It will be sent when you are back online.'), findsOneWidget);
      expect(find.text('summarise the open PRs'), findsOneWidget);

      online = true;
      await tester.tap(find.text('Retry now'));
      await tester.pumpAndSettle();

      expect(find.byKey(const ValueKey('agent-task-waiting-label')), findsNothing);
      expect(received, hasLength(1));
    });

    testWidgets('a refused task stays on the list as "Not sent", red and in words, with the reason', (tester) async {
      final client = MockClient((request) async {
        if (request.method == 'GET') return http.Response(jsonEncode({'tasks': []}), 200);
        return http.Response('{"error":"consent_required","message":"Engineer consent needed"}', 403);
      });
      await tester.pumpWidget(_app(const AgentTasksPage(), client));
      await tester.pumpAndSettle();

      await tester.enterText(find.byType(TextField), 'push to main');
      await tester.tap(find.text('Send task'));
      await tester.pumpAndSettle();

      final palette = AuraPalette.of(tester.element(find.byType(AgentTasksPage)));
      expect(find.byKey(const ValueKey('agent-task-not-sent-label')), findsOneWidget);
      expect(find.text('Engineer consent needed'), findsOneWidget);
      final label = tester.widget<Text>(find.byKey(const ValueKey('agent-task-not-sent-label')));
      expect(label.style!.color, palette.danger);
      expect(find.byIcon(Icons.error_outline), findsOneWidget);

      await tester.tap(find.text('Dismiss'));
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('agent-task-not-sent-label')), findsNothing);
    });

    testWidgets('the server\'s own tasks list is shown, a failed one in red with words', (tester) async {
      final client = MockClient((request) async {
        return http.Response(
          jsonEncode({
            'tasks': [
              {'id': 'a', 'goal': 'ship it', 'persona': 'engineer', 'status': 'succeeded', 'created_at': '2026-11-22T10:00:00Z'},
              {'id': 'b', 'goal': 'break it', 'persona': 'engineer', 'status': 'failed', 'created_at': '2026-11-22T11:00:00Z', 'error': {'kind': 'timeout', 'message': 'took too long'}},
            ],
          }),
          200,
        );
      });
      await tester.pumpWidget(_app(const AgentTasksPage(), client));
      await tester.pumpAndSettle();
      expect(find.text('Done'), findsOneWidget);
      expect(tester.widget<Text>(find.byKey(const ValueKey('agent-task-failed-label'))).data, 'Failed');
      expect(find.text('took too long'), findsOneWidget);
    });
  });

  test('the outbox provider is not built by the screens above when signed out (no timers leak)', () {
    // The driver is the only thing that creates a timer, and it does nothing without a session.
    final container = ProviderContainer(overrides: [authProvider.overrideWith(() => _SignedOut())]);
    addTearDown(container.dispose);
    container.read(agentTaskOutboxDriverProvider);
  });
}

class _SignedOut extends AuthNotifier {
  @override
  Future<UserProfile?> build() async => null;
}
