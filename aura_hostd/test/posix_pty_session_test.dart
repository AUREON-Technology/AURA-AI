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
  });
}
