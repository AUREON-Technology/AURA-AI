/// Phase 7, rule 12 (expose the plan) and rule 15 (what remains).
///
/// Two findings behind this suite. The first is a plumbing bug: the streamed
/// `done` frame sends the plan as `plan_summary` (kernel.py) while the
/// non-streaming `/chat` body sends `plan` (v3.py), and `TurnMeta.fromJson`
/// read only `plan` — so on the path chat actually uses, `TurnMeta.plan` was
/// empty for every real turn. The second is what that hid: a turn can plan
/// four steps, run two and answer anyway, and nothing distinguished it from a
/// turn where everything ran.
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/features/chat/data/turn_meta.dart';
import 'package:aura_client/features/chat/presentation/widgets/incomplete_plan_notice.dart';

Widget _wrap(Widget child) => MaterialApp(
      theme: AuraThemeV2.dark,
      home: Scaffold(body: Center(child: child)),
    );

PlanStep _step(String status, {String? description}) =>
    PlanStep(id: status, kind: 'tool', status: status, description: description);

void main() {
  group('the plan reaches the client from either transport', () {
    test('the streamed done frame key (plan_summary) is accepted', () {
      // This is the bug: kernel.py emits `plan_summary`, and reading only
      // `plan` left this empty on every streamed turn.
      final meta = TurnMeta.fromJson({
        'plan_summary': [
          {'id': 's1', 'kind': 'tool', 'status': 'failed', 'description': 'Read the file'},
        ],
      });
      expect(meta.plan, hasLength(1));
      expect(meta.plan.single.status, 'failed');
    });

    test('the non-streaming body key (plan) still works', () {
      final meta = TurnMeta.fromJson({
        'plan': [
          {'id': 's1', 'kind': 'tool', 'status': 'succeeded', 'description': 'Read the file'},
        ],
      });
      expect(meta.plan, hasLength(1));
    });

    test('neither key present is simply an empty plan', () {
      expect(TurnMeta.fromJson(const {}).plan, isEmpty);
    });
  });

  group('what counts as a gap', () {
    test('succeeded and skipped are settled', () {
      // A skipped step is a planner decision, not an omission.
      expect(planHasGaps([_step('succeeded'), _step('skipped')]), isFalse);
    });

    test('failed, blocked and never-started are gaps', () {
      for (final status in const ['failed', 'blocked', 'pending', 'ready', 'running']) {
        expect(planHasGaps([_step(status)]), isTrue, reason: status);
      }
    });

    test('an absent or unknown status is not treated as a problem', () {
      // An older backend, or a status this client has never seen, must not
      // raise an alarm it cannot justify.
      expect(planHasGaps([const PlanStep(id: 'x', kind: 'tool')]), isFalse);
      expect(planHasGaps([_step('')]), isFalse);
    });

    test('a skipped step is listed once something else opens the notice', () {
      // It should not trigger the notice alone, but hiding it while showing
      // its siblings would misrepresent the plan.
      final shown = unfinishedPlanSteps([_step('succeeded'), _step('skipped'), _step('failed')]);
      expect(shown.map((s) => s.status), ['skipped', 'failed']);
    });
  });

  group('IncompletePlanNotice', () {
    testWidgets('is silent when every step succeeded', (tester) async {
      await tester.pumpWidget(_wrap(
        IncompletePlanNotice(plan: [_step('succeeded'), _step('succeeded')]),
      ));
      expect(tester.getSize(find.byType(IncompletePlanNotice)), Size.zero);
    });

    testWidgets('is silent on an empty plan', (tester) async {
      await tester.pumpWidget(_wrap(const IncompletePlanNotice(plan: [])));
      expect(tester.getSize(find.byType(IncompletePlanNotice)), Size.zero);
    });

    testWidgets('counts the gaps against the whole plan', (tester) async {
      await tester.pumpWidget(_wrap(IncompletePlanNotice(plan: [
        _step('succeeded'),
        _step('succeeded'),
        _step('failed', description: 'Run the tests'),
        _step('blocked', description: 'Push the branch'),
      ])));
      expect(
        find.text('AURA planned 4 steps and 2 did not complete.'),
        findsOneWidget,
      );
    });

    testWidgets('reads singular for one gap', (tester) async {
      await tester.pumpWidget(_wrap(IncompletePlanNotice(plan: [
        _step('succeeded'),
        _step('failed', description: 'Run the tests'),
      ])));
      expect(
        find.text('AURA planned 2 steps and one did not complete.'),
        findsOneWidget,
      );
    });

    testWidgets("keeps the backend's own status word per step", (tester) async {
      // blocked, failed and never-started are genuinely different outcomes;
      // flattening them to "incomplete" would drop the useful part.
      await tester.pumpWidget(_wrap(IncompletePlanNotice(plan: [
        _step('failed', description: 'Run the tests'),
        _step('blocked', description: 'Push the branch'),
      ])));
      expect(find.text('Run the tests — failed'), findsOneWidget);
      expect(find.text('Push the branch — blocked'), findsOneWidget);
    });

    testWidgets('falls back to the step kind when it has no description',
        (tester) async {
      await tester.pumpWidget(_wrap(IncompletePlanNotice(plan: [_step('failed')])));
      expect(find.text('tool — failed'), findsOneWidget);
    });

    testWidgets('announces that the answer came without them', (tester) async {
      await tester.pumpWidget(_wrap(IncompletePlanNotice(plan: [
        _step('succeeded'),
        _step('failed', description: 'Run the tests'),
      ])));
      final semantics = tester
          .widgetList<Semantics>(find.byType(Semantics))
          .firstWhere((s) => s.properties.liveRegion == true);
      expect(semantics.properties.label, contains('without them'));
    });
  });
}
