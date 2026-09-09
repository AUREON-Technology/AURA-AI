/// Spawns and supervises `aura_hostd run` as a child process (ADR-011 Unit 8) -- the first
/// process-supervision code in this app; there was no existing precedent to follow here.
///
/// Deliberately thin: this owns the `Process` handle only when *this app* started it. A daemon
/// someone started themselves from a terminal (or `auth pair`'d there first) is still fully
/// usable through `HostdControlClient` -- [isSpawnedHere] is false in that case, and the "stop"
/// action available to the UI should be [HostdControlClient.shutdown] (a graceful request the
/// control server answers) rather than anything here, which has no handle to kill.
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';

class HostdSupervisor {
  Process? _process;
  final StreamController<String> _logLines = StreamController<String>.broadcast();

  /// Emits one line at a time from the daemon's stdout and stderr, interleaved in arrival order.
  /// Only has anything to emit for a process this supervisor itself started.
  Stream<String> get output => _logLines.stream;

  bool get isSpawnedHere => _process != null;

  /// Starts `<executablePath> run`. Throws [ProcessException]/[FileSystemException] (uncaught,
  /// for the caller to turn into a UI-facing error) if [executablePath] doesn't exist or isn't
  /// executable -- there is no bundled `aura_hostd` binary to fall back to; the user configures
  /// this path themselves (see the settings field this feeds, `hostd_settings.dart`).
  Future<void> start(String executablePath) async {
    if (_process != null) return;
    final process = await Process.start(executablePath, const ['run']);
    _process = process;
    _pipe(process.stdout);
    _pipe(process.stderr);
    unawaited(process.exitCode.then((code) {
      _logLines.add('[aura_hostd exited with code $code]');
      _process = null;
    }));
  }

  void _pipe(Stream<List<int>> stream) {
    stream.transform(utf8.decoder).transform(const LineSplitter()).listen(
          _logLines.add,
          onError: (Object error) => _logLines.add('[log stream error: $error]'),
        );
  }

  /// Sends SIGTERM to a process this supervisor started. Does nothing for a daemon it doesn't
  /// own a handle to -- callers should prefer `HostdControlClient.shutdown()` for that case,
  /// which works regardless of who started the process.
  void stop() {
    _process?.kill(ProcessSignal.sigterm);
    _process = null;
  }

  void dispose() {
    stop();
    _logLines.close();
  }
}
