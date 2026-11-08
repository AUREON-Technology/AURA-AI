/// M8.8 / CB-26: the supervisor restarts `aura_hostd` when it crashes, and only then.
///
/// Before this, a daemon that died stayed dead until someone clicked Start, and every Engineer
/// tool call failed with "no desktop connected" in the meantime. These drive the supervisor with
/// a fake process and a hand-fired restart timer, so every exit code and backoff step is
/// deterministic. The last group kills a real process with SIGKILL -- the roadmap's
/// "kill-daemon" check -- on POSIX, where `kill -9` means what it says.
library;

import 'dart:async';
import 'dart:io';

import 'package:aura_client/core/hostd/hostd_supervisor_io.dart';
import 'package:flutter_test/flutter_test.dart';

class _FakeProcess implements Process {
  _FakeProcess(this.pid);

  @override
  final int pid;

  final Completer<int> _exit = Completer<int>();
  final StreamController<List<int>> _stdout = StreamController<List<int>>();
  final StreamController<List<int>> _stderr = StreamController<List<int>>();
  ProcessSignal? killedWith;

  @override
  Future<int> get exitCode => _exit.future;

  @override
  Stream<List<int>> get stdout => _stdout.stream;

  @override
  Stream<List<int>> get stderr => _stderr.stream;

  @override
  IOSink get stdin => throw UnimplementedError();

  @override
  bool kill([ProcessSignal signal = ProcessSignal.sigterm]) {
    killedWith = signal;
    return true;
  }

  void exit(int code) {
    _exit.complete(code);
    _stdout.close();
    _stderr.close();
  }
}

class _ScheduledRestart implements Timer {
  _ScheduledRestart(this.delay, this._callback);

  final Duration delay;
  final void Function() _callback;
  bool _cancelled = false;

  @override
  void cancel() => _cancelled = true;

  @override
  bool get isActive => !_cancelled;

  @override
  int get tick => 0;

  void fire() {
    if (!_cancelled) _callback();
  }
}

/// A supervisor wired to fakes: [processes] are what it has started, [scheduled] the restarts it
/// has asked for, [now] its clock.
class _Harness {
  _Harness({int maxRestarts = 5, Object? Function(int attempt)? failStart}) {
    supervisor = HostdSupervisor(
      maxRestarts: maxRestarts,
      startProcess: (executable, arguments) async {
        launches.add([executable, ...arguments]);
        final error = failStart?.call(launches.length);
        if (error != null) throw error;
        final process = _FakeProcess(1000 + launches.length);
        processes.add(process);
        return process;
      },
      schedule: (delay, callback) {
        final restart = _ScheduledRestart(delay, callback);
        scheduled.add(restart);
        return restart;
      },
      now: () => now,
    );
    supervisor.states.listen(states.add);
  }

  late final HostdSupervisor supervisor;
  final List<List<String>> launches = [];
  final List<_FakeProcess> processes = [];
  final List<_ScheduledRestart> scheduled = [];
  final List<HostdSupervisorState> states = [];
  DateTime now = DateTime(2026, 11, 8, 20);

  _FakeProcess get current => processes.last;

  /// Let the supervisor see an exit (it learns of it through `exitCode.then`).
  Future<void> crash(int code) async {
    current.exit(code);
    await pumpEventQueue();
  }

  Future<void> fireRestart() async {
    scheduled.last.fire();
    await pumpEventQueue();
  }
}

