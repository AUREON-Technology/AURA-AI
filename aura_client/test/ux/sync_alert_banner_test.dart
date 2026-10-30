/// Phase 11: `AuraSyncStatusView` can only show sync state where a screen has
/// chrome to hold a pill, and chat is the only one that does. So on Tasks,
/// Finance or Calendar a change the server refused looked exactly like a
/// change that saved.
///
/// These cover the shell-level banner that closes that gap, and the two
/// properties that keep it from becoming noise: it is silent when there is
/// nothing to say, and it says the work is safe locally rather than just
/// raising an alarm.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/sync/sync_alert_banner.dart';
import 'package:aura_client/core/sync/sync_client.dart';

void main() {
  Future<void> pump(
    WidgetTester tester, {
    required int parked,
    VoidCallback? onReview,
  }) async {
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          parkedOutboxCountProvider.overrideWith((ref) => Stream.value(parked)),
        ],
        child: MaterialApp(
          theme: AuraThemeV2.dark,
          home: Scaffold(body: SyncAlertBanner(onReview: onReview)),
        ),
      ),
    );
    await tester.pump();
    await tester.pump();
  }

  testWidgets('says nothing at all when everything has reached the server',
      (tester) async {
    // Permanent chrome that usually reads "fine" is chrome people stop seeing.
    await pump(tester, parked: 0);
    expect(find.byType(AuraBanner), findsNothing);
    expect(tester.getSize(find.byType(SyncAlertBanner)), Size.zero);
  });

  testWidgets('counts the changes, and says where they are', (tester) async {
    await pump(tester, parked: 3);
    expect(find.byType(AuraBanner), findsOneWidget);
    expect(
      find.textContaining("3 changes haven't reached the server"),
      findsOneWidget,
    );
    // The reassurance matters as much as the alarm: the work is not lost.
    expect(find.textContaining('saved on this device'), findsOneWidget);
  });

  testWidgets('reads singular for one change', (tester) async {
    await pump(tester, parked: 1);
    expect(find.textContaining("1 change hasn't reached"), findsOneWidget);
  });

  testWidgets('announces itself as a live region, in full sentences',
      (tester) async {
    await pump(tester, parked: 2);
    final semantics = tester
        .widgetList<Semantics>(find.byType(Semantics))
        .firstWhere((s) => s.properties.liveRegion == true);
    // The visible copy uses contractions; the announced copy does not, since
    // "haven't" read aloud is worse than "have not".
    expect(semantics.properties.label, '2 changes have not reached the server. '
        'They are saved on this device.');
  });

  testWidgets('offers Review only when given something to do', (tester) async {
    await pump(tester, parked: 2);
    expect(find.text('Review'), findsNothing);

    var reviewed = 0;
    await pump(tester, parked: 2, onReview: () => reviewed++);
    await tester.tap(find.text('Review'));
    expect(reviewed, 1);
  });

  group('AuraBanner', () {
    testWidgets('is not dismissible — the condition outlives the dismissal',
        (tester) async {
      await tester.pumpWidget(MaterialApp(
        theme: AuraThemeV2.dark,
        home: const Scaffold(
          body: AuraBanner(icon: Icons.warning_amber, message: 'Something is up'),
        ),
      ));
      // No close affordance: a banner the user can silence while the problem
      // is still true is a problem they have been invited to forget.
      expect(find.byIcon(Icons.close), findsNothing);
      expect(find.byType(IconButton), findsNothing);
    });

    testWidgets('an action with no callback is not rendered', (tester) async {
      await tester.pumpWidget(MaterialApp(
        theme: AuraThemeV2.dark,
        home: const Scaffold(
          body: AuraBanner(
            icon: Icons.warning_amber,
            message: 'Something is up',
            actionLabel: 'Ghost',
          ),
        ),
      ));
      expect(find.text('Ghost'), findsNothing);
    });
  });
}
