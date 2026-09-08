/// Implements the RPC methods the backend's `HostAgentSandboxRunner`
/// (`aura_backend/aura_core/adapters/host_agent.py`) calls: `create_task`, `run`, `write_file`,
/// `read_file`. Every filesystem/command operation goes through [WorkspaceRoots] for containment
/// before touching disk -- this class has no path-handling logic of its own that bypasses it.
///
/// A "task" here does not own a single workspace root the way a Docker sandbox owns one
/// container: paths arrive workspace-relative from the tool layer's point of view, so each call
/// resolves against whichever granted root actually contains it (`findContainingRoot`). A path
/// outside every granted root is refused outright, matching ADR-011's "never merely confirmable."
library;

import 'dart:convert';
import 'dart:io';

import 'command_policy.dart';
import 'containment.dart';

class RpcError implements Exception {
  RpcError(this.message);
  final String message;

  @override
  String toString() => message;
}

/// How long a command may run before this side kills it -- a hard ceiling under whatever
/// `timeout_s` the tool call requested, in case the caller asks for something unreasonable.
const _maxCommandTimeout = Duration(minutes: 15);

class TaskHandler {
  TaskHandler(this._roots);

  final WorkspaceRoots _roots;
  final Set<String> _activeTaskIds = {};

  Future<Map<String, Object?>> createTask(Map<String, dynamic> params) async {
    final taskId = 't_${DateTime.now().microsecondsSinceEpoch}_${_activeTaskIds.length}';
    _activeTaskIds.add(taskId);
    return {'task_id': taskId};
  }

  Future<Map<String, Object?>> writeFile(Map<String, dynamic> params) async {
    final path = _requireString(params, 'path');
    final content = _requireString(params, 'content');
    final root = _roots.findContainingRoot(path);
    if (root == null) {
      throw RpcError(
        'no granted workspace root contains "$path" -- grant it (or its parent project folder) '
        'first',
      );
    }
    final resolved = _roots.requireContained(root, path);
    final file = File(resolved);
    file.parent.createSync(recursive: true);
    file.writeAsStringSync(content);
    return {'bytes_written': utf8.encode(content).length};
  }

  Future<Map<String, Object?>> readFile(Map<String, dynamic> params) async {
    final path = _requireString(params, 'path');
    final root = _roots.findContainingRoot(path);
    if (root == null) {
      throw RpcError('no granted workspace root contains "$path"');
    }
    final resolved = _roots.requireContained(root, path);
    final file = File(resolved);
    if (!file.existsSync()) {
      throw RpcError('$path does not exist');
    }
    return {'content': file.readAsStringSync()};
  }

  Future<Map<String, Object?>> run(Map<String, dynamic> params) async {
    // The caller (`aura_backend/aura_core/tools/coding.py::run_command`) always sends
    // `["sh", "-c", "<shell command>"]` -- a full argv, not a bare string -- so it can be
    // `Process.start`ed directly without this daemon re-interpreting it through another shell.
    // Tolerate other argv shapes too (a future caller might not go through `sh -c`) by falling
    // back to running the argv's first element as the program.
    final commandArgv = _requireStringList(params, 'command');
    if (commandArgv.isEmpty) {
      throw RpcError('"command" must be a non-empty list of arguments');
    }
    final cwdParam = params['cwd'] as String?; // not sent by the current backend; tolerated
    final requestedTimeout = (params['timeout_s'] as num?)?.toDouble() ?? 60.0;

    final root = cwdParam != null
        ? _roots.findContainingRoot(cwdParam)
        : (_roots.all.isNotEmpty ? _roots.all.first : null);
    if (root == null) {
      throw RpcError(
        cwdParam != null
            ? 'no granted workspace root contains "$cwdParam"'
            : 'no workspace root is granted -- run `aura_hostd grant <path>` first',
      );
    }
    final cwd = cwdParam != null ? _roots.requireContained(root, cwdParam) : root.canonicalPath;

    final commandForPolicy = _effectiveShellCommand(commandArgv);
    final verdict = evaluateCommand(commandForPolicy, workspaceTrusted: root.trusted);
    if (!verdict.isAllowed) {
      throw RpcError('command refused: ${verdict.reason}');
    }

    final timeout = Duration(
      milliseconds: (requestedTimeout * 1000).clamp(1000, _maxCommandTimeout.inMilliseconds).round(),
    );

    final process = await Process.start(
      commandArgv.first,
      commandArgv.sublist(1),
      workingDirectory: cwd,
    );

    final stdoutBuf = StringBuffer();
    final stderrBuf = StringBuffer();
    final stdoutDone = process.stdout.transform(const SystemEncoding().decoder).forEach(stdoutBuf.write);
    final stderrDone = process.stderr.transform(const SystemEncoding().decoder).forEach(stderrBuf.write);

    int exitCode;
    var timedOut = false;
    try {
      exitCode = await process.exitCode.timeout(timeout, onTimeout: () {
        timedOut = true;
        process.kill(ProcessSignal.sigkill);
        return -1;
      });
    } finally {
      await Future.wait([stdoutDone, stderrDone]);
    }

    if (timedOut) {
      stderrBuf.write('\n[aura_hostd] command killed after ${timeout.inSeconds}s timeout');
    }

    return {
      'stdout': stdoutBuf.toString(),
      'stderr': stderrBuf.toString(),
      'exit_code': exitCode,
    };
  }

  String _requireString(Map<String, dynamic> params, String key) {
    final value = params[key];
    if (value is! String) {
      throw RpcError('missing or invalid "$key" parameter');
    }
    return value;
  }

  List<String> _requireStringList(Map<String, dynamic> params, String key) {
    final value = params[key];
    if (value is! List) {
      throw RpcError('missing or invalid "$key" parameter');
    }
    return value.map((e) => e.toString()).toList(growable: false);
  }
}

/// `["sh", "-c", "<command>"]` / `["cmd.exe", "/c", "<command>"]` -> `"<command>"`, so command
/// policy evaluates the actual shell command the user's tool call asked for rather than the shell
/// wrapper around it. Any other shape is joined with spaces as a best-effort fallback -- it will
/// not match the allowlist, which fails safe (denied, not silently allowed).
String _effectiveShellCommand(List<String> argv) {
  final looksLikeShellWrapper = argv.length == 3 &&
      (argv[0] == 'sh' || argv[0] == 'bash' || argv[0] == 'cmd.exe') &&
      (argv[1] == '-c' || argv[1] == '/c');
  return looksLikeShellWrapper ? argv[2] : argv.join(' ');
}
