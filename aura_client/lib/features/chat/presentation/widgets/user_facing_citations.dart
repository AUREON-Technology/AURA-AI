/// Bookkeeping provenances the kernel packs into [ContextBundle.citations]
/// that must not appear as user-facing source chips.
const _hiddenExact = {'episodic_summary', 'working_memory', 'cag'};
const _hiddenPrefixes = ['memory:', 'goal:', 'tool:'];

bool isUserFacingCitation(String citation) {
  final label = citation.trim();
  if (label.isEmpty) return false;
  if (_hiddenExact.contains(label)) return false;
  for (final prefix in _hiddenPrefixes) {
    if (label.startsWith(prefix)) return false;
  }
  return true;
}

List<String> userFacingCitations(Iterable<String> citations) {
  return citations.where(isUserFacingCitation).toList(growable: false);
}
