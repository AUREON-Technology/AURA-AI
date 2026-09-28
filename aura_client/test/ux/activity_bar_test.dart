/// UX_BASELINE_AUDIT.md F-02: the desktop activity bar carried one control
/// wired to an empty callback ("Search") and one labelled for something other
/// than what it did (a gear labelled "Settings" that toggled the bottom
/// panel). Both sat in the app's primary navigation.
///
/// These assert the fixed behaviour, plus the accessibility affordances an
/// icon-only navigation bar needs: a tooltip per item, selection exposed to
/// semantics rather than only as a tint, and a disabled item that renders and
/// announces as disabled instead of silently swallowing the tap.
library;

import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/security/encryption_service.dart';
import 'package:aura_client/desktop/events/ide_event.dart';
import 'package:aura_client/desktop/events/ide_event_bus.dart';
import 'package:aura_client/desktop/shell/activity_bar.dart';
import 'package:aura_client/desktop/state/state_notifiers.dart';
import 'package:drift/native.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

const _goToFileTooltip = 'Go to File (Ctrl+P)';
const _panelTooltip = 'Terminal, Output & Problems (Ctrl+Shift+J)';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late AppDatabase db;
  late IdeEventBus bus;
  late List<IdeEvent> published;

  setUp(() async {
    await EncryptionService.init();
    db = AppDatabase.forTesting(NativeDatabase.memory());
    bus = IdeEventBus();
    published = <IdeEvent>[];
    bus.stream.listen(published.add);
  });

  tearDown(() async {
    await bus.close();
    await db.close();
  });

  /// `workspaceNotifierProvider` is overridden rather than left to its own
  /// factory because that factory also listens to `hostdStatusProvider`,
  /// which polls a real daemon. Nothing here needs the daemon.
  Future<void> pumpBar(WidgetTester tester) async {
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          databaseProvider.overrideWithValue(db),
          ideEventBusProvider.overrideWithValue(bus),
          workspaceNotifierProvider.overrideWith((ref) => WorkspaceNotifier(db, bus)),
          layoutNotifierProvider.overrideWith((ref) => LayoutNotifier(db, bus)),
        ],
        child: MaterialApp(
          theme: AuraThemeV2.dark,
          home: const Scaffold(
            body: SizedBox(height: 720, child: ActivityBar()),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
  }

  Future<void> openWorkspace(WidgetTester tester, ProviderContainer container) async {
    await container.read(workspaceNotifierProvider.notifier).createWorkspace(
          id: 'ws-1',
          rootPath: '/tmp/ws-1',
          displayName: 'ws-1',
        );
    await tester.pumpAndSettle();
  }

  testWidgets('every item carries a tooltip — the bar is icon-only', (tester) async {
    await pumpBar(tester);

    for (final tooltip in const [
      'Explorer (Ctrl+B)',
      _goToFileTooltip,
      'Source Control',
      'AURA Agents (Ctrl+J)',
      'AURA Control Center',
      _panelTooltip,
    ]) {
      expect(find.byTooltip(tooltip), findsOneWidget, reason: 'missing tooltip: $tooltip');
    }
  });

  testWidgets('Go to File is disabled, not silently inert, with no workspace open',
      (tester) async {
    await pumpBar(tester);

    final inkWell = tester.widget<InkWell>(
      find.descendant(of: find.byTooltip(_goToFileTooltip), matching: find.byType(InkWell)),
    );
    expect(inkWell.onTap, isNull);

    await tester.tap(find.byTooltip(_goToFileTooltip), warnIfMissed: false);
    await tester.pump();
    expect(published.whereType<ShowQuickOpenRequested>(), isEmpty);
  });

  testWidgets('Go to File opens Quick Open once a workspace is open', (tester) async {
    await pumpBar(tester);
    final container = ProviderScope.containerOf(
      tester.element(find.byType(ActivityBar)),
      listen: false,
    );
    await openWorkspace(tester, container);

    await tester.tap(find.byTooltip(_goToFileTooltip));
    await tester.pump();

    expect(published.whereType<ShowQuickOpenRequested>(), hasLength(1));
  });

  testWidgets('the bottom item is named for the panel it toggles, and tracks it',
      (tester) async {
    await pumpBar(tester);
    final container = ProviderScope.containerOf(
      tester.element(find.byType(ActivityBar)),
      listen: false,
    );

    // Not "Settings" — it has never opened settings.
    expect(find.byTooltip(_panelTooltip), findsOneWidget);
    expect(container.read(layoutNotifierProvider).showBottomPanel, isFalse);

    await tester.tap(find.byTooltip(_panelTooltip));
    await tester.pumpAndSettle();

    expect(container.read(layoutNotifierProvider).showBottomPanel, isTrue);
  });

  testWidgets('selection is exposed to semantics, not only as a colour', (tester) async {
    await pumpBar(tester);

    // Explorer is the default primary sidebar panel and the sidebar starts
    // visible, so it is the selected item on first render. Selection is
    // otherwise carried by a brand tint and a 6px dot -- neither of which a
    // screen reader can report.
    final semantics = tester.widget<Semantics>(
      find
          .ancestor(of: find.byTooltip('Explorer (Ctrl+B)'), matching: find.byType(Semantics))
          .first,
    );
    expect(semantics.properties.button, isTrue);
    expect(semantics.properties.selected, isTrue);
    expect(semantics.properties.enabled, isTrue);
    expect(semantics.properties.label, 'Explorer');
  });

  testWidgets('a disabled item announces as disabled', (tester) async {
    await pumpBar(tester);

    final semantics = tester.widget<Semantics>(
      find.ancestor(of: find.byTooltip(_goToFileTooltip), matching: find.byType(Semantics)).first,
    );
    expect(semantics.properties.enabled, isFalse);
  });
}