void main() {
  group('starting', () {
    test('runs `<path> run` and reports running', () async {
      final h = _Harness();

      await h.supervisor.start('/opt/aura/aura_hostd');

      expect(h.launches, [
        ['/opt/aura/aura_hostd', 'run'],
      ]);
      expect(h.supervisor.isSpawnedHere, isTrue);
      expect(h.supervisor.state.phase, HostdSupervisorPhase.running);
    });

    test('a failing first start throws to the caller and leaves nothing pending', () async {
      final h = _Harness(failStart: (_) => const ProcessException('/nope', ['run'], 'not found'));

      await expectLater(h.supervisor.start('/nope'), throwsA(isA<ProcessException>()));

      expect(h.supervisor.state.phase, HostdSupervisorPhase.stopped);
      expect(h.scheduled, isEmpty);
    });
  });

  group('a crash', () {
    test('is restarted after a backoff', () async {
      final h = _Harness();
      await h.supervisor.start('/opt/aura/aura_hostd');

      await h.crash(255);

      expect(h.supervisor.state.phase, HostdSupervisorPhase.restarting);
      expect(h.supervisor.state.lastExitCode, 255);
      expect(h.scheduled.single.delay, const Duration(seconds: 1));
      expect(h.supervisor.isSpawnedHere, isFalse);

      await h.fireRestart();

      expect(h.launches, hasLength(2));
      expect(h.supervisor.state.phase, HostdSupervisorPhase.running);
      expect(h.supervisor.state.restarts, 1);
    });

    test('a kill signal counts as a crash (negative exit code on POSIX)', () async {
      final h = _Harness();
      await h.supervisor.start('/opt/aura/aura_hostd');

      await h.crash(-9);

      expect(h.supervisor.state.phase, HostdSupervisorPhase.restarting);
    });

    test('backs off exponentially, capped at 30 s', () async {
      final h = _Harness(maxRestarts: 10);
      await h.supervisor.start('/opt/aura/aura_hostd');

      final delays = <int>[];
      for (var i = 0; i < 7; i++) {
        await h.crash(255);
        delays.add(h.scheduled.last.delay.inSeconds);
        await h.fireRestart();
      }

      expect(delays, [1, 2, 4, 8, 16, 30, 30]);
    });

    test('gives up after maxRestarts crashes in a row, and says why', () async {
      final h = _Harness(maxRestarts: 3);
      await h.supervisor.start('/opt/aura/aura_hostd');

      for (var i = 0; i < 3; i++) {
        await h.crash(255);
        await h.fireRestart();
      }
      await h.crash(255);

      expect(h.supervisor.state.phase, HostdSupervisorPhase.failed);
      expect(h.supervisor.state.reason, contains('crashed 3 times in a row'));
      expect(h.scheduled, hasLength(3), reason: 'no fourth restart is scheduled');
      expect(h.launches, hasLength(4));
    });

    test('a run that lasted long enough resets the crash count', () async {
      final h = _Harness(maxRestarts: 2);
      await h.supervisor.start('/opt/aura/aura_hostd');

      for (var i = 0; i < 5; i++) {
        h.now = h.now.add(const Duration(minutes: 5)); // each run was stable
        await h.crash(255);
        expect(h.supervisor.state.phase, HostdSupervisorPhase.restarting,
            reason: 'crash ${i + 1} days apart from the last must still be restarted');
        expect(h.scheduled.last.delay, const Duration(seconds: 1));
        await h.fireRestart();
      }
    });

    test('a restart that cannot spawn gives up rather than retrying the same path', () async {
      final h = _Harness(
        failStart: (attempt) => attempt == 2 ? const ProcessException('/gone', ['run'], 'gone') : null,
      );
      await h.supervisor.start('/opt/aura/aura_hostd');

      await h.crash(255);
      await h.fireRestart();

      expect(h.supervisor.state.phase, HostdSupervisorPhase.failed);
      expect(h.supervisor.state.reason, contains('could not restart'));
    });
  });

  group('a deliberate exit', () {
    test('exit 0 (POST /shutdown, SIGINT, SIGTERM) is never restarted', () async {
      final h = _Harness();
      await h.supervisor.start('/opt/aura/aura_hostd');

      await h.crash(0);

      expect(h.supervisor.state.phase, HostdSupervisorPhase.stopped);
      expect(h.scheduled, isEmpty);
    });

    test('exit 64, a usage error, gives up at once: restarting cannot fix it', () async {
      final h = _Harness();
      await h.supervisor.start('/opt/aura/aura_hostd');

      await h.crash(64);

      expect(h.supervisor.state.phase, HostdSupervisorPhase.failed);
      expect(h.supervisor.state.reason, contains('exit 64'));
      expect(h.scheduled, isEmpty);
    });

    test('stop() kills the process, and its late exit restarts nothing', () async {
      final h = _Harness();
      await h.supervisor.start('/opt/aura/aura_hostd');
      final process = h.current;

      h.supervisor.stop();
      await h.crash(-15);

      expect(process.killedWith, ProcessSignal.sigterm);
      expect(h.supervisor.state.phase, HostdSupervisorPhase.stopped);
      expect(h.scheduled, isEmpty);
    });

    test('stop() during a backoff cancels the pending restart', () async {
      final h = _Harness();
      await h.supervisor.start('/opt/aura/aura_hostd');
      await h.crash(255);

      h.supervisor.stop();
      await h.fireRestart();

      expect(h.launches, hasLength(1));
      expect(h.supervisor.state.phase, HostdSupervisorPhase.stopped);
    });
  });

  test('start() while a restart is pending starts now and resets the count', () async {
    final h = _Harness();
    await h.supervisor.start('/opt/aura/aura_hostd');
    await h.crash(255);

    await h.supervisor.start('/opt/aura/aura_hostd');
    await h.fireRestart(); // the cancelled one

    expect(h.launches, hasLength(2), reason: 'one start, not a start plus the stale restart');
    expect(h.supervisor.state.phase, HostdSupervisorPhase.running);
    expect(h.supervisor.state.restarts, 0);
  });

  group('a real process killed with SIGKILL', () {
    late Directory dir;

    setUp(() => dir = Directory.systemTemp.createTempSync('hostd_supervisor_test_'));
    tearDown(() => dir.deleteSync(recursive: true));

    test('is restarted as a new process', () async {
      // Stands in for `aura_hostd run`: ignores its argument and stays up until killed. `exec`
      // keeps the pid, so the supervisor's pid is the process we kill.
      final script = File('${dir.path}/fake_hostd')..writeAsStringSync('#!/bin/sh\nexec sleep 60\n');
      Process.runSync('chmod', ['+x', script.path]);
      final supervisor = HostdSupervisor();
      addTearDown(supervisor.dispose);
      final restarted = Completer<void>();
      supervisor.states.listen((state) {
        if (state.phase == HostdSupervisorPhase.running && state.restarts == 1 && !restarted.isCompleted) {
          restarted.complete();
        }
      });

      await supervisor.start(script.path);
      final first = supervisor.pid!;
      Process.killPid(first, ProcessSignal.sigkill);

      // The first backoff is 1 s.
      await restarted.future.timeout(const Duration(seconds: 10));

      final second = supervisor.pid;
      expect(second, isNotNull);
      expect(second, isNot(first), reason: 'a new process, not the killed one');
      expect(supervisor.isSpawnedHere, isTrue);

      supervisor.stop();
      expect(supervisor.pid, isNull);
    }, skip: Platform.isWindows ? 'SIGKILL is POSIX; the logic is covered above on every platform' : false);
  });
}
