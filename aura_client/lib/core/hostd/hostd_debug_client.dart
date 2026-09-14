/// WebSocket client for `aura_hostd`'s local `/debug` route (D7 Part A.3). See
/// `hostd_debug_client_io.dart` for the real implementation and why the web build gets a stub
/// instead -- same conditional-export pattern as `hostd_terminal_client.dart`.
library;

export 'hostd_debug_client_stub.dart' if (dart.library.ffi) 'hostd_debug_client_io.dart';
