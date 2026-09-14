/// D7 Part A.2: process-based batch linting for the Desktop IDE's "Problems" tab.
///
/// Deliberately **not** an LSP client and **not** routed through `TaskHandler`'s agent-facing
/// `run` (which is gated by `command_policy`/the approval queue): a background lint scan on save
/// is a human/IDE convenience action, not an agent tool call, and routing it through the
/// agent-facing path would either spuriously trigger approval prompts on every save in an
/// untrusted workspace or require punching a policy hole. This follows `git_service.dart`'s own
/// established pattern instead: a fixed, hardcoded command allowlist run directly via
/// `ControlServer`, never an arbitrary client-supplied string -- the same "human-only boundary"
/// this daemon already draws around git.
library;

import 'dart:io';

import 'package:path/path.dart' as p;

/// Mirrors `task_handler.dart`'s `_maxOutputCapChars` discipline -- a lint run against a huge
/// codebase must not grow this process's memory unboundedly.
const _maxOutputChars = 500000;

/// The fixed, hardcoded allowlist. Adding a tool here is a conscious edit, not something a client
/// can request arbitrarily -- see this file's own doc comment on why that boundary matters.
const Map<String, List<String>> _toolCommands = {
  'dart_analyze': ['dart', 'analyze', '--format=machine'],
  'ruff': ['ruff', 'check', '--output-format=json', '.'],
  'pyflakes': ['python', '-m', 'pyflakes', '.'],
  'eslint': ['npx', 'eslint', '.', '-f', 'json'],
};

/// One tool's raw output from a single run -- parsing into `Diagnostic`s happens client-side
/// (`diagnostics_parser.dart` in `aura_client`), since the output format is tool-specific and the
/// daemon's job here is transport, not interpretation.
class DiagnosticsRunResult {
  const DiagnosticsRunResult({
    required this.tool,
    required this.exitCode,
    required this.stdout,
    required this.stderr,
    required this.truncated,
    required this.notFound,
  });

  final String tool;
  final int exitCode;
  final String stdout;
  final String stderr;
  final bool truncated;

  /// True when the tool's executable itself couldn't be found/run (e.g. `ruff` not installed) --
  /// distinct from the tool running and finding issues (a non-zero `exitCode` is normal for a
  /// linter that found problems).
  final bool notFound;
}

class DiagnosticsService {
  /// Picks the applicable tool from marker files already present in [root], the same
  /// marker-file-detection idiom the client's own language-mode detection uses
  /// (`flutter_code_editor_provider.dart`'s extension map). Returns `null` when nothing matches --
  /// the client renders an explicit "no diagnostics tool detected" state for that, never a
  /// silently-empty Problems list.
  String? detect(String root) {
    bool exists(String name) => File(p.join(root, name)).existsSync();

    if (exists('pubspec.yaml')) return 'dart_analyze';
    if (exists('pyproject.toml') || exists('ruff.toml') || exists('.ruff.toml')) return 'ruff';
    if (exists('requirements.txt') || exists('setup.py') || exists('setup.cfg')) return 'pyflakes';
    if (exists('package.json') &&
        (exists('.eslintrc.json') ||
            exists('.eslintrc.js') ||
            exists('.eslintrc.cjs') ||
            exists('.eslintrc') ||
            exists('eslint.config.js') ||
            exists('eslint.config.mjs'))) {
      return 'eslint';
    }
    return null;
  }

  /// Runs [tool] against [root]. Never throws for "tool not installed" -- that's a normal,
  /// expected outcome ([DiagnosticsRunResult.notFound]), not a server error.
  Future<DiagnosticsRunResult> run(String root, String tool) async {
    final argv = _toolCommands[tool];
    if (argv == null) {
      throw ArgumentError('unknown diagnostics tool "$tool"');
    }
    try {
      final result = await Process.run(
        argv.first,
        argv.sublist(1),
        workingDirectory: root,
        runInShell: true,
      );
      final rawStdout = result.stdout.toString();
      final rawStderr = result.stderr.toString();
      final truncated = rawStdout.length > _maxOutputChars || rawStderr.length > _maxOutputChars;
      return DiagnosticsRunResult(
        tool: tool,
        exitCode: result.exitCode,
        stdout: _cap(rawStdout),
        stderr: _cap(rawStderr),
        truncated: truncated,
        notFound: false,
      );
    } on ProcessException {
      return DiagnosticsRunResult(
        tool: tool,
        exitCode: -1,
        stdout: '',
        stderr: '',
        truncated: false,
        notFound: true,
      );
    }
  }

  String _cap(String s) => s.length > _maxOutputChars ? s.substring(0, _maxOutputChars) : s;
}
