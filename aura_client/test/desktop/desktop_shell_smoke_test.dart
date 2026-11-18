/// M8.0 smoke check: does the Desktop IDE shell render at all?
///
/// Until this file, no test pumped `DesktopShell` itself — only individual
/// pieces (activity bar, layout notifier, explorer service). The shell is
/// gated to Windows and Linux (`isDesktopIde`, WP7), and a test host's default
/// target platform is neither, so the `/ide` route would render its "not
/// available" page and nothing would exercise the composed layout.
///
/// This pumps the real shell with the platform overridden to Windows, and again
/// to Linux (D-08b), at the
/// sizes the breakpoint logic distinguishes and in both text directions, then
/// visits every activity-bar destination and the bottom panel. The assertion
/// is the absence of framework exceptions (layout overflow, unbounded
/// constraints, provider errors) — a rendering-level check, not a claim about
/// how the IDE looks or behaves on a real Windows or Linux machine.
///
/// It also pins one piece of behaviour the composed shell owns: below the wide
/// breakpoint a floating panel must not stop an activity-bar click from
/// reaching its destination in one click.
///
/// The daemon providers are overridden with a fixed "daemon not reachable"
/// snapshot, so nothing polls a real `aura_hostd`.
library;

import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/hostd/hostd_providers.dart';
import 'package:aura_client/core/platform/aura_platform.dart';
import 'package:aura_client/core/security/encryption_service.dart';
import 'package:aura_client/desktop/agent/agent_panel.dart';
import 'package:aura_client/desktop/control_center/control_center_panel.dart';
import 'package:aura_client/desktop/events/ide_event_bus.dart';
import 'package:aura_client/desktop/explorer/explorer_panel.dart';
import 'package:aura_client/desktop/shell/bottom_panel.dart';
import 'package:aura_client/desktop/shell/desktop_shell.dart';
import 'package:aura_client/desktop/state/state_notifiers.dart';
import 'package:drift/native.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';

