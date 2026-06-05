import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:aura_client/core/ai/ai_router/aura_ai_router.dart';

void main() {
  group('AuraAIRouter Route Selection Tests', () {
    test('Force Online Setting ignores network status and selects Gemini', () async {
      final container = ProviderContainer();
      container.read(aiProviderSettingProvider.notifier).state = AIProvider.gemini;

      final router = container.read(aiRouter);
      final route = await router.selectRoute();

      expect(route["provider"], equals(AIProvider.gemini));
      expect(route["model"], equals("gemini-2.5-flash"));
    });

    test('Force Offline Fast Setting selects Phi-3', () async {
      final container = ProviderContainer();
      container.read(aiProviderSettingProvider.notifier).state = AIProvider.offlineFast;

      final router = container.read(aiRouter);
      final route = await router.selectRoute();

      expect(route["provider"], equals(AIProvider.offlineFast));
      expect(route["model"], equals("phi3"));
    });

    test('Force Offline Reasoning Setting selects Mistral', () async {
      final container = ProviderContainer();
      container.read(aiProviderSettingProvider.notifier).state = AIProvider.offlineReasoning;

      final router = container.read(aiRouter);
      final route = await router.selectRoute();

      expect(route["provider"], equals(AIProvider.offlineReasoning));
      expect(route["model"], equals("mistral"));
    });

    test('Force Offline Premium Setting selects Llama3', () async {
      final container = ProviderContainer();
      container.read(aiProviderSettingProvider.notifier).state = AIProvider.offlinePremium;

      final router = container.read(aiRouter);
      final route = await router.selectRoute();

      expect(route["provider"], equals(AIProvider.offlinePremium));
      expect(route["model"], equals("llama3"));
    });
  });
}
