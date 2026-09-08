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

/// Bounds how much of a command's output this daemon ever holds in memory or forwards over the
/// wire, independent of whatever cap the backend applies on its own end while draining a streamed
/// run (`aura_backend/aura_core/tools/coding.py::_RUN_COMMAND_STREAM_CAP_CHARS`). This is the
/// "enforce output caps... on the daemon side too, not only server-side" half of that job -- a
/// runaway command should not be able to grow this process's memory or flood the connection
/// before either side's truncation ever gets a chance to apply. Once reached, the process's
/// stdout/stderr are still drained (never reading them again would let the pipe fill and block
/// the child, recreating the same kind of hang the timeout-kill path already had to fix), just no
/// longer captured or forwarded.
const _maxOutputCapChars = 500000;

/// Called once per chunk of output as a running command produces it (`stream` being `"stdout"` or
/// `"stderr"`). Optional: [TaskHandler.run] behaves identically without one, just without giving
/// the caller a way to observe output before the command finishes.
typedef OutputChunkCallback = void Function(String stream, String data);

class TaskHandler {
  TaskHandler(this._roots, {this.yolo = false});

  final WorkspaceRoots _roots;
  /// Global YOLO: skip the allowlist for granted roots. Containment + hard denylist still apply.
  bool yolo;
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

  Future<Map<String, Object?>> run(
    Map<String, dynamic> params, {
    OutputChunkCallback? onChunk,
  }) async {
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
    final serverConfirmed = params['server_confirmed'] == true;
    final verdict = evaluateCommand(
      commandForPolicy,
      workspaceTrusted: root.trusted,
      yolo: yolo,
      serverConfirmed: serverConfirmed,
    );
    if (!verdict.isAllowed) {
      throw RpcError('command refused: ${verdict.reason}');
    }

    final timeout = Duration(
      milliseconds: (requestedTimeout * 1000).clamp(1000, _maxCommandTimeout.inMilliseconds).round(),
    );

    final effectiveArgv = _lineBuffered(commandArgv);
    final process = await Process.start(
      effectiveArgv.first,
      effectiveArgv.sublist(1),
      workingDirectory: cwd,
    );

    final stdoutBuf = StringBuffer();
    final stderrBuf = StringBuffer();
    var capturedChars = 0;
    var outputTruncated = false;

    void capture(String streamName, StringBuffer buf, String chunk) {
      if (capturedChars >= _maxOutputCapChars) {
        outputTruncated = true;
        return; // still drained below, just no longer kept or forwarded -- see field doc.
      }
      final remaining = _maxOutputCapChars - capturedChars;
      final piece = chunk.length > remaining ? chunk.substring(0, remaining) : chunk;
      buf.write(piece);
      capturedChars += piece.length;
      if (piece.isNotEmpty) onChunk?.call(streamName, piece);
      if (piece.length < chunk.length) outputTruncated = true;
    }

    final stdoutDone = process.stdout
        .transform(const SystemEncoding().decoder)
        .forEach((chunk) => capture('stdout', stdoutBuf, chunk));
    final stderrDone = process.stderr
        .transform(const SystemEncoding().decoder)
        .forEach((chunk) => capture('stderr', stderrBuf, chunk));

    int exitCode;
    var timedOut = false;
    try {
      exitCode = await process.exitCode.timeout(timeout, onTimeout: () {
        timedOut = true;
        process.kill(ProcessSignal.sigkill);
        return -1;
      });
    } finally {
      // Killing `process` only signals the PID we started -- `sh -c '<command>'` may have forked
      // rather than exec'd it (shell-dependent, not something this daemon controls), leaving an
      // orphaned descendant that still holds the stdout/stderr pipe fds open. Waiting on
      // stdoutDone/stderrDone unboundedly would then hang this whole call until that orphan exits
      // on its own -- exactly as long as whatever it was doing (observed: a killed `sleep 30`
      // held the pipes open for the full 30 seconds). Bounding this wait is the fix: a normally
      // terminating process's streams close well within this window, and a still-running orphan
      // does not get to stall the RPC response it's no longer even attached to.
      await Future.wait([stdoutDone, stderrDone]).timeout(
        const Duration(seconds: 2),
        onTimeout: () => const <void>[],
      );
    }

    if (timedOut) {
      stderrBuf.write('\n[aura_hostd] command killed after ${timeout.inSeconds}s timeout');
    }
    if (outputTruncated) {
      stderrBuf.write('\n[aura_hostd] output truncated after $_maxOutputCapChars characters');
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

bool? _stdbufAvailableCache;

/// Checked once (cheap insurance against a `PATH` lookup on every single command) and cached for
/// the daemon's lifetime -- `stdbuf` is not going to appear or disappear mid-run.
bool _stdbufAvailable() {
  final cached = _stdbufAvailableCache;
  if (cached != null) return cached;
  if (Platform.isWindows) {
    return _stdbufAvailableCache = false;
  }
  try {
    final result = Process.runSync('which', ['stdbuf']);
    return _stdbufAvailableCache = result.exitCode == 0;
  } catch (_) {
    return _stdbufAvailableCache = false;
  }
}

/// This is the daemon's PTY *approximation*, not a real one -- there is no raw terminal mode, no
/// resize, no job control, and no `isatty()` lie to the child process (a program that changes its
/// output format based on whether stdout is a terminal will still see a pipe and behave
/// accordingly). What it does provide: `stdbuf -oL -eL` forces line buffering instead of glibc's
/// default full buffering when stdout/stderr are not a terminal, so `run`'s streamed output
/// actually shows real incremental progress for common C-based CLI tools (git, grep, make, ...)
/// instead of one big flush at the end. It works through `sh -c '<command>'` because the
/// LD_PRELOAD environment variable `stdbuf` sets is inherited by the shell's own child process,
/// not because `sh` itself needs unbuffering. It does **not** help an interpreted runtime that
/// decides its own stdout buffering independent of libc's setvbuf (Python's own io layer is the
/// common case; `PYTHONUNBUFFERED=1` is that ecosystem's equivalent, and is not set here since
/// this daemon should not be guessing per-runtime environment variables on the user's behalf).
/// Falls back to the argv unchanged when `stdbuf` is not on `PATH` (any non-Linux machine without
/// GNU coreutils, most notably macOS's default install).
List<String> _lineBuffered(List<String> argv) {
  if (!_stdbufAvailable()) return argv;
  return ['stdbuf', '-oL', '-eL', ...argv];
}
