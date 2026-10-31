/// Phase 7, rule 14 (what AURA knows) and rule 46 (do not hide security
/// risks): `TurnMeta.tainted` has arrived on every `done` frame since the
/// trust-channel work shipped — `kernel.py` emits it — and nothing in the
/// client rendered it.
///
/// Per ADR-006 it means the turn's context was quarantined, which is the
/// mechanism that stops a poisoned document from silently triggering a write.
/// The user saw the answer with no sign that it had engaged.
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/features/chat/data/turn_meta.dart';
import 'package:aura_client/features/chat/presentation/widgets/turn_status_row.dart';

Widget _wrap(Widget child) => MaterialApp(
      theme: AuraThemeV2.dark,
      home: Scaffold(body: Center(child: child)),
    );

void main() {
  group('quarantined context', () {
    testWidgets('is reported when the backend says the turn was tainted',
        (tester) async {
      await tester.pumpWidget(_wrap(const TurnStatusRow(
        meta: TurnMeta(tainted: true),
        hasCitations: true,
      )));
      expect(find.text(TurnStatusRow.quarantinedContextCopy), findsOneWidget);
    });

    testWidgets('is silent on an ordinary turn', (tester) async {
      await tester.pumpWidget(_wrap(const TurnStatusRow(
        meta: TurnMeta(),
        hasCitations: true,
      )));
      expect(find.text(TurnStatusRow.quarantinedContextCopy), findsNothing);
    });

    testWidgets('says what is prevented, not just that something is off',
        (tester) async {
      // The useful half is the protection: nothing with side effects runs
      // without confirmation. A bare "untrusted content" warning would leave
      // the user with nothing to do about it.
      await tester.pumpWidget(_wrap(const TurnStatusRow(
        meta: TurnMeta(tainted: true),
        hasCitations: true,
      )));
      expect(find.textContaining('without your confirmation'), findsOneWidget);
    });

    testWidgets('leads the notes — it bounds what the answer may do', (tester) async {
      // Every other note rates how good the answer is. This one is about what
      // it is allowed to do, so it should not be read last.
      await tester.pumpWidget(_wrap(const TurnStatusRow(
        meta: TurnMeta(tainted: true, revised: true, degraded: true),
        hasCitations: false,
      )));

      final quarantine = tester.getTopLeft(
        find.text(TurnStatusRow.quarantinedContextCopy),
      );
      final redacted = tester.getTopLeft(
        find.text('This answer was redacted by a safety check.'),
      );
      expect(quarantine.dy, lessThan(redacted.dy));
    });
  });

  group('what is deliberately not rendered', () {
    testWidgets('an absent guards_complete is not reported as a failed guard',
        (tester) async {
      // `TurnMeta.guardsComplete` parses as `json['guards_complete'] == true`,
      // so a backend that never sends the field is indistinguishable from one
      // reporting guards that did not finish. Warning on `!guardsComplete`
      // would fire on every turn from an older backend — a false alarm, which
      // is worse than the silence it replaces. Making the field nullable is
      // the prerequisite; this test pins the restraint until then.
      await tester.pumpWidget(_wrap(const TurnStatusRow(
        meta: TurnMeta(),
        hasCitations: true,
      )));
      expect(find.textContaining('safety check'), findsNothing);
      expect(find.textContaining('guard'), findsNothing);
    });
  });
}
