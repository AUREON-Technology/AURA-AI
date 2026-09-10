/// Where `aura_hostd` (a separate process on the same machine, ADR-011 Unit 8) writes
/// `control.json` -- see `hostd_config_paths_io.dart` for the real logic and why it must mirror
/// the daemon's own `resolveConfigDir()` exactly. `dart:io`'s `Platform` isn't available on web,
/// so this follows the same conditional-export pattern `core/auth/desktop_oauth_client.dart`
/// already established: real logic behind `dart.library.ffi`, a stub (always `null`, since
/// `aura_hostd` doesn't exist as a concept on web) otherwise.
library;

export 'hostd_config_paths_stub.dart' if (dart.library.ffi) 'hostd_config_paths_io.dart';
