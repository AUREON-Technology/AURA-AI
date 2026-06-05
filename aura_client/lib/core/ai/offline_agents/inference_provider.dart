abstract class InferenceProvider {
  /// Streams text completions chunk-by-chunk for the active model configuration
  Stream<String> generateStream({
    required String model,
    required List<Map<String, String>> messages,
    required String systemInstruction,
  });

  /// Pings the provider to check active status and availability
  Future<bool> checkHealth();
}
