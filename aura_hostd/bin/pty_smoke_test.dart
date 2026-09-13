/// Throwaway manual smoke test for [PtySession] -- D4 Stage 2 per
/// docs/architecture/015-desktop-ide.md. Not part of the daemon's real CLI;
/// deleted once TerminalSessionManager/control_server wiring is proven.
///
/// Must be run from a real interactive terminal (not an automation harness
/// whose process token lacks NT AUTHORITY\INTERACTIVE) -- ConPTY needs a
/// genuinely interactive session to keep the attached shell alive.
///
/// Spawns a real ConPTY-backed powershell.exe, has it write a marker file
/// (proof the shell actually executed the command, not just echoed it),
/// resizes once, then disposes cleanly.
library;

import 'dart:convert';
import 'dart:io';

import 'package:aura_hostd/src/pty_session.dart';

Future<void> main() async {
  final session = PtySession(
    sessionId: 'smoke-test',
    workingDirectory: Directory.current.path,
  );

  final markerPath = '${Directory.current.path}\\smoke_marker.txt';
  final markerFile = File(markerPath);
  if (markerFile.existsSync()) markerFile.deleteSync();

  print('[smoke] starting PtySession...');
  await session.start();
  print('[smoke] started, child alive: ${session.isChildAlive}');

  final sub = session.output.listen((chunk) {
    stdout.write(utf8.decode(chunk, allowMalformed: true));
  });

  await Future<void>.delayed(const Duration(seconds: 1));
  final cmd = 'Set-Content -Path "$markerPath" -Value hello-from-conpty\r';
  session.write(utf8.encode(cmd));

  await Future<void>.delayed(const Duration(seconds: 2));
  print('\n[smoke] child alive: ${session.isChildAlive}');
  print('[smoke] marker file exists? ${markerFile.existsSync()}'
      '${markerFile.existsSync() ? " -> content: ${markerFile.readAsStringSync().trim()}" : ""}');

  print('\n[smoke] resizing to 100x40...');
  session.resize(100, 40);
  await Future<void>.delayed(const Duration(seconds: 1));

  session.write(utf8.encode('exit\r'));
  final code = await session.exitCode.timeout(
    const Duration(seconds: 10),
    onTimeout: () {
      print('\n[smoke] exitCode did not complete in time, disposing anyway.');
      return -1;
    },
  );
  print('\n[smoke] session exited with code $code');

  await sub.cancel();
  await session.dispose();
  print('[smoke] disposed. Check `Get-Process powershell` -- this process should not be listed.');
}
