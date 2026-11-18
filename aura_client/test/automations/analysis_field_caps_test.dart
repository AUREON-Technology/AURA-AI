// F36: the analysis routes refuse fields over their caps with 422, so the client cuts a long
// email or issue to fit rather than failing the whole analysis.
import 'package:flutter_test/flutter_test.dart';

import 'package:aura_client/features/automations/data/automation_repository.dart';

void main() {
  test('a value within the cap is sent unchanged', () {
    expect(AutomationRepository.clip('hello', 10), 'hello');
  });

  test('a long value is cut to the cap', () {
    final long = 'a' * (AutomationRepository.maxEmailBody + 10);
    expect(AutomationRepository.clip(long, AutomationRepository.maxEmailBody).length,
        AutomationRepository.maxEmailBody);
  });

  test('characters are counted as code points, never splitting one', () {
    const emoji = '😀';
    final cut = AutomationRepository.clip(emoji * 5, 3);
    expect(cut, emoji * 3);
    expect(cut.runes.length, 3);
  });

  test('the caps match the server', () {
    expect(AutomationRepository.maxEmailBody, 50000);
    expect(AutomationRepository.maxEmailSubject, 2000);
    expect(AutomationRepository.maxEmailSender, 512);
    expect(AutomationRepository.maxIssueBody, 65536);
    expect(AutomationRepository.maxIssueTitle, 1024);
  });
}
