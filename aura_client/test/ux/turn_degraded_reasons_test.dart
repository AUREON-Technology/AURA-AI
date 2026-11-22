/// Audit 2026-11-21, C3: the backend's `done` frame says *why* a turn was
/// degraded (`degraded_reasons`), and the client kept only `degraded: bool` --
/// so "the answer claimed to add a task but nothing ran" and "a backup model
/// answered" both read as one generic "reduced mode" line.
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:aura_client/core/ai/online_agents/sse_agent_client.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/features/chat/data/turn_meta.dart';
import 'package:aura_client/features/chat/presentation/widgets/turn_status_row.dart';

Widget _wrap(Widget child) => MaterialApp(
      theme: AuraThemeV2.dark,
      home: Scaffold(body: Center(child: child)),
    );

void main() {
  group('TurnMeta.degradedReasons', () {
    test('is read from the done frame and survives a round trip', () {
      final meta = TurnMeta.fromJson({
        'degraded': true,
        'degraded_reasons': ['unbacked_action_claim', 'tainted_context', 7],
      });
      expect(meta.degradedReasons, ['unbacked_action_claim', 'tainted_context']);
      expect(TurnMeta.fromJson(meta.toJson()).degradedReasons, meta.degradedReasons);
    });

    test('is empty against a backend that does not send it', () {
      expect(TurnMeta.fromJson({'degraded': true}).degradedReasons, isEmpty);
      expect(const TurnMeta().toJson().containsKey('degraded_reasons'), isFalse);
    });
  });

  group('revision frames', () {
    test('carry their reason, so a correction is not shown as a safety edit', () {
      final correction = SseChatEvent.fromWire('revision', {
        'text': 'Done.\n\nCorrection: no action was actually taken in this turn.',
        'reason': 'unbacked_action_claim',
      });
      expect(correction.kind, SseEventKind.revision);
      expect(correction.reason, 'unbacked_action_claim');
      expect(SseChatEvent.fromWire('revision', {'text': 'x'}).reason, isNull);
    });
  });

  group('the status row names the reason', () {
    testWidgets('an unbacked action claim says no action was taken', (tester) async {
      await tester.pumpWidget(_wrap(const TurnStatusRow(
        meta: TurnMeta(degraded: true, degradedReasons: ['unbacked_action_claim']),
        hasCitations: true,
      )));
      expect(find.text(TurnStatusRow.noActionTakenCopy), findsOneWidget);
      expect(find.text(TurnStatusRow.reducedModeCopy), findsNothing);
    });

    testWidgets('the most consequential reason wins', (tester) async {
      await tester.pumpWidget(_wrap(const TurnStatusRow(
        meta: TurnMeta(
          degraded: true,
          degradedReasons: ['tool_failure', 'planning_failed', 'provider_fallback'],
        ),
        hasCitations: true,
      )));
      expect(find.text(TurnStatusRow.toolsUnavailableCopy), findsOneWidget);
      expect(find.text(TurnStatusRow.toolFailedCopy), findsNothing);
    });

    testWidgets('an empty reply and a trimmed tool result are named', (tester) async {
      await tester.pumpWidget(_wrap(const TurnStatusRow(
        meta: TurnMeta(degraded: true, degradedReasons: ['empty_generation_fallback']),
        hasCitations: true,
      )));
      expect(find.text(TurnStatusRow.emptyReplyCopy), findsOneWidget);

      await tester.pumpWidget(_wrap(const TurnStatusRow(
        meta: TurnMeta(degraded: true, degradedReasons: ['tool_output_dropped']),
        hasCitations: true,
      )));
      expect(find.text(TurnStatusRow.toolOutputTrimmedCopy), findsOneWidget);
    });

    testWidgets('a provider fallback keeps the generic reduced-mode note', (tester) async {
      await tester.pumpWidget(_wrap(const TurnStatusRow(
        meta: TurnMeta(degraded: true, degradedReasons: ['provider_fallback']),
        hasCitations: true,
      )));
      expect(find.text(TurnStatusRow.reducedModeCopy), findsOneWidget);
    });

    testWidgets('no reasons, no degradation: nothing new appears', (tester) async {
      await tester.pumpWidget(_wrap(const TurnStatusRow(
        meta: TurnMeta(),
        hasCitations: true,
      )));
      expect(find.text(TurnStatusRow.noActionTakenCopy), findsNothing);
      expect(find.text(TurnStatusRow.reducedModeCopy), findsNothing);
    });
  });
}