const _destinations = [
  'Explorer (Ctrl+B)',
  'Source Control',
  'AURA Agents (Ctrl+J)',
  'AURA Control Center',
  'Terminal, Output & Problems (Ctrl+Shift+J)',
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

  Future<void> pumpShell(
    WidgetTester tester, {
    required Size size,
    required TextDirection direction,
  }) async {
    tester.view.physicalSize = size;
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final router = GoRouter(
      initialLocation: '/ide',
      routes: [
        ShellRoute(
          builder: (context, state, child) => DesktopShell(child: child),
          routes: [
            GoRoute(
              path: '/ide',
              builder: (context, state) => const Center(child: Text('welcome')),
            ),
          ],
        ),
      ],
    );

    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          databaseProvider.overrideWithValue(db),
          ideEventBusProvider.overrideWithValue(bus),
          hostdStatusProvider.overrideWith(
            (ref) => Stream.value(HostdStatusSnapshot(reachable: false)),
          ),
          pendingApprovalsProvider.overrideWith((ref) => Stream.value(const [])),
          workspaceNotifierProvider.overrideWith((ref) => WorkspaceNotifier(db, bus)),
          layoutNotifierProvider.overrideWith((ref) => LayoutNotifier(db, bus)),
        ],
        child: MaterialApp.router(
          theme: AuraThemeV2.dark,
          routerConfig: router,
          builder: (context, child) => Directionality(textDirection: direction, child: child!),
        ),
      ),
    );
    await tester.pumpAndSettle();
  }

  /// The floating-panel scrim, present only while a sidebar floats over the
  /// editor (narrow windows).
  final scrim = find.byWidgetPredicate(
    (w) => w is Semantics && w.properties.label == 'Close panel',
  );

  /// Unmount inside the test so Drift's stream-close timer fires before the
  /// binding checks for pending timers.
  Future<void> unmount(WidgetTester tester) async {
    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pump(const Duration(milliseconds: 1));
    await tester.pump(const Duration(milliseconds: 1));
  }

  /// Runs [body] against the shell with the platform forced to [platform] and
  /// hit-test misses fatal: a tap that lands on some other widget means the
  /// control is covered and a user could not click it either.
  Future<void> withShell(
    WidgetTester tester,
    TargetPlatform platform,
    Size size,
    TextDirection direction,
    Future<void> Function() body,
  ) async {
    debugDefaultTargetPlatformOverride = platform;
    WidgetController.hitTestWarningShouldBeFatal = true;
    try {
      expect(isDesktopIde, isTrue, reason: '${platform.name} is a Desktop IDE platform');
      await pumpShell(tester, size: size, direction: direction);
      expect(find.byType(DesktopShell), findsOneWidget);
      expect(tester.takeException(), isNull);
      await body();
      await unmount(tester);
    } finally {
      WidgetController.hitTestWarningShouldBeFatal = false;
      debugDefaultTargetPlatformOverride = null;
    }
  }

  Future<void> click(WidgetTester tester, String tooltip) async {
    final item = find.byTooltip(tooltip);
    expect(item, findsWidgets, reason: 'activity bar item "$tooltip" missing');
    await tester.tap(item.first);
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull, reason: 'after clicking "$tooltip"');
  }

  const sizes = {
    'wide': Size(1920, 1080),
    'wide edge': Size(1280, 800),
    'standard': Size(1024, 700),
    'compact': Size(800, 600),
  };

  // D-08b: the same checks on Linux, where the IDE now runs too.
  for (final platform in [TargetPlatform.windows, TargetPlatform.linux]) {
    for (final direction in TextDirection.values) {
      for (final entry in sizes.entries) {
        final size = entry.value;
        final label = '${size.width.toInt()}x${size.height.toInt()} (${entry.key}) ${direction.name}';

        testWidgets('shell renders and every destination opens, clicked back to back — ${platform.name} $label',
            (tester) async {
          await withShell(tester, platform, size, direction, () async {
            for (final tooltip in _destinations) {
              await click(tester, tooltip);
            }
          });
        });
      }

      // Below the wide breakpoint the AI panel floats over the primary sidebar.
      // A primary destination opened underneath it would change nothing the user
      // can see, so one click has to close the AI panel and show the
      // destination. Before the fix the scrim covered the activity bar and a
      // click only closed the AI panel: two clicks per destination.
      for (final entry in {'standard': sizes['standard']!, 'compact': sizes['compact']!}.entries) {
        final label = '${entry.key} ${direction.name}';

        testWidgets('one click reaches Control Center while the AI panel floats — ${platform.name} $label',
            (tester) async {
          await withShell(tester, platform, entry.value, direction, () async {
            await click(tester, 'AURA Agents (Ctrl+J)');
            expect(find.byType(AgentPanel), findsOneWidget);
            expect(scrim, findsOneWidget);

            await click(tester, 'AURA Control Center');
            expect(find.byType(AgentPanel), findsNothing);
            expect(find.byType(ControlCenterPanel), findsOneWidget);
          });
        });

        testWidgets('one click reaches the bottom panel while the AI panel floats — ${platform.name} $label',
            (tester) async {
          await withShell(tester, platform, entry.value, direction, () async {
            await click(tester, 'AURA Agents (Ctrl+J)');
            expect(find.byType(BottomPanel), findsNothing);

            await click(tester, 'Terminal, Output & Problems (Ctrl+Shift+J)');
            expect(find.byType(AgentPanel), findsNothing);
            expect(find.byType(BottomPanel), findsOneWidget);
            expect(scrim, findsNothing);
          });
        });
      }

      testWidgets('one click reaches the bottom panel while the Explorer floats — ${platform.name} compact ${direction.name}',
          (tester) async {
        await withShell(tester, platform, sizes['compact']!, direction, () async {
          // The Explorer is open by default (LayoutState.showPrimarySidebar), and
          // in compact the primary sidebar floats -- so it is already up.
          expect(find.byType(ExplorerPanel), findsOneWidget);
          expect(scrim, findsOneWidget, reason: 'the primary sidebar floats in compact');

          await click(tester, 'Terminal, Output & Problems (Ctrl+Shift+J)');
          expect(scrim, findsNothing);
          expect(find.byType(ExplorerPanel), findsNothing);
          expect(find.byType(BottomPanel), findsOneWidget);
        });
      });

      testWidgets('clicking the scrim still closes the floating AI panel — ${platform.name} standard ${direction.name}',
          (tester) async {
        await withShell(tester, platform, sizes['standard']!, direction, () async {
          await click(tester, 'AURA Agents (Ctrl+J)');
          expect(find.byType(AgentPanel), findsOneWidget);

          // Just inside the content area on its start side: scrim, not panel
          // (the floating AI panel sits at the end side) and not activity bar.
          final shell = tester.getRect(find.byType(DesktopShell));
          const inset = 48.0 + 20.0;
          final x = direction == TextDirection.ltr ? shell.left + inset : shell.right - inset;
          await tester.tapAt(Offset(x, shell.center.dy));
          await tester.pumpAndSettle();

          expect(find.byType(AgentPanel), findsNothing);
          expect(scrim, findsNothing);
        });
      });
    }
  }
}
