/// The `aura_hostd` a packaged build ships next to this app's own executable (M8.10).
///
/// Before packaging existed, nobody could run the desktop Engineer agent without building the
/// daemon themselves and typing its path into Settings. `desktop-package.yml` now compiles
/// `aura_hostd` and puts it in the same directory as the app's executable -- the Windows release
/// folder the installer copies, and the root of the Linux bundle -- so that is where this looks.
/// A path the person saved in Settings still wins (`HostdExecutablePathNotifier`); this is only
/// the default.
library;

import 'dart:io';

/// The bundled daemon's path, or `null` when this build has none (a `flutter run` from source,
/// or a package built before bundling existed).
///
/// [appExecutable] and [exists] default to this process's own executable and the real
/// filesystem; tests pass their own.
String? bundledHostdPath({String? appExecutable, bool Function(String path)? exists}) {
  final executable = appExecutable ?? Platform.resolvedExecutable;
  final fileExists = exists ?? (String path) => File(path).existsSync();
  final dir = File(executable).parent.path;
  final name = Platform.isWindows ? 'aura_hostd.exe' : 'aura_hostd';
  final candidate = '$dir${Platform.pathSeparator}$name';
  return fileExists(candidate) ? candidate : null;
}
