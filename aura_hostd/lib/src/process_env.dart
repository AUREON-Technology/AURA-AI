/// The environment the agent's commands run with (G-06, SEC-04; owner decision D-06).
///
/// The daemon is started by the desktop app with the app's whole environment, which on a
/// developer's machine routinely holds `GITHUB_TOKEN`, cloud keys and API keys. A command the agent
/// runs used to inherit all of it, and its output now leaves the machine (D-02) -- so `printenv`
/// was enough to exfiltrate every one. Agent commands, and the diagnostics linters that load a
/// repository's own config, get only what an ordinary tool needs to start: where programs are,
/// who and where the user is, the locale, the terminal type and a temp directory; on Windows also
/// the system locations `cmd.exe`, Git for Windows' `sh.exe` and most runtimes need to work.
///
/// Anything else a command needs it must set itself, visibly, in the command line. The human's
/// own terminal (`terminal_session_manager.dart`) is not routed through this: it is the user's
/// shell, typed by the user.
library;

import 'dart:io';

/// Kept everywhere. `LC_*` is matched as a prefix (see [minimalEnvironment]).
const Set<String> _commonNames = {
  'PATH',
  'HOME',
  'USER',
  'LOGNAME',
  'LANG',
  'TERM',
  'TMPDIR',
  'TEMP',
  'TMP',
  'SHELL',
};

/// Also kept on Windows, where names are case-insensitive (compared upper-cased). `ProgramFiles*`
/// is matched as a prefix.
const Set<String> _windowsNames = {
  'USERPROFILE',
  'SYSTEMROOT',
  'SYSTEMDRIVE',
  'COMSPEC',
  'PATHEXT',
  'WINDIR',
  'APPDATA',
  'LOCALAPPDATA',
  'PROGRAMDATA',
  'PROGRAMW6432',
};

/// The allowlisted subset of [source] (normally `Platform.environment`), keeping each variable's
/// original spelling. [windows] defaults to the host platform; passing it lets the Windows rules be
/// checked on any host.
Map<String, String> minimalEnvironment(Map<String, String> source, {bool? windows}) {
  final isWindows = windows ?? Platform.isWindows;
  final result = <String, String>{};
  for (final MapEntry(:key, :value) in source.entries) {
    final name = isWindows ? key.toUpperCase() : key;
    final keep = _commonNames.contains(name) ||
        name.startsWith('LC_') ||
        (isWindows && (_windowsNames.contains(name) || name.startsWith('PROGRAMFILES')));
    if (keep) result[key] = value;
  }
  return result;
}
