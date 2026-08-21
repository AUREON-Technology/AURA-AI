import 'package:aura_client/core/ai/online_agents/llm_timeouts.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('kLlmConnectTimeout matches AURA_LLM_TIMEOUT_S (180s)', () {
    expect(kLlmConnectTimeout, const Duration(seconds: 180));
  });
}
