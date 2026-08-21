/// Maps GoRouter `extra` from the Home capture bar into a Copilot query.
String? chatInitialQueryFromExtra(Object? extra) {
  if (extra is! String) return null;
  final query = extra.trim();
  return query.isEmpty ? null : query;
}
