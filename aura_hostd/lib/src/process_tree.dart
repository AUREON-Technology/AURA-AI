/// Starting a command so that it can be stopped completely (G-16, SEC-09).
///
/// `Process.kill` signals one pid. The agent's commands run as `sh -c '<command>'`, and whatever
/// the shell starts -- a `&` background job, a dev server, a test runner's workers -- used to
/// survive a timeout or a cancel and keep running as the user, unseen. So on POSIX the command is
/// started as the leader of its own process group, and stopping it signals the whole group,
/// orphans the shell already left behind included; on Windows `taskkill /T` takes the process
/// tree, the same way `git_service.dart::_killTree` stops a hung network git.
library;

import 'dart:convert';
import 'dart:io';
import 'dart:math';

/// Makes itself a process-group leader, then becomes the real command (`exec` keeps the pid, so
/// the pid `Process.start` reports is the group id). Perl, because the daemon already depends on
/// it for the POSIX terminal (`posix_pty_session.dart`) and `setsid`(1) is not on macOS.
const String _perlGroupLeader = r'''
setpgrp(0, 0) or die "aura_hostd: setpgrp: $!\n";
exec { $ARGV[0] } @ARGV;
print STDERR "aura_hostd: could not execute '$ARGV[0]': $!\n";
exit 127;
''';

bool? _perlAvailableCache;

bool _perlAvailable() {
  final cached = _perlAvailableCache;
  if (cached != null) return cached;
  try {
    return _perlAvailableCache = Process.runSync('which', ['perl']).exitCode == 0;
  } on ProcessException {
    return _perlAvailableCache = false;
  }
}

/// The environment variable every command is started with, naming this one run. A process group is
/// left by `setsid`, by a daemonising server and by anything that double-forks, and the group kill
/// then misses it; the environment is inherited by every descendant however it got detached, so the
/// processes that still carry the name of a command we are stopping are the ones to stop.
const String commandMarkerVariable = 'AURA_HOSTD_COMMAND_ID';

final Random _markerRandom = Random.secure();

String _newMarker() =>
    base64Url.encode(List<int>.generate(12, (_) => _markerRandom.nextInt(256))).replaceAll('=', '');

/// Pids (other than this process) whose environment carries `NAME=VALUE`. Linux reads
/// `/proc/<pid>/environ` (same-user processes only, which is exactly what the daemon's commands
/// are); other POSIX systems ask `ps -E`. Best effort: a process that hides or scrubs its
/// environment is not found, and nothing here ever throws.
Future<List<int>> pidsCarryingMarker(String value) async {
  final needle = utf8.encode('$commandMarkerVariable=$value');
  final found = <int>[];
  try {
    if (Platform.isLinux) {
      await for (final entity in Directory('/proc').list(followLinks: false)) {
        final pid = int.tryParse(entity.uri.pathSegments.where((s) => s.isNotEmpty).last);
        if (pid == null || pid == _ownPid) continue;
        try {
          final bytes = await File('${entity.path}/environ').readAsBytes();
          if (_containsEntry(bytes, needle)) found.add(pid);
        } on Object {
          // Gone already, or not ours to read: not a process of ours either way.
        }
      }
    } else if (!Platform.isWindows) {
      final result = await Process.run('ps', ['-E', '-ww', '-A', '-o', 'pid=,command=']);
      for (final line in LineSplitter.split(result.stdout as String)) {
        if (!line.contains('$commandMarkerVariable=$value')) continue;
        final pid = int.tryParse(line.trim().split(RegExp(r'\s+')).first);
        if (pid != null && pid != _ownPid) found.add(pid);
      }
    }
  } on Object {
    // Best effort.
  }
  return found;
}

int get _ownPid => pid;

/// Whether the NUL-separated environment block [block] holds the exact entry [needle].
bool _containsEntry(List<int> block, List<int> needle) {
  var start = 0;
  for (var i = 0; i <= block.length; i++) {
    if (i == block.length || block[i] == 0) {
      if (i - start == needle.length) {
        var same = true;
        for (var j = 0; j < needle.length; j++) {
          if (block[start + j] != needle[j]) {
            same = false;
            break;
          }
        }
        if (same) return true;
      }
      start = i + 1;
    }
  }
  return false;
}

class ProcessTree {
  ProcessTree._(this.process, this._ownGroup, this._marker);

  final Process process;

  /// This run's value of [commandMarkerVariable].
  final String _marker;

  /// Whether [process] leads its own process group, so `kill(-pid)` reaches its descendants.
  /// False on Windows (where [kill] uses `taskkill /T` instead) and on a POSIX host without
  /// perl, where only the process itself can be signalled -- the old behaviour, kept rather than
  /// refusing to run anything.
  final bool _ownGroup;

  /// Starts [argv] in [workingDirectory] with exactly [environment] (never the daemon's own).
  /// Throws [ProcessException] like [Process.start] when [argv] cannot be started.
  static Future<ProcessTree> start(
    List<String> argv, {
    required String workingDirectory,
    required Map<String, String> environment,
    bool runInShell = false,
  }) async {
    final group = !Platform.isWindows && _perlAvailable();
    final effective = group ? ['perl', '-e', _perlGroupLeader, '--', ...argv] : argv;
    final marker = _newMarker();
    final process = await Process.start(
      effective.first,
      effective.sublist(1),
      workingDirectory: workingDirectory,
      environment: {...environment, commandMarkerVariable: marker},
      includeParentEnvironment: false,
      // Only matters where there is no group leader to `exec` through PATH (Windows' `.cmd`
      // launchers such as `npx`).
      runInShell: runInShell && !group,
    );
    return ProcessTree._(process, group, marker);
  }

  /// SIGKILLs the command and everything it started (see this file's doc), then waits briefly for
  /// the command itself to be reaped.
  Future<void> kill() async {
    if (Platform.isWindows) {
      try {
        await Process.run('taskkill', ['/F', '/T', '/PID', '${process.pid}'])
            .timeout(const Duration(seconds: 10));
      } on Object {
        // Fall through to the plain kill below; it at least ends the command itself.
      }
    } else if (_ownGroup) {
      Process.killPid(-process.pid, ProcessSignal.sigkill);
    }
    process.kill(ProcessSignal.sigkill);
    if (!Platform.isWindows) {
      // What left the process group (setsid, a daemonising server, a double fork) is still marked
      // with this run's name; stop those too.
      for (final stray in await pidsCarryingMarker(_marker)) {
        Process.killPid(stray, ProcessSignal.sigkill);
      }
    }
    await process.exitCode.timeout(const Duration(seconds: 5), onTimeout: () => -1);
  }
}
