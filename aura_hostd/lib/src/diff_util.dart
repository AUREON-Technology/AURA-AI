/// Minimal line-based unified diff generator (D6, `docs/architecture/015-desktop-ide.md`) -- not
/// a byte-identical replica of git's own diff algorithm (this is an O(n*m) longest-common-
/// subsequence backtrace, one hunk per file, no minimal-hunk splitting), but produces the
/// `---`/`+++`/`@@`/`+`/`-`/` ` line shapes `SandboxDiffView` (aura_client) already knows how to
/// color -- good enough for a human reviewing what a write would do before approving it
/// (`approval_queue.dart`'s `PendingApproval.diffPreview`), which is this diff's only purpose. Not
/// used for anything that re-applies a patch, only for display.
library;

/// Hard ceiling on how many lines of `before`/`after` this will ever diff -- the LCS table below
/// is O(n*m) in line count, so a review diff beyond this would cost more memory/time than it's
/// worth. Matches this daemon's existing size-cap discipline elsewhere (e.g. `task_handler.dart`'s
/// `_maxOutputCapChars`); a file past this ceiling gets a placeholder instead of a real diff, not a
/// slow/unbounded computation.
const maxDiffInputLines = 2000;

/// Produces a unified diff of [path] between [before] and [after]. [before] is the empty string
/// for a file that doesn't exist yet (a new file) -- every line of [after] is then shown as added,
/// same as `git diff` would show for a new file.
String unifiedDiff({required String path, required String before, required String after}) {
  final beforeLines = before.isEmpty ? const <String>[] : before.split('\n');
  final afterLines = after.isEmpty ? const <String>[] : after.split('\n');
  final buffer = StringBuffer()
    ..writeln('--- a/$path')
    ..writeln('+++ b/$path');

  if (beforeLines.length > maxDiffInputLines || afterLines.length > maxDiffInputLines) {
    buffer.writeln(
      '@@ diff too large to render (${beforeLines.length} -> ${afterLines.length} lines) @@',
    );
    return buffer.toString();
  }

  buffer.writeln('@@ -1,${beforeLines.length} +1,${afterLines.length} @@');
  for (final op in _diffOps(beforeLines, afterLines)) {
    buffer.writeln('${op.marker}${op.text}');
  }
  return buffer.toString();
}

class _DiffOp {
  const _DiffOp(this.marker, this.text);
  final String marker; // ' ' (context), '-' (removed), or '+' (added)
  final String text;
}

/// Standard LCS dynamic-programming table + backtrace -- O(n*m) time and space, bounded by
/// [maxDiffInputLines] above (2000x2000 ints is a few MB, acceptable for the rare large file;
/// typical writes are far smaller).
List<_DiffOp> _diffOps(List<String> a, List<String> b) {
  final n = a.length;
  final m = b.length;
  final dp = List.generate(n + 1, (_) => List<int>.filled(m + 1, 0));
  for (var i = n - 1; i >= 0; i--) {
    for (var j = m - 1; j >= 0; j--) {
      dp[i][j] = a[i] == b[j]
          ? dp[i + 1][j + 1] + 1
          : (dp[i + 1][j] >= dp[i][j + 1] ? dp[i + 1][j] : dp[i][j + 1]);
    }
  }

  final ops = <_DiffOp>[];
  var i = 0;
  var j = 0;
  while (i < n && j < m) {
    if (a[i] == b[j]) {
      ops.add(_DiffOp(' ', a[i]));
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      ops.add(_DiffOp('-', a[i]));
      i++;
    } else {
      ops.add(_DiffOp('+', b[j]));
      j++;
    }
  }
  while (i < n) {
    ops.add(_DiffOp('-', a[i]));
    i++;
  }
  while (j < m) {
    ops.add(_DiffOp('+', b[j]));
    j++;
  }
  return ops;
}
