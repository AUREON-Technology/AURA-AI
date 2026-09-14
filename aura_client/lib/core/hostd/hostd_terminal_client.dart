/// WebSocket client for `aura_hostd`'s local `/terminal` route (D4,
/// docs/architecture/015-desktop-ide.md). See `hostd_terminal_client_io.dart`
/// for the real implementation and why the web build gets a stub instead --
/// same conditional-export pattern as `hostd_control_client.dart`.
library;

export 'hostd_terminal_client_stub.dart' if (dart.library.ffi) 'hostd_terminal_client_io.dart';
