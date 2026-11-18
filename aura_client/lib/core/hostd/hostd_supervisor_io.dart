/// Spawns and supervises `aura_hostd run` as a child process (ADR-011 Unit 8; restart on crash is
/// M8.8 / CB-26).
///
/// Owns the `Process` handle only when *this app* started it. A daemon someone started themselves
/// from a terminal (or `auth pair`'d there first) is still fully usable through
/// `HostdControlClient` -- [isSpawnedHere] is false in that case, and the "stop" action available
/// to the UI should be [HostdControlClient.shutdown] (a graceful request the control server
/// answers) rather than anything here, which has no handle to kill.
///
/// **Restart on crash.** Before this, a daemon that died stayed dead until someone noticed and
/// clicked Start: every Engineer tool call failed in the meantime with "no desktop connected".
/// Now an unexpected exit is restarted after an exponential backoff (1 s, 2 s, 4 s ... capped at
/// [maxBackoff]). What counts as unexpected follows from how `aura_hostd run` exits
/// (`bin/aura_hostd.dart`):
///
/// * **0** is always deliberate -- `POST /shutdown`, SIGINT, SIGTERM. Never restarted.
/// * **64** is a usage error: the configured executable rejected `run`. Restarting cannot fix
///   that, so the supervisor gives up at once.
/// * **Anything else** -- an unhandled exception (255), a kill signal (negative on POSIX), a
///   native crash -- is restarted.
///
/// A daemon that keeps dying (a port already in use, a corrupt config) must not restart forever:
/// after [maxRestarts] crashes in a row the supervisor gives up and says so. A run that stayed up
/// for [stableAfter] resets that count, so an occasional crash days apart is always restarted.
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math' as math;

import 'package:aura_client/core/hostd/hostd_supervisor_state.dart';
import 'package:aura_client/l10n/l10n.dart';

export 'package:aura_client/core/hostd/hostd_supervisor_state.dart';

/// Starts a process. [Process.start] in production; a fake in tests.
typedef HostdProcessStarter = Future<Process> Function(String executable, List<String> arguments);

/// Schedules [callback] after [delay]. [Timer.new] in production; tests fire it by hand.
typedef HostdRestartScheduler = Timer Function(Duration delay, void Function() callback);

class HostdSupervisor {
  HostdSupervisor({
    HostdProcessStarter? startProcess,
    HostdRestartScheduler? schedule,
    DateTime Function()? now,
    this.maxRestarts = 5,
    this.stableAfter = const Duration(seconds: 30),
    this.maxBackoff = const Duration(seconds: 30),
  })  : _startProcess = startProcess ?? ((exe, args) => Process.start(exe, args)),
        _schedule = schedule ?? Timer.new,
        _now = now ?? DateTime.now;

  /// Crashes in a row (without a run lasting [stableAfter]) before the supervisor gives up.
  final int maxRestarts;

  /// How long a run must last for its crash to count as a fresh start rather than part of a loop.
  final Duration stableAfter;

  /// The longest wait between restarts.
  final Duration maxBackoff;

  final HostdProcessStarter _startProcess;
  final HostdRestartScheduler _schedule;
  final DateTime Function() _now;

  final StreamController<String> _logLines = StreamController<String>.broadcast();
  final StreamController<HostdSupervisorState> _states =
      StreamController<HostdSupervisorState>.broadcast();

  Process? _process;
  String? _executable;
  DateTime? _startedAt;
  Timer? _pendingRestart;

  /// Whether the user wants the daemon running. Only a [start] sets it; [stop], a clean exit and
  /// giving up clear it.
  bool _wanted = false;

  /// Crashes since the last run that lasted [stableAfter].
  int _crashesInARow = 0;

  /// Restarts since the user last pressed Start.
  int _restarts = 0;

  /// Bumped for every process started and on [stop], so a late exit of a process this supervisor
  /// no longer cares about cannot trigger a restart.
  int _generation = 0;

  HostdSupervisorState _state = HostdSupervisorState.idle;

  /// Emits one line at a time from the daemon's stdout and stderr, interleaved in arrival order,
  /// plus a line for each exit and restart. Only has anything to emit for a process this
  /// supervisor itself started.
  Stream<String> get output => _logLines.stream;

  /// The current state, and every change to it.
  HostdSupervisorState get state => _state;
  Stream<HostdSupervisorState> get states => _states.stream;

  bool get isSpawnedHere => _process != null;

  /// The pid of the process this supervisor is running now, if any. A restart gives a new one.
  int? get pid => _process?.pid;

