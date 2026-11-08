/// What [HostdSupervisor] is doing with the `aura_hostd` process it started (M8.8, CB-26).
///
/// Plain Dart, shared by the io implementation and the web stub, so a screen can render the
/// supervisor's state without caring which one it got.
library;

enum HostdSupervisorPhase {
  /// Nothing started here, or it was stopped (by [HostdSupervisor.stop], or by the daemon
  /// exiting cleanly on its own -- `POST /shutdown`, SIGINT/SIGTERM -- which is never a crash).
  stopped,

  /// The process this supervisor started is alive.
  running,

  /// It crashed; a restart is scheduled after a backoff.
  restarting,

  /// It kept crashing, or exited in a way restarting cannot fix, so the supervisor gave up. The
  /// user has to look at the log and start it again.
  failed,
}

class HostdSupervisorState {
  const HostdSupervisorState({
    required this.phase,
    this.restarts = 0,
    this.lastExitCode,
    this.nextRestartIn,
    this.reason,
  });

  static const idle = HostdSupervisorState(phase: HostdSupervisorPhase.stopped);

  final HostdSupervisorPhase phase;

  /// Restarts since the last start that the user asked for.
  final int restarts;

  /// Exit code of the most recent unexpected exit, if there was one.
  final int? lastExitCode;

  /// While [HostdSupervisorPhase.restarting]: how long until the next attempt.
  final Duration? nextRestartIn;

  /// While [HostdSupervisorPhase.failed]: why the supervisor stopped trying.
  final String? reason;

  @override
  String toString() =>
      'HostdSupervisorState($phase, restarts: $restarts, lastExitCode: $lastExitCode, '
      'nextRestartIn: $nextRestartIn, reason: $reason)';
}
