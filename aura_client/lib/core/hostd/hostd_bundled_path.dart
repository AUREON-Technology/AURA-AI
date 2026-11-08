/// Where a packaged build of this app keeps its own `aura_hostd` (M8.10) -- see
/// `hostd_bundled_path_io.dart`. Same conditional-export pattern as `hostd_config_paths.dart`:
/// web has no native daemon, so its stub always answers `null`.
library;

export 'hostd_bundled_path_stub.dart' if (dart.library.ffi) 'hostd_bundled_path_io.dart';
