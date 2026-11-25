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
///
/// "Human-only" is not "safe in any folder", though (G-17, SEC-10): a linter loads the
/// repository's own configuration and plugins (`eslint.config.js` is JavaScript; ruff and pyflakes
/// import plugins), so running one is running repository-supplied code. It therefore runs only in a
/// trusted folder, with a timeout, and with the same minimal environment as the agent's commands.
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:path/path.dart' as p;

import 'process_env.dart';
import 'process_tree.dart';

/// Mirrors `task_handler.dart`'s `_maxOutputCapChars` discipline -- a lint run against a huge
/// codebase must not grow this process's memory unboundedly.
const _maxOutputChars = 500000;

/// The fixed, hardcoded allowlist. Adding a tool here is a conscious edit, not something a client
/// can request arbitrarily -- see this file's own doc comment on why that boundary matters.
const Map<String, List<String>> _defaultToolCommands = {
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
    this.timedOut = false,
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

  /// True when the tool ran past [DiagnosticsService.timeout] and was killed (G-17).
  final bool timedOut;
}

/// Thrown by [DiagnosticsService.run] for a folder the user has not trusted (G-17, SEC-10).
class DiagnosticsNotTrustedError implements Exception {
  DiagnosticsNotTrustedError(this.root);
  final String root;

  @override
  String toString() => 'diagnostics run only in a trusted folder -- a linter loads the '
      "repository's own config and plugins; trust $root to run them";
}

class DiagnosticsService {
  /// [toolCommands] and [parentEnvironment] exist for tests (a stand-in tool, a planted secret);
  /// production uses the fixed allowlist above and the daemon's own environment, filtered.
  DiagnosticsService({
    this.timeout = const Duration(minutes: 2),
    Map<String, List<String>>? toolCommands,
    Map<String, String>? parentEnvironment,
  })  : _toolCommands = toolCommands ?? _defaultToolCommands,
        _parentEnvironment = parentEnvironment ?? Platform.environment;

  /// How long one lint run may take before it and everything it started are killed.
  final Duration timeout;
  final Map<String, List<String>> _toolCommands;
  final Map<String, String> _parentEnvironment;

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
  /// expected outcome ([DiagnosticsRunResult.notFound]), not a server error. Throws
  /// [DiagnosticsNotTrustedError] unless [trusted] (checked after the tool name, so an unknown tool
  /// is still reported as such).
  Future<DiagnosticsRunResult> run(String root, String tool, {required bool trusted}) async {
    final argv = _toolCommands[tool];
    if (argv == null) {
      throw ArgumentError('unknown diagnostics tool "$tool"');
    }
    if (!trusted) throw DiagnosticsNotTrustedError(root);
    try {
      final tree = await ProcessTree.start(
        argv,
        workingDirectory: root,
        environment: minimalEnvironment(_parentEnvironment),
        // Windows' `npx` is a `.cmd` launcher only a shell finds.
        runInShell: true,
      );
      final stdoutText = tree.process.stdout.transform(const Utf8Decoder(allowMalformed: true)).join();
      final stderrText = tree.process.stderr.transform(const Utf8Decoder(allowMalformed: true)).join();
      var timedOut = false;
      final exitCode = await tree.process.exitCode.timeout(timeout, onTimeout: () async {
        timedOut = true;
        await tree.kill();
        return -1;
      });
      final output = await Future.wait([stdoutText, stderrText]).timeout(
        const Duration(seconds: 2),
        onTimeout: () => const ['', ''],
      );
      final rawStdout = output[0];
      final rawStderr = timedOut
          ? '${output[1]}\n[aura_hostd] $tool killed after ${timeout.inSeconds}s timeout'
          : output[1];
      final truncated = rawStdout.length > _maxOutputChars || rawStderr.length > _maxOutputChars;
      return DiagnosticsRunResult(
        tool: tool,
        exitCode: exitCode,
        stdout: _cap(rawStdout),
        stderr: _cap(rawStderr),
        truncated: truncated,
        notFound: false,
        timedOut: timedOut,
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
