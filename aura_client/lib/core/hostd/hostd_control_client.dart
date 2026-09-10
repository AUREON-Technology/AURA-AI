/// HTTP client for `aura_hostd`'s local control server (ADR-011 Unit 8). See
/// `hostd_control_client_io.dart` for the real implementation and why the web build gets a stub
/// instead (no native process to supervise there) -- same conditional-export pattern as
/// `hostd_config_paths.dart`.
library;

export 'hostd_control_client_stub.dart' if (dart.library.ffi) 'hostd_control_client_io.dart';
