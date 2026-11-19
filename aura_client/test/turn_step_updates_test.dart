// A step's "running" frame and its result share a step_id. The app used to append both, so every
// finished step kept a spinning "running" row beside its result -- on every planned turn, and on
// every image turn (ADR-031's vision step). Phase I also adds `degraded`: the vision model was
// asleep and only OCR read the image.

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/features/chat/data/turn_meta.dart';
import 'package:aura_client/features/chat/presentation/widgets/step_log_panel.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

TurnStep _step(String id, String status, [String label = 'Looking at the image(s)']) =>
    TurnStep.fromJson({'step_id': id, 'kind': 'vision', 'status': status, 'description': label});

void main() {
  group('upsertStep', () {
    test('a step\'s result replaces its running row where it stands', () {
      final steps = <TurnStep>[];
      upsertStep(steps, _step('vision', 'running'));
      upsertStep(steps, _step('s1', 'running', 'Search'));
      upsertStep(steps, _step('vision', 'done', 'Looked at the image(s)'));

      expect(steps.map((s) => '${s.stepId}:${s.status}'), ['vision:done', 's1:running']);
      expect(steps.first.label, 'Looked at the image(s)');
    });

    test('a new step is appended', () {
      final steps = [_step('a', 'succeeded')];
      upsertStep(steps, _step('b', 'running'));
      expect(steps.map((s) => s.stepId), ['a', 'b']);
    });

    test('steps without an id cannot be matched and are kept apart', () {
      final steps = <TurnStep>[];
      upsertStep(steps, const TurnStep(kind: 'generate', status: 'running'));
      upsertStep(steps, const TurnStep(kind: 'generate', status: 'succeeded'));
      expect(steps, hasLength(2));
    });
  });

  group('StepLogPanel icons', () {
    Future<void> pump(WidgetTester tester, TurnStep step) => tester.pumpWidget(MaterialApp(
          theme: AuraThemeV2.light,
          home: Scaffold(body: StepLogPanel(steps: [step])),
        ));

    testWidgets('the vision step\'s "done" shows as finished, not pending', (tester) async {
      await pump(tester, _step('vision', 'done', 'Looked at the image(s)'));
      expect(find.byIcon(Icons.check_circle), findsOneWidget);
      expect(find.byType(CircularProgressIndicator), findsNothing);
    });

    testWidgets('an OCR-only look warns and says why', (tester) async {
      const why = 'Only the text in the image(s) could be read: the vision model is unavailable '
          'or still starting. Send it again in a minute for a full look.';
      await pump(tester, _step('vision', 'degraded', why));
      expect(find.byIcon(Icons.warning_amber_rounded), findsOneWidget);
      expect(find.text(why), findsOneWidget);
    });
  });
}
