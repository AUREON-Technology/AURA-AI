/// Phase 11, the last two states in rule 19's vocabulary: `syncing` and the
/// one that matters more — a change the server has refused and that is
/// therefore *not* on the server, while the engine has nothing left to try
/// and reports itself settled.
///
/// Reporting that as "Synced" is untrue in the way that costs most: the user
/// believes their work is safe when it is not. Both signals already existed
/// (`SyncState`, `parkedOutboxCountProvider`); only one widget inside chat
/// rendered them.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/sync/sync_client.dart';
import 'package:aura_client/core/sync/sync_status_view.dart';
import 'package:aura_client/features/sync/sync_engine.dart';

class _FixedSyncEngine extends SyncEngine {
  _FixedSyncEngine(this._state);
  final SyncState _state;

  @override
  SyncState build() => _state;
}

void main() {
  group('auraSyncPhaseFor', () {
    test('parked changes override a settled sync', () {
      expect(auraSyncPhaseFor(SyncState.synced, 0), AuraSyncPhase.synced);
      expect(auraSyncPhaseFor(SyncState.synced, 2), AuraSyncPhase.pendingRefused);
    });

    test('a live state wins over parked changes', () {
      // Mid-sync, what is happening now is the more useful thing to show.
      expect(auraSyncPhaseFor(SyncState.syncing, 5), AuraSyncPhase.syncing);
      expect(auraSyncPhaseFor(SyncState.offline, 5), AuraSyncPhase.offline);
      expect(auraSyncPhaseFor(SyncState.error, 5), AuraSyncPhase.failed);
    });
  });

  group('auraSyncLabelFor', () {
    test('names what failed rather than saying "Error"', () {
      expect(auraSyncLabelFor(AuraSyncPhase.failed, 0), 'Sync failed');
    });

    test('counts parked changes, singular and plural', () {
      expect(auraSyncLabelFor(AuraSyncPhase.pendingRefused, 1), '1 change not synced');
      expect(auraSyncLabelFor(AuraSyncPhase.pendingRefused, 3), '3 changes not synced');
    });
  });

  test('every phase has its own glyph, so colour is never the only signal', () {
    final icons = AuraSyncPhase.values.map(auraSyncIconFor).toSet();
    expect(icons, hasLength(AuraSyncPhase.values.length));
  });

  group('AuraSyncStatusView', () {
    Future<void> pump(
      WidgetTester tester, {
      SyncState state = SyncState.synced,
      int parked = 0,
      bool showLabel = true,
      VoidCallback? onSyncNow,
    }) async {
      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            syncEngineProvider.overrideWith(() => _FixedSyncEngine(state)),
            parkedOutboxCountProvider.overrideWith((ref) => Stream.value(parked)),
          ],
          child: MaterialApp(
            theme: AuraThemeV2.dark,
            home: Scaffold(
              body: Center(
                child: AuraSyncStatusView(showLabel: showLabel, onSyncNow: onSyncNow),
              ),
            ),
          ),
        ),
      );
      // Not pumpAndSettle: the syncing state renders a CircularProgressIndicator,
      // which animates forever and would never settle. Two pumps is enough to
      // deliver the parked-count stream's first value.
      await tester.pump();
      await tester.pump();
    }

    Semantics statusSemantics(WidgetTester tester) {
      return tester
          .widgetList<Semantics>(find.byType(Semantics))
          .firstWhere((s) => s.properties.liveRegion == true);
    }

    testWidgets('says how many changes are not synced, not "Synced"', (tester) async {
      await pump(tester, state: SyncState.synced, parked: 2);
      expect(find.text('2 changes not synced'), findsOneWidget);
      expect(find.text('Synced'), findsNothing);
    });

    testWidgets('with the label hidden, the status still reaches a screen reader',
        (tester) async {
      // The narrow-surface case. The previous version dropped the text and
      // left a tinted dot, so the state was carried by colour alone.
      await pump(tester, state: SyncState.error, showLabel: false);

      expect(find.text('Sync failed'), findsNothing);
      expect(statusSemantics(tester).properties.label, 'Sync status: Sync failed');
    });

    testWidgets('announces the status as a live region', (tester) async {
      await pump(tester, state: SyncState.synced, parked: 1);
      final semantics = statusSemantics(tester);
      expect(semantics.properties.liveRegion, isTrue);
      expect(semantics.properties.label, 'Sync status: 1 change not synced');
    });

    testWidgets('shows a spinner and no button while syncing', (tester) async {
      await pump(tester, state: SyncState.syncing, onSyncNow: () {});
      expect(find.byType(CircularProgressIndicator), findsOneWidget);
      // Nothing to press mid-sync — the old version disabled the button;
      // this replaces it, which is one fewer dead control.
      expect(find.byType(IconButton), findsNothing);
    });

    testWidgets('Sync now runs the callback it was given', (tester) async {
      var synced = 0;
      await pump(tester, state: SyncState.synced, onSyncNow: () => synced++);
      await tester.tap(find.byType(IconButton));
      expect(synced, 1);
    });

    testWidgets('is read-only when given no callback', (tester) async {
      await pump(tester, state: SyncState.synced);
      expect(tester.widget<IconButton>(find.byType(IconButton)).onPressed, isNull);
      // And the tooltip does not offer an action it cannot perform.
      expect(find.byTooltip('Synced'), findsOneWidget);
    });
  });
}
