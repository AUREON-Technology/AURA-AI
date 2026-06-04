class AppConfig {
  // Configured via compile-time environment injection (e.g. --dart-define=BACKEND_URL=...)
  static const String backendUrl = String.fromEnvironment(
    'BACKEND_URL',
    defaultValue: "https://aura-ai-production-a31f.up.railway.app",
  );
}
