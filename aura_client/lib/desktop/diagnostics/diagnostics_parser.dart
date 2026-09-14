/// Parses each diagnostics tool's own output format into the shared [Diagnostic] shape (D7 Part
/// A.2). One pure function per tool -- the daemon's `/diagnostics/run` is pure transport (see
/// `aura_hostd/lib/src/diagnostics_service.dart`'s doc comment), so interpretation lives here.
library;

import 'dart:convert';

import 'package:aura_client/desktop/events/ide_event.dart';

typedef DiagnosticsByFile = Map<String, List<Diagnostic>>;

/// [rootPath] is used to turn each tool's own (often absolute) file paths into paths relative to
/// the workspace root -- the same shape `EditorTab.path`/the Explorer already use, so a Problems
/// row can be matched straight back to an open tab.
DiagnosticsByFile parseDiagnostics(String tool, String stdout, String rootPath) {
  try {
    return switch (tool) {
      'dart_analyze' => _parseDartAnalyzeMachine(stdout, rootPath),
      'ruff' => _parseRuffJson(stdout, rootPath),
      'pyflakes' => _parsePyflakesText(stdout, rootPath),
      'eslint' => _parseEslintJson(stdout, rootPath),
      _ => const {},
    };
  } catch (_) {
    // A tool upgrading its output format shouldn't crash the Problems panel -- worst case is a
    // scan silently yields nothing this run, which the "no diagnostics" empty state already
    // covers gracefully; better than throwing through a UI event handler.
    return const {};
  }
}

void _add(DiagnosticsByFile map, String path, Diagnostic diagnostic) {
  (map[path] ??= []).add(diagnostic);
}

String _relativize(String rawPath, String rootPath) {
  final normalizedPath = rawPath.replaceAll('\\', '/');
  final normalizedRoot = rootPath.replaceAll('\\', '/');
  if (normalizedPath.startsWith(normalizedRoot)) {
    var rest = normalizedPath.substring(normalizedRoot.length);
    while (rest.startsWith('/')) {
      rest = rest.substring(1);
    }
    return rest;
  }
  return normalizedPath;
}

/// `dart analyze --format=machine` emits one `|`-delimited line per diagnostic:
/// `SEVERITY|TYPE|ERROR_CODE|FILE|LINE|COLUMN|LENGTH|MESSAGE`. `MESSAGE` itself may contain `|`
/// escaped as `\|`, so split on unescaped pipes only.
DiagnosticsByFile _parseDartAnalyzeMachine(String stdout, String rootPath) {
  final result = <String, List<Diagnostic>>{};
  for (final line in const LineSplitter().convert(stdout)) {
    if (line.trim().isEmpty) continue;
    final parts = _splitUnescapedPipes(line);
    if (parts.length < 8) continue;
    final severity = switch (parts[0].toUpperCase()) {
      'ERROR' => DiagnosticSeverity.error,
      'WARNING' => DiagnosticSeverity.warning,
      'INFO' => DiagnosticSeverity.info,
      _ => DiagnosticSeverity.hint,
    };
    final path = _relativize(parts[3], rootPath);
    final lineNo = int.tryParse(parts[4]) ?? 1;
    final col = int.tryParse(parts[5]) ?? 1;
    final message = parts[7].replaceAll(r'\|', '|');
    _add(
      result,
      path,
      Diagnostic(
        range: EditorRange(startLine: lineNo, startColumn: col, endLine: lineNo, endColumn: col),
        severity: severity,
        message: message,
        source: 'dart analyze',
        code: parts[2],
      ),
    );
  }
  return result;
}

List<String> _splitUnescapedPipes(String line) {
  final parts = <String>[];
  final buffer = StringBuffer();
  for (var i = 0; i < line.length; i++) {
    if (line[i] == '\\' && i + 1 < line.length && line[i + 1] == '|') {
      buffer.write(r'\|');
      i++;
    } else if (line[i] == '|') {
      parts.add(buffer.toString());
      buffer.clear();
    } else {
      buffer.write(line[i]);
    }
  }
  parts.add(buffer.toString());
  return parts;
}

