import 'package:aura_client/core/insights/insights_client.dart';
import 'package:flutter_test/flutter_test.dart';

/// The `X-Timezone` value the backend parses to compute the user's "today"
/// (audit F-17). Its accepted forms are pinned on the server side in
/// `aura_backend/tests/unit/test_user_timezone.py`.
void main() {
  group('formatUtcOffset', () {
    test('formats east, west, zero and half-hour offsets as +HH:MM', () {
      expect(formatUtcOffset(const Duration(hours: 3)), '+03:00');
      expect(formatUtcOffset(const Duration(hours: -5)), '-05:00');
      expect(formatUtcOffset(Duration.zero), '+00:00');
      expect(formatUtcOffset(const Duration(hours: 5, minutes: 30)), '+05:30');
      expect(formatUtcOffset(const Duration(hours: -9, minutes: -30)), '-09:30');
      expect(formatUtcOffset(const Duration(hours: 14)), '+14:00');
    });

    test('the device offset always produces a well-formed value', () {
      expect(
        formatUtcOffset(DateTime.now().timeZoneOffset),
        matches(RegExp(r'^[+-]\d{2}:\d{2}$')),
      );
    });
  });
}
