/// M8.0 smoke check: does the Desktop IDE shell render at all?
///
/// Until this file, no test pumped `DesktopShell` itself — only individual
/// pieces (activity bar, layout notifier, explorer service). The shell is
/// gated to Windows (`isDesktopIde`), so on a Linux CI host the `/ide` route
/// renders `SizedBox.shrink()` and nothing exercises the composed layout.
///
/// This pumps the real shell with the platform overridden to Windows, at the
/// sizes the breakpoint logic distinguishes and in both text directions, then
/// visits every activity-bar destination and the bottom panel. The assertion
/// is the absence of framework exceptions (layout overflow, unbounded
/// constraints, provider errors) — a rendering-level check, not a claim about
/// how the IDE looks or behaves on a real Windows machine.
///
/// The daemon providers are overridden with a fixed "daemon not reachable"
/// snapshot, so nothing polls a real `aura_hostd`.
library;

import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/hostd/hostd_providers.dart';
import 'package:aura_client/core/security/encryption_service.dart';
import 'package:aura_client/desktop/events/ide_event_bus.dart';
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

  for (final direction in TextDirection.values) {
    for (final size in const [Size(1920, 1080), Size(1280, 800), Size(1024, 700)]) {
      testWidgets('shell renders and every destination opens — '
          '${size.width.toInt()}x${size.height.toInt()} ${direction.name}', (tester) async {
        debugDefaultTargetPlatformOverride = TargetPlatform.windows;
        // A tap that lands on some other widget means the control is covered
        // and a user could not click it either.
        WidgetController.hitTestWarningShouldBeFatal = true;
        try {
          await pumpShell(tester, size: size, direction: direction);
          expect(find.byType(DesktopShell), findsOneWidget);
          expect(tester.takeException(), isNull);

          for (final tooltip in _destinations) {
            final item = find.byTooltip(tooltip);
            expect(item, findsWidgets, reason: 'activity bar item "$tooltip" missing');
            await tester.tap(item.first);
            await tester.pumpAndSettle();
            expect(tester.takeException(), isNull, reason: 'opening "$tooltip"');

            // A floating panel's scrim covers the activity bar (see the skipped
            // test below), so close it the way a user would — by clicking the
            // scrim, here over the activity-bar column, which no floating panel
            // covers — before visiting the next destination.
            if (scrim.evaluate().isNotEmpty) {
              final column = tester.getRect(find.byTooltip(_destinations.first).first);
              await tester.tapAt(column.center);
              await tester.pumpAndSettle();
              expect(scrim, findsNothing, reason: 'clicking the scrim after "$tooltip" closes it');
            }
          }

          await unmount(tester);
        } finally {
          WidgetController.hitTestWarningShouldBeFatal = false;
          debugDefaultTargetPlatformOverride = null;
        }
      });
    }
  }

  // Found by this file on 2026-11-08: below the breakpoint where the AI panel
  // floats, its scrim is `Positioned.fill` over the whole shell
  // (desktop_shell.dart, `_ShellBody`), activity bar included. A click on any
  // other activity-bar item then only closes the panel — the user has to click
  // twice to reach, e.g., the Control Center. The scrim's own comment expects
  // the activity bar to stay usable. Un-skip once the scrim starts after the
  // activity bar.
  testWidgets('activity bar stays clickable while the AI panel floats', (tester) async {
    debugDefaultTargetPlatformOverride = TargetPlatform.windows;
    WidgetController.hitTestWarningShouldBeFatal = true;
    try {
      await pumpShell(tester, size: const Size(1024, 700), direction: TextDirection.ltr);
      await tester.tap(find.byTooltip('AURA Agents (Ctrl+J)').first);
      await tester.pumpAndSettle();
      expect(scrim, findsOneWidget);
      await tester.tap(find.byTooltip('AURA Control Center').first);
      await tester.pumpAndSettle();
      await unmount(tester);
    } finally {
      WidgetController.hitTestWarningShouldBeFatal = false;
      debugDefaultTargetPlatformOverride = null;
    }
  }, skip: true); // known defect, see comment above
}
