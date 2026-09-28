/// Phase 11 (UX_BASELINE_AUDIT.md §2, and the brief's rules 19–21): every
/// feature needs one vocabulary for empty, error and offline, and each of
/// those must answer what is empty, why, and what the user can do — with the
/// kind of state reaching a screen reader rather than living only in the
/// colour of an icon.
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:aura_client/core/design_system/design_system.dart';

Widget _wrap(Widget child) => MaterialApp(
      theme: AuraThemeV2.dark,
      home: Scaffold(body: Center(child: child)),
    );

Semantics _stateSemantics(WidgetTester tester) {
  return tester
      .widgetList<Semantics>(find.byType(Semantics))
      .firstWhere((s) => s.properties.liveRegion == true);
}

void main() {
  testWidgets('an empty state answers what, why and what now', (tester) async {
    var started = 0;
    await tester.pumpWidget(_wrap(
      AuraEmptyState(
        icon: Icons.smart_toy_outlined,
        title: 'No agent runs yet',
        message: 'AURA agent sessions appear here once you ask it to do something.',
        actionLabel: 'Start an agent task',
        onAction: () => started++,
      ),
    ));

    expect(find.text('No agent runs yet'), findsOneWidget);
    expect(find.textContaining('appear here once you ask'), findsOneWidget);

    await tester.tap(find.text('Start an agent task'));
    expect(started, 1);
  });

  testWidgets('an error state can offer two ways out', (tester) async {
    var retried = 0;
    var checked = 0;
    await tester.pumpWidget(_wrap(
      AuraEmptyState(
        icon: Icons.cloud_off,
        tone: AuraStateTone.offline,
        title: "Couldn't connect to AURA",
        message: 'The Control Plane did not respond. Your local workspace is safe.',
        actionLabel: 'Retry',
        onAction: () => retried++,
        secondaryActionLabel: 'Check connection',
        onSecondaryAction: () => checked++,
      ),
    ));

    await tester.tap(find.text('Retry'));
    await tester.tap(find.text('Check connection'));
    expect(retried, 1);
    expect(checked, 1);
  });

  testWidgets('a secondary action without its callback is not rendered', (tester) async {
    await tester.pumpWidget(_wrap(
      const AuraEmptyState(
        icon: Icons.inbox_outlined,
        title: 'Nothing here',
        secondaryActionLabel: 'Ghost button',
      ),
    ));
    expect(find.text('Ghost button'), findsNothing);
  });

  group('tone', () {
    testWidgets('is announced, not only painted', (tester) async {
      // An error rendered as a differently-coloured grey block is
      // indistinguishable from an empty one to anyone not seeing the colour.
      await tester.pumpWidget(_wrap(
        const AuraEmptyState(
          icon: Icons.error_outline,
          tone: AuraStateTone.error,
          title: 'It broke',
          message: 'Here is why.',
        ),
      ));

      expect(_stateSemantics(tester).properties.label, 'Error. It broke. Here is why.');
    });

    testWidgets('neutral adds no prefix — nothing is wrong', (tester) async {
      await tester.pumpWidget(_wrap(
        const AuraEmptyState(icon: Icons.inbox_outlined, title: 'Nothing here'),
      ));
      expect(_stateSemantics(tester).properties.label, 'Nothing here');
    });

    testWidgets('offline reads as offline, not as a generic failure', (tester) async {
      await tester.pumpWidget(_wrap(
        const AuraEmptyState(
          icon: Icons.cloud_off,
          tone: AuraStateTone.offline,
          title: 'No connection',
        ),
      ));
      expect(_stateSemantics(tester).properties.label, 'Offline. No connection');
    });

    testWidgets('each tone paints a distinguishable icon colour', (tester) async {
      final colours = <AuraStateTone, Color?>{};
      for (final tone in AuraStateTone.values) {
        await tester.pumpWidget(_wrap(
          AuraEmptyState(icon: Icons.circle, tone: tone, title: 'x'),
        ));
        colours[tone] = tester.widget<Icon>(find.byIcon(Icons.circle)).color;
      }
      expect(colours[AuraStateTone.error], isNot(colours[AuraStateTone.neutral]));
      expect(colours[AuraStateTone.warning], isNot(colours[AuraStateTone.error]));
    });
  });

  testWidgets('the state is a live region, so its arrival is announced', (tester) async {
    await tester.pumpWidget(_wrap(
      const AuraEmptyState(icon: Icons.inbox_outlined, title: 'Nothing here'),
    ));
    expect(_stateSemantics(tester).properties.liveRegion, isTrue);
    // The composed label is the announcement; the child text must not be read
    // a second time underneath it.
    expect(_stateSemantics(tester).excludeSemantics, isTrue);
  });
}
