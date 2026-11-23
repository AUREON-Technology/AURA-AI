// Tool chips and confirmation cards are driven by `GET /api/v3/tools`, and a failed Engineer run
// is shown in red -- and not in red alone.
//
// The fixture below is the shape `list_tools` in `aura_core/api/v3.py` returns (name, description,
// category, side_effecting, requires_confirmation, a JSON-schema `parameters`), not an invented one.

import 'package:aura_client/core/ai/engineer/engineer_workspaces.dart';
import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/network/aura_api_client.dart';
import 'package:aura_client/features/chat/data/chat_repository.dart';
import 'package:aura_client/features/chat/data/turn_meta.dart';
import 'package:aura_client/features/chat/data/tool_catalog_repository.dart';
import 'package:aura_client/features/chat/presentation/widgets/engineer_confirmation_card.dart';
import 'package:aura_client/features/chat/presentation/widgets/tool_call_chip.dart';
import 'package:aura_client/features/engineering/data/engineer_run_repository.dart';
import 'package:aura_client/features/engineering/presentation/engineer_history_page.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_riverpod/misc.dart' show Override;
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

const _toolsJson = {
  'tools': [
    {
      'name': 'docker_build',
      'description':
          "Build a Docker image on the user's own machine (needs AURA Desktop connected), from a "
              'Dockerfile and context directory inside the workspace. Returns the exit code.',
      'category': 'development',
      'side_effecting': true,
      'requires_confirmation': true,
      'parameters': {
        'type': 'object',
        'properties': {
          'image_tag': {'type': 'string', 'description': "Image name and optional tag, e.g. 'myapp:dev'."},
          'dockerfile': {'type': 'string'},
        },
      },
    },
    {
      'name': 'lint_code',
      'description': 'Lint the engineer workspace code and return structured findings.',
      'category': 'development',
      'side_effecting': false,
      'requires_confirmation': false,
      'parameters': {'type': 'object', 'properties': {}},
    },
    {
      'name': 'web_search',
      'description': 'Search the web.',
      'category': 'knowledge',
      'side_effecting': false,
      'requires_confirmation': false,
      'parameters': {'type': 'object', 'properties': {}},
    },
    {
      'name': 'mystery',
      'description': '',
      'category': 'something_new',
      'side_effecting': false,
      'requires_confirmation': false,
      'parameters': {},
    },
  ],
};

Widget _wrap(Widget child, {List<Override> overrides = const []}) => ProviderScope(
      overrides: overrides,
      child: MaterialApp(theme: AuraThemeV2.dark, home: Scaffold(body: child)),
    );

class _CatalogToken extends TokenManager {
  _CatalogToken(super.ref);
  @override
  Future<String?> getValidAccessToken() async => 'jwt';
}

ProviderContainer _scope(http.Client client) {
  final container = ProviderContainer(
    retry: (_, _) => null,
    overrides: [
      tokenManagerProvider.overrideWith((ref) => _CatalogToken(ref)),
      auraApiClientProvider.overrideWith((ref) => AuraApiClient(ref, httpClient: client)),
    ],
  );
  addTearDown(container.dispose);
  return container;
}

class _SeededPending extends PendingConfirmation {
  _SeededPending(this._seed);
  final List<PendingToolCall> _seed;
  @override
  List<PendingToolCall> build() => _seed;
}

EngineerRun _run(String status, {String tool = 'run_command'}) => EngineerRun(
      id: 'r-$status',
      turnId: 't',
      correlationId: 'c',
      persona: 'engineer',
      tool: tool,
      callId: 'call',
      status: status,
      branch: 'feature/x',
      createdAt: DateTime(2026, 11, 23, 10),
      updatedAt: DateTime(2026, 11, 23, 10),
    );

