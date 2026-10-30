import 'package:aura_client/core/ai/online_agents/llm_timeouts.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('kLlmConnectTimeout matches AURA_LLM_TIMEOUT_S (180s)', () {
    expect(kLlmConnectTimeout, const Duration(seconds: 180));
  });

  test('kChatStreamIdleTimeout sits well above the 15s server keepalive', () {
    expect(kChatStreamIdleTimeout, const Duration(seconds: 45));
    expect(kChatStreamIdleTimeout, greaterThanOrEqualTo(const Duration(seconds: 15) * 3));
  });
}
