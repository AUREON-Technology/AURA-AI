import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/features/health/presentation/widgets/health_daily_dashboard.dart';
import 'package:aura_client/features/tasks/presentation/widgets/task_item_card.dart';
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

// F-32 / R4.2: Life Hub task titles and the health card must stay legible on the light theme
// (the default), not only on the dark one their legacy colours were picked for.

const _wcagAaText = 4.5;

Future<void> _pump(WidgetTester tester, ThemeData theme, Widget child) {
  return tester.pumpWidget(
    ProviderScope(
      child: MaterialApp(
        theme: theme,
        home: Scaffold(body: Padding(padding: const EdgeInsets.all(16), child: child)),
      ),
    ),
  );
}

/// The colour a [Text] actually paints with (its style merged over the inherited default).
Color _textColor(WidgetTester tester, String text) =>
    tester.renderObject<RenderParagraph>(find.text(text)).text.style!.color!;

/// The fill of the [Card] that encloses [text].
Color _cardColor(WidgetTester tester, String text) {
  final card = find.ancestor(of: find.text(text), matching: find.byType(Card)).first;
  final material = tester.widget<Material>(
    find.descendant(of: card, matching: find.byType(Material)).first,
  );
  return material.color!;
}

void _expectLegible(WidgetTester tester, String text) {
  final ratio = contrastRatio(_textColor(tester, text), _cardColor(tester, text));
  expect(
    ratio,
    greaterThanOrEqualTo(_wcagAaText),
    reason: '"$text" is ${ratio.toStringAsFixed(2)}:1 against its card',
  );
}

void main() {
  final task = Task(
    id: 'task-1',
    title: 'Renew passport',
    description: 'Book the appointment first',
    priority: 'medium',
    isCompleted: false,
    category: 'personal',
    createdAt: DateTime(2026, 10, 1),
    isSynced: true,
  );

  // Themes are read inside each test body, not here: building one loads fonts, which needs the
  // test binding.
  for (final name in ['light', 'dark']) {
    ThemeData theme() => name == 'light' ? AuraThemeV2.light : AuraThemeV2.dark;

    testWidgets('TaskItemCard title and description meet WCAG AA under the $name theme',
        (tester) async {
      await _pump(tester, theme(), TaskItemCard(task: task));

      _expectLegible(tester, 'Renew passport');
      _expectLegible(tester, 'Book the appointment first');
    });

    testWidgets('HealthDailyDashboard headings meet WCAG AA under the $name theme',
        (tester) async {
      await _pump(tester, theme(), const HealthDailyDashboard(record: null));

      _expectLegible(tester, 'Daily Goals Progress');
      _expectLegible(tester, 'Quick Logger Panel');
      _expectLegible(tester, 'Water Intake');
    });
  }

  testWidgets('the light theme card is the surface the audit measured against', (tester) async {
    // Pins the premise: on this white surface the old AuraTheme.textPrimary was ~1.05:1.
    expect(AuraThemeV2.light.cardTheme.color, AuraPalette.light.surface);
    expect(contrastRatio(const Color(0xFFF8FAFC), AuraPalette.light.surface), lessThan(1.1));
  });
}
