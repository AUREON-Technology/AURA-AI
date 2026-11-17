/// The few Desktop IDE behaviours that differ between Windows and Linux (WP7, D-08b).
///
/// Each takes the platform explicitly so it can be tested on any host; callers pass
/// `defaultTargetPlatform`. Everything else in the IDE is the same on both.
library;

import 'package:flutter/foundation.dart';
import 'package:path/path.dart' as p;
import 'package:xterm/xterm.dart' as xterm;

/// The emulator mode for the IDE terminal. Windows terminals sit on ConPTY; on Linux aura_hostd
/// runs a POSIX PTY (`$SHELL`, else `/bin/bash`), which expects Linux key sequences.
xterm.TerminalTargetPlatform ideTerminalTargetPlatform(TargetPlatform platform) =>
    platform == TargetPlatform.linux ? xterm.TerminalTargetPlatform.linux : xterm.TerminalTargetPlatform.windows;

/// The line typed into an already-open terminal to move it to [dir].
///
/// The Windows terminal is PowerShell (aura_hostd's `PtySession` runs `powershell.exe`), whose
/// single-quoted strings escape an embedded quote by doubling it. A POSIX shell has no escape
/// inside single quotes at all: the quote is closed, an escaped quote added, and it is reopened.
/// Both forms keep `$`, backticks and spaces literal.
String ideTerminalCdCommand(String dir, TargetPlatform platform) {
  final quoted = platform == TargetPlatform.windows
      ? "'${dir.replaceAll("'", "''")}'"
      : "'${dir.replaceAll("'", r"'\''")}'";
  return 'cd $quoted\r';
}

/// The program and arguments that show [path] in the system file manager, or `null` where the
/// IDE does not run. Explorer can highlight a file in its folder; `xdg-open` cannot, so on Linux
/// a file reveals its parent folder. Always an argv list, never a shell string.
(String, List<String>)? ideRevealCommand(
  String path, {
  required bool isDirectory,
  required TargetPlatform platform,
}) {
  switch (platform) {
    case TargetPlatform.windows:
      return ('explorer', isDirectory ? [path] : ['/select,', path]);
    case TargetPlatform.linux:
      return ('xdg-open', [isDirectory ? path : p.posix.dirname(path)]);
    default:
      return null;
  }
}