void main() {
  group('ToolCatalog', () {
    final catalog = ToolCatalog.fromJson(_toolsJson);

    test('reads every tool from the /tools shape', () {
      expect(catalog.length, 4);
      final build = catalog['docker_build']!;
      expect(build.category, 'development');
      expect(build.sideEffecting, isTrue);
      expect(build.requiresConfirmation, isTrue);
    });

    test('keeps each argument description from the JSON schema, and skips ones without', () {
      final build = catalog['docker_build']!;
      expect(build.parameterDescriptions['image_tag'], contains('myapp:dev'));
      expect(build.parameterDescriptions.containsKey('dockerfile'), isFalse);
    });

    test('the summary is the first sentence, not a paragraph written for the model', () {
      expect(
        catalog['docker_build']!.summary,
        "Build a Docker image on the user's own machine (needs AURA Desktop connected), from a "
        'Dockerfile and context directory inside the workspace.',
      );
      expect(catalog['mystery']!.summary, isEmpty);
    });

    test('a very long single sentence is cut, not shown whole', () {
      final long = ToolInfo(name: 'x', description: 'word ' * 100);
      expect(long.summary.length, lessThanOrEqualTo(140));
      expect(long.summary.endsWith('…'), isTrue);
    });

    test('an unknown tool is simply absent, and an unknown category gets the generic icon', () {
      expect(catalog['nope'], isNull);
      expect(iconForToolCategory('something_new'), Icons.build_outlined);
      expect(iconForToolCategory('development'), Icons.code);
    });

    test('malformed rows are skipped instead of failing the whole list', () {
      final partial = ToolCatalog.fromJson({
        'tools': ['junk', 3, {'name': ''}, {'name': 'ok', 'parameters': 'not a map'}],
      });
      expect(partial.length, 1);
      expect(partial['ok'], isNotNull);
    });
  });

  group('toolCatalogProvider', () {
    test('asks GET /api/v3/tools and parses the answer', () async {
      http.Request? seen;
      final container = _scope(MockClient((request) async {
        seen = request;
        return http.Response(
          '{"tools":[{"name":"lint_code","description":"Lint.","category":"development"}]}',
          200,
          headers: {'content-type': 'application/json'},
        );
      }));
      final catalog = await container.read(toolCatalogProvider.future);
      expect(seen!.method, 'GET');
      expect(seen!.url.path, '/api/v3/tools');
      expect(catalog['lint_code']!.category, 'development');
    });

    test('a failed fetch is an empty catalog, never an error', () async {
      final container = _scope(MockClient((_) async => http.Response('boom', 500)));
      final catalog = await container.read(toolCatalogProvider.future);
      expect(catalog.isEmpty, isTrue);
    });
  });

  group('tool chips', () {
    final catalog = ToolCatalog.fromJson(_toolsJson);

    testWidgets('a tool the client has no curated icon for takes its category icon', (tester) async {
      await tester.pumpWidget(_wrap(ToolCallChips(
        catalog: catalog,
        toolCalls: const [
          {'name': 'lint_code', 'arguments': {}, 'settled': true},
        ],
      )));
      expect(find.text('Lint Code'), findsOneWidget);
      expect(find.byIcon(Icons.code), findsOneWidget);
      expect(find.byIcon(Icons.build_outlined), findsNothing);
    });

    testWidgets('a curated icon still wins over the category', (tester) async {
      await tester.pumpWidget(_wrap(ToolCallChips(
        catalog: catalog,
        toolCalls: const [
          {'name': 'calculator', 'arguments': {}, 'settled': true},
        ],
      )));
      expect(find.byIcon(Icons.calculate_outlined), findsOneWidget);
    });

    testWidgets('the tooltip is the tool description, and approval-gated tools say so', (tester) async {
      await tester.pumpWidget(_wrap(ToolCallChips(
        catalog: catalog,
        toolCalls: const [
          {'name': 'docker_build', 'arguments': {}, 'settled': true},
          {'name': 'web_search', 'arguments': {}, 'settled': true},
        ],
      )));
      final tooltips = tester.widgetList<Tooltip>(find.byType(Tooltip)).map((t) => t.message ?? '').toList();
      expect(tooltips, hasLength(2));
      expect(tooltips.any((m) => m.startsWith('Build a Docker image') && m.contains('approval')), isTrue);
      expect(tooltips.any((m) => m == 'Search the web.'), isTrue);
      // Only the approval-gated one carries the marker.
      expect(find.byIcon(Icons.verified_user_outlined), findsOneWidget);
    });

    testWidgets('with no catalog a chip is exactly what it was: generic icon, no tooltip', (tester) async {
      await tester.pumpWidget(_wrap(const ToolCallChips(toolCalls: [
        {'name': 'docker_build', 'arguments': {}, 'settled': true},
      ])));
      expect(find.text('Docker Build'), findsOneWidget);
      expect(find.byIcon(Icons.build_outlined), findsOneWidget);
      expect(find.byType(Tooltip), findsNothing);
      expect(find.byIcon(Icons.verified_user_outlined), findsNothing);
    });
  });

  group('confirmation card for a tool the client has no card for', () {
    Future<void> pump(WidgetTester tester, {required bool withCatalog}) async {
      await tester.pumpWidget(_wrap(
        const EngineerConfirmationCard(turnId: 't1'),
        overrides: [
          pendingConfirmationProvider.overrideWith(() => _SeededPending(const [
                PendingToolCall(
                  callId: 'c1',
                  tool: 'docker_build',
                  arguments: {'image_tag': 'myapp:dev'},
                  turnId: 't1',
                ),
              ])),
          toolCatalogProvider.overrideWith(
            (ref) async => withCatalog ? ToolCatalog.fromJson(_toolsJson) : ToolCatalog.empty,
          ),
        ],
      ));
      await tester.pumpAndSettle();
    }

    testWidgets('shows the tool name, its description, its arguments and that it changes things', (tester) async {
      await pump(tester, withCatalog: true);
      expect(find.text('Docker Build'), findsOneWidget);
      expect(find.textContaining("Build a Docker image on the user's own machine"), findsOneWidget);
      expect(find.text('image_tag: myapp:dev'), findsOneWidget);
      expect(find.text('This action changes things outside the chat.'), findsOneWidget);
      expect(find.byIcon(Icons.code), findsWidgets);
    });

    testWidgets('the argument carries the schema description as its tooltip', (tester) async {
      await pump(tester, withCatalog: true);
      final tip = tester.widget<Tooltip>(find.ancestor(
        of: find.text('image_tag: myapp:dev'),
        matching: find.byType(Tooltip),
      ));
      expect(tip.message, contains('myapp:dev'));
    });

    testWidgets('without the catalog it is the plain name-and-arguments card it was', (tester) async {
      await pump(tester, withCatalog: false);
      expect(find.text('docker_build'), findsOneWidget);
      expect(find.text('image_tag: myapp:dev'), findsOneWidget);
      expect(find.text('This action changes things outside the chat.'), findsNothing);
    });
  });

  group('engineer history: a failed run is red', () {
    Future<AuraPalette> pumpPage(WidgetTester tester, List<EngineerRun> runs) async {
      await tester.pumpWidget(_wrap(
        const EngineerHistoryPage(),
        overrides: [
          engineerRunsProvider.overrideWith((ref) async => runs),
          engineerWorkspacesProvider.overrideWith((ref) async => EngineerWorkspaceList.empty),
        ],
      ));
      await tester.pumpAndSettle();
      return AuraPalette.of(tester.element(find.byType(EngineerHistoryPage)));
    }

    testWidgets('the whole tile is tinted and outlined in the danger colour', (tester) async {
      final palette = await pumpPage(tester, [_run('failed')]);
      final box = tester.widget<Container>(find.byKey(const ValueKey('engineer-run-failed-card')));
      final decoration = box.decoration! as BoxDecoration;
      expect((decoration.border! as Border).top.color, palette.danger);
      expect(decoration.color!.a, greaterThan(0));
      expect(decoration.color!.toARGB32() & 0x00FFFFFF, palette.danger.toARGB32() & 0x00FFFFFF);
    });

    testWidgets('red is not the only signal: the tile says "failed" with an icon, and says it to a screen reader', (tester) async {
      await pumpPage(tester, [_run('failed')]);
      final label = tester.widget<Text>(find.byKey(const ValueKey('engineer-run-failed-label')));
      expect(label.data, 'failed');
      expect(find.byIcon(Icons.error_outline), findsOneWidget);
      expect(find.bySemanticsLabel(RegExp(r'Status: failed')), findsOneWidget);
    });

    testWidgets('a succeeded or pending run keeps the ordinary card', (tester) async {
      await pumpPage(tester, [_run('succeeded'), _run('pending')]);
      expect(find.byKey(const ValueKey('engineer-run-failed-card')), findsNothing);
      expect(find.byKey(const ValueKey('engineer-run-failed-label')), findsNothing);
      expect(find.byType(AuraCard), findsNWidgets(2));
    });

    testWidgets('only the failed one of a mixed list is red', (tester) async {
      await pumpPage(tester, [_run('succeeded'), _run('failed'), _run('pending')]);
      expect(find.byKey(const ValueKey('engineer-run-failed-card')), findsOneWidget);
      expect(find.byType(AuraCard), findsNWidgets(2));
    });

    testWidgets('the detail sheet shows a failed status in red too', (tester) async {
      final palette = await pumpPage(tester, [_run('failed')]);
      await tester.tap(find.byKey(const ValueKey('engineer-run-failed-card')));
      await tester.pumpAndSettle();
      final status = tester.widget<Text>(find.text('Status: failed'));
      expect(status.style!.color, palette.danger);
    });
  });
}
