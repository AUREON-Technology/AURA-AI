// The desktop Engineer panel borrows the app-wide persona (audit 2026-11-16, F7): it switches
// to Engineer only when the account may use it, shows why when it may not, and hands the previous
// persona back when it closes -- unless the user picked another one in the meantime.
import 'package:aura_client/core/hostd/hostd_providers.dart';
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/security/encryption_service.dart';
import 'package:aura_client/core/workspaces/active_workspace.dart';
import 'package:aura_client/desktop/agent/agent_panel.dart';
import 'package:aura_client/desktop/events/ide_event_bus.dart';
import 'package:aura_client/desktop/state/state_notifiers.dart';
import 'package:aura_client/features/chat/data/agent_config_repository.dart';
import 'package:aura_client/features/chat/data/agents_repository.dart';
import 'package:aura_client/features/chat/presentation/widgets/chat_composer.dart';
import 'package:drift/native.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

const _usable = [
  AgentInfo(id: 'orchestrator', description: '', version: '1'),
  AgentInfo(id: 'finance_advisor', description: '', version: '1'),
  AgentInfo(id: 'engineer', description: '', version: '1', requiredCapabilities: {'engineer_agent'}),
];

const _locked = [
  AgentInfo(id: 'orchestrator', description: '', version: '1'),
  AgentInfo(id: 'finance_advisor', description: '', version: '1'),
  AgentInfo(
    id: 'engineer', description: '', version: '1',
    usable: false, requiredCapabilities: {'engineer_agent'},
  ),
];

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late AppDatabase db;
  late IdeEventBus bus;

  setUp(() async {
    await EncryptionService.init();
    db = AppDatabase.forTesting(NativeDatabase.memory());
    bus = IdeEventBus();
  });

  tearDown(() async {
    await bus.close();
    await db.close();
  });

  /// Pumps the panel next to a toggle that removes it, starting from `finance_advisor`.
  Future<(ProviderContainer, Future<void> Function())> pumpPanel(
    WidgetTester tester,
    List<AgentInfo> agents,
  ) async {
    final showPanel = ValueNotifier(true);
    addTearDown(showPanel.dispose);
    final container = ProviderContainer(
      overrides: [
        databaseProvider.overrideWithValue(db),
        ideEventBusProvider.overrideWithValue(bus),
        workspaceNotifierProvider.overrideWith((ref) => WorkspaceNotifier(db, bus)),
        // The Engineer panel's host banner watches /status, which polls every 3 s; a
        // running, paired daemon keeps the banner hidden and leaves no poll timer behind.
        hostdStatusProvider.overrideWith(
          (ref) => Stream.value(HostdStatusSnapshot(reachable: true, authenticated: true)),
        ),
        agentsProvider.overrideWith((ref) async => agents),
      ],
    );
    addTearDown(container.dispose);
    container.read(activeAgentIdProvider.notifier).setAgent('finance_advisor');

    await tester.pumpWidget(
      UncontrolledProviderScope(
        container: container,
        child: MaterialApp(
          theme: AuraThemeV2.dark,
          home: Scaffold(
            body: ValueListenableBuilder<bool>(
              valueListenable: showPanel,
              builder: (_, show, _) => show ? const AgentPanel() : const SizedBox.shrink(),
            ),
          ),
        ),
      ),
    );
    await tester.pump();
    await tester.pump();
    Future<void> closePanel() async {
      showPanel.value = false;
      await tester.pump();
      await tester.pump();
    }

    return (container, closePanel);
  }

  /// Unmount inside the test so Drift's stream-close timer fires before the binding checks for
  /// pending timers.
  Future<void> unmount(WidgetTester tester) async {
    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pump(const Duration(milliseconds: 1));
    await tester.pump(const Duration(milliseconds: 1));
  }

  testWidgets('switches to Engineer while open and restores the previous persona on close',
      (tester) async {
    final (container, closePanel) = await pumpPanel(tester, _usable);
    expect(container.read(activeAgentIdProvider), 'engineer');
    expect(find.byType(ChatComposer), findsOneWidget);

    await closePanel();
    expect(container.read(activeAgentIdProvider), 'finance_advisor');
    await unmount(tester);
  });

  testWidgets('does not override a persona the user chose while the panel was open',
      (tester) async {
    final (container, closePanel) = await pumpPanel(tester, _usable);
    container.read(activeAgentIdProvider.notifier).setAgent('orchestrator');

    await closePanel();
    expect(container.read(activeAgentIdProvider), 'orchestrator');
    await unmount(tester);
  });

  testWidgets('a locked Engineer is never selected and the panel says why', (tester) async {
    final (container, closePanel) = await pumpPanel(tester, _locked);
    expect(container.read(activeAgentIdProvider), 'finance_advisor');
    expect(find.text('Engineer is not available on this account'), findsOneWidget);
    expect(find.byType(ChatComposer), findsNothing);

    await closePanel();
    expect(container.read(activeAgentIdProvider), 'finance_advisor');
    await unmount(tester);
  });

  // The active workspace decides a turn, but sign-in and pairing accept any workspace with the
  // Engineer: a team member on their own Free workspace is told where it is and can switch.
  testWidgets('locked here but in a team: the panel names it and switches to it', (tester) async {
    const team = WorkspaceSummary(id: 'ws_team_1', kind: 'team', name: 'Acme', role: 'member');
    final (container, closePanel) =
        await pumpPanel(tester, AgentList(_locked, engineerWorkspaces: const [team]));
    expect(find.text('The Engineer is in another workspace'), findsOneWidget);
    expect(find.textContaining("but Acme does"), findsOneWidget);
    expect(find.byType(ChatComposer), findsNothing);

    await tester.tap(find.byKey(const Key('engineer-switch-ws_team_1')));
    await tester.pump();
    expect(container.read(activeWorkspaceProvider)?.id, 'ws_team_1');

    await closePanel();
    await unmount(tester);
  });

  test('engineerUsable reads the backend flag, and a missing Engineer is not usable', () {
    expect(engineerUsable(_usable), isTrue);
    expect(engineerUsable(_locked), isFalse);
    expect(engineerUsable(const [AgentInfo(id: 'orchestrator', description: '', version: '1')]),
        isFalse);
  });
}
