/// Starting a command so that it can be stopped completely (G-16, SEC-09).
///
/// `Process.kill` signals one pid. The agent's commands run as `sh -c '<command>'`, and whatever
/// the shell starts -- a `&` background job, a dev server, a test runner's workers -- used to
/// survive a timeout or a cancel and keep running as the user, unseen. So on POSIX the command is
/// started as the leader of its own process group, and stopping it signals the whole group,
/// orphans the shell already left behind included; on Windows `taskkill /T` takes the process
/// tree, the same way `git_service.dart::_killTree` stops a hung network git.
library;

import 'dart:io';

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

class ProcessTree {
  ProcessTree._(this.process, this._ownGroup);

  final Process process;

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
    final process = await Process.start(
      effective.first,
      effective.sublist(1),
      workingDirectory: workingDirectory,
      environment: environment,
      includeParentEnvironment: false,
      // Only matters where there is no group leader to `exec` through PATH (Windows' `.cmd`
      // launchers such as `npx`).
      runInShell: runInShell && !group,
    );
    return ProcessTree._(process, group);
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
    await process.exitCode.timeout(const Duration(seconds: 5), onTimeout: () => -1);
  }
}
