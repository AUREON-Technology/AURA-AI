/// Real round-trip test for [PosixPtySession] -- the actual verification the 2026-09-25 audit
/// found missing entirely for any PTY code in this daemon (`hostd-ci.yml` ran `ubuntu-latest` but
/// nothing PTY-specific was ever exercised there; this file is the first). Skipped on Windows
/// (that platform has its own [PtySession]/ConPTY path, not this one) rather than skipped
/// everywhere the way the Windows-only junction test in containment_test.dart is mirrored --
/// `posix_openpt` needs no special privilege and works inside an ordinary CI container, so this
/// genuinely runs on `ubuntu-latest` rather than only ever running on a contributor's own machine.
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:aura_hostd/src/posix_pty_session.dart';
import 'package:test/test.dart';

void main() {
  group('PosixPtySession', () {
    test('runs a real shell command and captures its output', () async {
      if (Platform.isWindows) {
        return; // This backend is Linux/macOS-only; PtySession/ConPTY covers Windows separately.
      }

      final session = PosixPtySession(
        sessionId: 'test-session',
        workingDirectory: Directory.systemTemp.path,
        shellExecutable: '/bin/sh',
      );

      final chunks = <int>[];
      final gotMarker = Completer<void>();
      late final StreamSubscription<Uint8List> sub;
      sub = session.output.listen((chunk) {
        chunks.addAll(chunk);
        if (utf8.decode(chunks, allowMalformed: true).contains('AURA_PTY_MARKER_9f3a')) {
          if (!gotMarker.isCompleted) gotMarker.complete();
        }
      });

      try {
        await session.start();
        expect(session.pid, greaterThan(0));
        expect(session.isChildAlive, isTrue);

        session.write(utf8.encode('echo AURA_PTY_MARKER_9f3a\n'));
        await gotMarker.future.timeout(
          const Duration(seconds: 15),
          onTimeout: () => fail('did not see the echoed marker within 15s'),
        );

        session.write(utf8.encode('exit\n'));
        final code = await session.exitCode.timeout(
          const Duration(seconds: 15),
          onTimeout: () => fail('shell did not exit within 15s'),
        );
        expect(code, 0);
        expect(session.isChildAlive, isFalse);
      } finally {
        await sub.cancel();
        await session.dispose();
      }
    }, timeout: const Timeout(Duration(seconds: 30)));

    test('resize does not throw on a live session', () async {
      if (Platform.isWindows) return;

      final session = PosixPtySession(
        sessionId: 'test-resize',
        workingDirectory: Directory.systemTemp.path,
        shellExecutable: '/bin/sh',
      );
      try {
        await session.start();
        session.resize(120, 40);
      } finally {
        await session.dispose();
      }
    }, timeout: const Timeout(Duration(seconds: 15)));

    // The hang that kept `dart test` from ever exiting on ubuntu-latest: disposing before the Perl
    // helper had opened the slave left the reader isolate blocked in read() on a master that never
    // sees a hangup. dispose() now waits for the reader to acknowledge its wake byte (up to 5s, then
    // gives up), so a dispose that returns well inside that window proves the reader really stopped.
    // Repeated because the window it guards is a race against the helper's startup.
    test('dispose straight after start stops the reader promptly, every time', () async {
      if (Platform.isWindows) return;

      for (var i = 0; i < 10; i++) {
        final session = PosixPtySession(
          sessionId: 'test-quick-dispose-$i',
          workingDirectory: Directory.systemTemp.path,
          shellExecutable: '/bin/sh',
        );
        final outputDone = Completer<void>();
        final sub = session.output.listen((_) {}, onDone: outputDone.complete);
        try {
          await session.start();
          final pid = session.pid;
          final stopwatch = Stopwatch()..start();
          await session.dispose();
          stopwatch.stop();

          expect(stopwatch.elapsed, lessThan(const Duration(seconds: 3)),
              reason: 'iteration $i: dispose waited on a reader that did not stop');
          expect(outputDone.isCompleted, isTrue, reason: 'iteration $i: output stream left open');
          expect(session.isChildAlive, isFalse);
          expect(await session.exitCode.timeout(const Duration(seconds: 5)), isNot(0));
          expect(Process.killPid(pid, ProcessSignal.sigcont), isFalse,
              reason: 'iteration $i: child $pid still exists after dispose');
        } finally {
          await sub.cancel();
          await session.dispose();
        }
      }
    }, timeout: const Timeout(Duration(seconds: 60)));

    test('dispose while a command is running stops the reader and kills the shell', () async {
      if (Platform.isWindows) return;

      final session = PosixPtySession(
        sessionId: 'test-dispose-busy',
        workingDirectory: Directory.systemTemp.path,
        shellExecutable: '/bin/sh',
      );
      final chunks = <int>[];
      final sleeping = Completer<void>();
      final sub = session.output.listen((chunk) {
        chunks.addAll(chunk);
        if (utf8.decode(chunks, allowMalformed: true).contains('AURA_PTY_SLEEPING_51c2')) {
          if (!sleeping.isCompleted) sleeping.complete();
        }
      });
      try {
        await session.start();
        // `printf` with the marker split by an escape, so the terminal's echo of the typed line
        // cannot satisfy the match on its own -- only the command's own output can.
        session.write(utf8.encode("printf 'AURA_PTY_SLEEPING_\\0651c2\\n'; sleep 300\n"));
        await sleeping.future.timeout(const Duration(seconds: 15));

        final stopwatch = Stopwatch()..start();
        await session.dispose();
        expect(stopwatch.elapsed, lessThan(const Duration(seconds: 3)));
        expect(session.isChildAlive, isFalse);
      } finally {
        await sub.cancel();
        await session.dispose();
      }
    }, timeout: const Timeout(Duration(seconds: 30)));

    test('output closes on its own once the shell exits, before any dispose', () async {
      if (Platform.isWindows) return;

      final session = PosixPtySession(
        sessionId: 'test-natural-exit',
        workingDirectory: Directory.systemTemp.path,
        shellExecutable: '/bin/sh',
      );
      final outputDone = Completer<void>();
      final sub = session.output.listen((_) {}, onDone: outputDone.complete);
      try {
        await session.start();
        session.write(utf8.encode('exit 3\n'));
        expect(await session.exitCode.timeout(const Duration(seconds: 15)), 3);
        await outputDone.future.timeout(const Duration(seconds: 15));
      } finally {
        await sub.cancel();
        await session.dispose();
      }
    }, timeout: const Timeout(Duration(seconds: 30)));

    test('the pty master is not inherited by the shell', () async {
      // /proc/<pid>/fd is Linux-only; the FD_CLOEXEC call it checks is the same on macOS.
      if (!Platform.isLinux) return;

      final session = PosixPtySession(
        sessionId: 'test-cloexec',
        workingDirectory: Directory.systemTemp.path,
        shellExecutable: '/bin/sh',
      );
      final chunks = <int>[];
      final listed = Completer<void>();
      final sub = session.output.listen((chunk) {
        chunks.addAll(chunk);
        if (utf8.decode(chunks, allowMalformed: true).contains('AURA_FD_LIST_END_7d0e')) {
          if (!listed.isCompleted) listed.complete();
        }
      });
      try {
        await session.start();
        session.write(utf8.encode(
            "ls -l /proc/\$\$/fd; printf 'AURA_FD_LIST_END_\\067d0e\\n'\n"));
        await listed.future.timeout(const Duration(seconds: 15));
        final listing = utf8.decode(chunks, allowMalformed: true);
        expect(listing, contains('/dev/pts/'), reason: 'expected the shell to hold its slave');
        expect(listing, isNot(contains('ptmx')),
            reason: 'the shell inherited the pty master fd:\n$listing');
      } finally {
        await sub.cancel();
        await session.dispose();
      }
    }, timeout: const Timeout(Duration(seconds: 30)));
  });
}
