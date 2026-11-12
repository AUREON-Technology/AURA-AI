/// Phase 7, rule 14: `TurnMeta.degraded` was rendered as "AURA ran this turn
/// in a reduced mode." and nothing more — while the backend was already
/// sending `degraded_reasons`, whose own comment in `kernel.py` says the
/// bounded slugs exist "so a client can say *why*".
///
/// No client read them. These cover the translation from slug to sentence,
/// and the two restraints that keep it honest: a reason already stated by its
/// own note is not repeated, and a slug this client does not recognise is
/// dropped rather than leaked as a raw identifier.
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/features/chat/data/turn_meta.dart';
import 'package:aura_client/features/chat/presentation/widgets/turn_status_row.dart';
import 'package:aura_client/l10n/generated/app_localizations_en.dart';

Widget _wrap(Widget child) => MaterialApp(
      theme: AuraThemeV2.dark,
      home: Scaffold(body: Center(child: child)),
    );

final _l10n = AppLocalizationsEn();

void main() {
  group('TurnMeta carries the reasons', () {
    test('parses degraded_reasons from the done frame', () {
      final meta = TurnMeta.fromJson({
        'degraded': true,
        'degraded_reasons': ['provider_fallback', 'tool_failure'],
      });
      expect(meta.degradedReasons, ['provider_fallback', 'tool_failure']);
    });

    test('a missing or malformed list is empty, not a crash', () {
      expect(TurnMeta.fromJson(const {}).degradedReasons, isEmpty);
      expect(TurnMeta.fromJson({'degraded_reasons': 'nope'}).degradedReasons, isEmpty);
    });

    test('survives copyWith and a JSON round trip', () {
      const meta = TurnMeta(degraded: true, degradedReasons: ['tool_failure']);
      expect(meta.copyWith().degradedReasons, ['tool_failure']);
      expect(TurnMeta.decode(meta.encode()).degradedReasons, ['tool_failure']);
    });
  });

  group('explainDegradation', () {
    test('turns each known slug into a sentence', () {
      for (final slug in const [
        'provider_fallback',
        'tool_failure',
        'planning_failed',
        'empty_generation_fallback',
      ]) {
        expect(
          TurnStatusRow.explainDegradation(_l10n, [slug]),
          hasLength(1),
          reason: slug,
        );
      }
    });

    test('drops the two that already have their own note', () {
      // tainted_context is the quarantine line; empty_retrieval is the
      // library-empty line. Saying either twice would be worse than once.
      expect(
        TurnStatusRow.explainDegradation(_l10n, ['tainted_context', 'empty_retrieval']),
        isEmpty,
      );
    });

    test('drops an unrecognised slug rather than leaking the identifier', () {
      // A future backend adding a reason must not put `some_new_reason` on
      // screen; the generic line still covers the turn.
      final out = TurnStatusRow.explainDegradation(_l10n, ['some_new_reason']);
      expect(out, isEmpty);
    });

    test('does not repeat a sentence when a slug arrives twice', () {
      expect(
        TurnStatusRow.explainDegradation(_l10n, ['tool_failure', 'tool_failure']),
        hasLength(1),
      );
    });
  });

  group('TurnStatusRow', () {
    testWidgets('says why, instead of "a reduced mode"', (tester) async {
      await tester.pumpWidget(_wrap(const TurnStatusRow(
        meta: TurnMeta(degraded: true, degradedReasons: ['provider_fallback']),
        hasCitations: true,
      )));

      expect(find.text(_l10n.degradedProviderFallback), findsOneWidget);
      expect(find.text(TurnStatusRow.reducedModeCopy), findsNothing);
    });

    testWidgets('falls back to the generic line when no reason is explainable',
        (tester) async {
      // Either the backend named nothing, or everything it named is already
      // said elsewhere. The turn was still degraded, so it still says so.
      await tester.pumpWidget(_wrap(const TurnStatusRow(
        meta: TurnMeta(degraded: true, degradedReasons: ['tainted_context']),
        hasCitations: true,
      )));
      expect(find.text(TurnStatusRow.reducedModeCopy), findsOneWidget);
    });

    testWidgets('an older backend with no reasons still gets the generic line',
        (tester) async {
      await tester.pumpWidget(_wrap(const TurnStatusRow(
        meta: TurnMeta(degraded: true),
        hasCitations: true,
      )));
      expect(find.text(TurnStatusRow.reducedModeCopy), findsOneWidget);
    });

    testWidgets('an empty library does not hide a second reason', (tester) async {
      // Previously the library-empty line replaced the degraded note outright,
      // so a tool failure alongside it was invisible.
      await tester.pumpWidget(_wrap(const TurnStatusRow(
        meta: TurnMeta(
          degraded: true,
          searchedButEmpty: true,
          degradedReasons: ['empty_retrieval', 'tool_failure'],
        ),
        hasCitations: true,
      )));

      expect(
        find.textContaining(TurnStatusRow.libraryEmptyCopy),
        findsOneWidget,
      );
      expect(find.textContaining(_l10n.degradedToolFailure), findsOneWidget);
    });

    testWidgets('joins several reasons into one note', (tester) async {
      await tester.pumpWidget(_wrap(const TurnStatusRow(
        meta: TurnMeta(
          degraded: true,
          degradedReasons: ['provider_fallback', 'planning_failed'],
        ),
        hasCitations: true,
      )));
      expect(
        find.text('${_l10n.degradedProviderFallback} ${_l10n.degradedPlanningFailed}'),
        findsOneWidget,
      );
    });
  });
}