/// `ruff check --output-format=json` emits a JSON array of violations, each with `filename` and
/// `location`/`end_location` (1-based `row`/`column`). Ruff's JSON has no severity field -- every
/// entry is a lint violation, rendered as a warning (matching ruff's own default terminal
/// rendering, which doesn't distinguish severities either).
DiagnosticsByFile _parseRuffJson(String stdout, String rootPath) {
  final result = <String, List<Diagnostic>>{};
  final trimmed = stdout.trim();
  if (trimmed.isEmpty) return result;
  final decoded = jsonDecode(trimmed);
  if (decoded is! List) return result;
  for (final entry in decoded) {
    if (entry is! Map<String, dynamic>) continue;
    final filename = entry['filename'] as String?;
    if (filename == null) continue;
    final location = entry['location'] as Map<String, dynamic>?;
    final endLocation = entry['end_location'] as Map<String, dynamic>?;
    final startLine = (location?['row'] as num?)?.toInt() ?? 1;
    final startCol = (location?['column'] as num?)?.toInt() ?? 1;
    final endLine = (endLocation?['row'] as num?)?.toInt() ?? startLine;
    final endCol = (endLocation?['column'] as num?)?.toInt() ?? startCol;
    _add(
      result,
      _relativize(filename, rootPath),
      Diagnostic(
        range: EditorRange(startLine: startLine, startColumn: startCol, endLine: endLine, endColumn: endCol),
        severity: DiagnosticSeverity.warning,
        message: entry['message'] as String? ?? 'ruff violation',
        source: 'ruff',
        code: entry['code'] as String?,
      ),
    );
  }
  return result;
}

/// pyflakes has no JSON mode -- default text output is one line per issue:
/// `path:line:col: message` (older pyflakes versions omit `:col`, giving `path:line: message`).
final _pyflakesLine = RegExp(r'^(.+?):(\d+):(?:(\d+):)?\s*(.+)$');

DiagnosticsByFile _parsePyflakesText(String stdout, String rootPath) {
  final result = <String, List<Diagnostic>>{};
  for (final line in const LineSplitter().convert(stdout)) {
    final match = _pyflakesLine.firstMatch(line);
    if (match == null) continue;
    final lineNo = int.tryParse(match.group(2) ?? '') ?? 1;
    final col = int.tryParse(match.group(3) ?? '') ?? 1;
    _add(
      result,
      _relativize(match.group(1)!, rootPath),
      Diagnostic(
        range: EditorRange(startLine: lineNo, startColumn: col, endLine: lineNo, endColumn: col),
        severity: DiagnosticSeverity.warning,
        message: match.group(4) ?? line,
        source: 'pyflakes',
      ),
    );
  }
  return result;
}

/// `eslint -f json` emits an array of per-file result objects, each with a `messages` array;
/// `severity` is `2` (error) or `1` (warning) per ESLint's own convention.
DiagnosticsByFile _parseEslintJson(String stdout, String rootPath) {
  final result = <String, List<Diagnostic>>{};
  final trimmed = stdout.trim();
  if (trimmed.isEmpty) return result;
  final decoded = jsonDecode(trimmed);
  if (decoded is! List) return result;
  for (final fileResult in decoded) {
    if (fileResult is! Map<String, dynamic>) continue;
    final filePath = fileResult['filePath'] as String?;
    final messages = fileResult['messages'] as List?;
    if (filePath == null || messages == null) continue;
    for (final message in messages) {
      if (message is! Map<String, dynamic>) continue;
      final severity = (message['severity'] as num?)?.toInt() ?? 1;
      _add(
        result,
        _relativize(filePath, rootPath),
        Diagnostic(
          range: EditorRange(
            startLine: (message['line'] as num?)?.toInt() ?? 1,
            startColumn: (message['column'] as num?)?.toInt() ?? 1,
            endLine: (message['endLine'] as num?)?.toInt() ?? (message['line'] as num?)?.toInt() ?? 1,
            endColumn: (message['endColumn'] as num?)?.toInt() ?? (message['column'] as num?)?.toInt() ?? 1,
          ),
          severity: severity >= 2 ? DiagnosticSeverity.error : DiagnosticSeverity.warning,
          message: message['message'] as String? ?? 'eslint issue',
          source: 'eslint',
          code: message['ruleId'] as String?,
        ),
      );
    }
  }
  return result;
}
