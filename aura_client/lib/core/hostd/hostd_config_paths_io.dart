/// Where `aura_hostd` (a separate process on the same machine, ADR-011) writes `control.json`
/// (`aura_hostd/lib/src/control_server.dart`) -- this must resolve to *exactly* the same
/// directory the daemon's own `resolveConfigDir()` (`aura_hostd/lib/src/config.dart`) does, or
/// this app will never find the control file. Ported rather than shared because `aura_hostd` is
/// deliberately not a dependency of this app (see its own pubspec doc comment on why the bar for
/// depending on it is high) -- keep the two in sync by hand if either changes.
///
/// Unlike the daemon's own version, this never falls back to creating a fresh temp directory
/// when `HOME`/`USERPROFILE` is unset: two independent processes calling
/// `Directory.systemTemp.createTempSync` would each get a *different* directory, defeating the
/// one thing this function exists for (both sides agreeing on one path). That fallback is
/// vanishingly unlikely on a real desktop session anyway; this side just reports "unknown"
/// instead of inventing a path nothing else will ever write to.
library;

import 'dart:io';

String? hostdConfigDirPath() {
  final env = Platform.environment;
  if (Platform.isWindows) {
    final appData = env['APPDATA'];
    if (appData != null && appData.isNotEmpty) return '$appData/aura_hostd';
  } else if (Platform.isMacOS) {
    final home = env['HOME'];
    if (home != null && home.isNotEmpty) return '$home/Library/Application Support/aura_hostd';
  } else {
    final xdg = env['XDG_CONFIG_HOME'];
    if (xdg != null && xdg.isNotEmpty) return '$xdg/aura_hostd';
  }
  final home = env['HOME'] ?? env['USERPROFILE'];
  if (home != null && home.isNotEmpty) return '$home/.aura_hostd';
  return null;
}

String? hostdControlFilePath() {
  final dir = hostdConfigDirPath();
  return dir == null ? null : '$dir/control.json';
}
