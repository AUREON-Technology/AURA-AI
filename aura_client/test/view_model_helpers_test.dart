import 'package:aura_client/features/automations/presentation/github_view_model.dart';
import 'package:aura_client/features/calendar/presentation/calendar_view_model.dart';
import 'package:aura_client/features/finance/presentation/finance_view_model.dart';
import 'package:aura_client/features/health/presentation/health_view_model.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('sanitizeGitHubRepoPath', () {
    test('accepts owner/repo and strips slashes', () {
      expect(sanitizeGitHubRepoPath(' /Anas-Ibrahim/Aura-AI/ '), 'Anas-Ibrahim/Aura-AI');
    });

    test('rejects missing owner or extra path segments', () {
      expect(sanitizeGitHubRepoPath('Aura-AI'), isNull);
      expect(sanitizeGitHubRepoPath('a/b/c'), isNull);
      expect(sanitizeGitHubRepoPath(''), isNull);
    });
  });

  group('calendar month math', () {
    test('daysInMonth and Monday-based firstDayOffset', () {
      expect(daysInMonth(DateTime(2026, 2, 1)), 28);
      expect(daysInMonth(DateTime(2024, 2, 1)), 29);
      // 2026-08-01 is a Saturday → weekday 6 → offset 5
      expect(firstDayOffset(DateTime(2026, 8, 1)), 5);
      expect(dayKey(DateTime(2026, 8, 21)), '2026-08-21');
    });
  });

  group('parsePositiveAmount', () {
    test('accepts positive numbers and rejects junk', () {
      expect(parsePositiveAmount('12.50'), 12.50);
      expect(parsePositiveAmount('0'), isNull);
      expect(parsePositiveAmount('-1'), isNull);
      expect(parsePositiveAmount('abc'), isNull);
    });
  });

  group('goalProgress', () {
    test('clamps between 0 and 1', () {
      expect(goalProgress(0, 2000), 0);
      expect(goalProgress(1000, 2000), 0.5);
      expect(goalProgress(4000, 2000), 1.0);
      expect(goalProgress(10, 0), 0);
    });
  });
}