  /// Starts `<executablePath> run`. Throws [ProcessException]/[FileSystemException] (uncaught,
  /// for the caller to turn into a UI-facing error) if [executablePath] doesn't exist or isn't
  /// executable -- there is no bundled `aura_hostd` binary to fall back to; the user configures
  /// this path themselves (see the settings field this feeds, `hostd_settings.dart`).
  ///
  /// Also the way out of [HostdSupervisorPhase.restarting] and [HostdSupervisorPhase.failed]: a
  /// pending restart is cancelled and the daemon is started now, with the crash count reset.
  Future<void> start(String executablePath) async {
    if (_process != null) return;
    _pendingRestart?.cancel();
    _pendingRestart = null;
    _executable = executablePath;
    _crashesInARow = 0;
    _restarts = 0;
    _wanted = true;
    try {
      await _spawn();
    } catch (_) {
      _wanted = false;
      _emit(HostdSupervisorState.idle);
      rethrow;
    }
  }

  Future<void> _spawn() async {
    final process = await _startProcess(_executable!, const ['run']);
    final generation = ++_generation;
    _process = process;
    _startedAt = _now();
    _pipe(process.stdout);
    _pipe(process.stderr);
    _emit(HostdSupervisorState(phase: HostdSupervisorPhase.running, restarts: _restarts));
    unawaited(process.exitCode.then((code) {
      _log('[aura_hostd exited with code $code]');
      if (generation == _generation) _onExit(code);
    }));
  }

  void _onExit(int code) {
    _process = null;
    if (!_wanted) {
      _emit(HostdSupervisorState.idle);
      return;
    }
    if (code == 0) {
      // A deliberate shutdown (POST /shutdown, SIGINT, SIGTERM) -- whoever asked meant it.
      _wanted = false;
      _emit(HostdSupervisorState.idle);
      return;
    }
    if (code == 64) {
      _giveUp(code, currentL10n.coreHostdRejectedArguments);
      return;
    }
    final startedAt = _startedAt;
    if (startedAt != null && _now().difference(startedAt) >= stableAfter) {
      _crashesInARow = 0;
    }
    _crashesInARow++;
    if (_crashesInARow > maxRestarts) {
      _giveUp(code, currentL10n.coreHostdCrashedRepeatedly(maxRestarts));
      return;
    }
    final delay = _backoff(_crashesInARow);
    _log('[restarting aura_hostd in ${delay.inSeconds}s]');
    _emit(HostdSupervisorState(
      phase: HostdSupervisorPhase.restarting,
      restarts: _restarts,
      lastExitCode: code,
      nextRestartIn: delay,
    ));
    _pendingRestart = _schedule(delay, () => unawaited(_restart(code)));
  }

  Future<void> _restart(int lastExitCode) async {
    _pendingRestart = null;
    if (!_wanted || _process != null) return;
    _restarts++;
    try {
      await _spawn();
    } catch (error) {
      // The binary moved, lost its permissions, or similar: retrying the same path will not
      // help, and the user has to fix the setting.
      _log('[could not restart aura_hostd: $error]');
      _giveUp(lastExitCode, currentL10n.coreHostdRestartFailed('$error'));
    }
  }

  Duration _backoff(int crash) {
    final seconds = math.pow(2, crash - 1).toInt();
    final capped = math.min(seconds, maxBackoff.inSeconds);
    return Duration(seconds: capped);
  }

  void _giveUp(int code, String reason) {
    _wanted = false;
    _log('[$reason]');
    _emit(HostdSupervisorState(
      phase: HostdSupervisorPhase.failed,
      restarts: _restarts,
      lastExitCode: code,
      reason: reason,
    ));
  }

  void _pipe(Stream<List<int>> stream) {
    stream.transform(utf8.decoder).transform(const LineSplitter()).listen(
          _log,
          onError: (Object error) => _log('[log stream error: $error]'),
        );
  }

  void _log(String line) {
    if (!_logLines.isClosed) _logLines.add(line);
  }

  void _emit(HostdSupervisorState next) {
    _state = next;
    if (!_states.isClosed) _states.add(next);
  }

  /// Sends SIGTERM to a process this supervisor started, and cancels any pending restart. Does
  /// nothing for a daemon it doesn't own a handle to -- callers should prefer
  /// `HostdControlClient.shutdown()` for that case, which works regardless of who started the
  /// process.
  void stop() {
    _wanted = false;
    _pendingRestart?.cancel();
    _pendingRestart = null;
    _generation++;
    _process?.kill(ProcessSignal.sigterm);
    _process = null;
    _emit(HostdSupervisorState.idle);
  }

  void dispose() {
    stop();
    _logLines.close();
    _states.close();
  }
}
