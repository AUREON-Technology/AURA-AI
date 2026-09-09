/// Real loopback-redirect OAuth 2.0 + PKCE login against Google (RFC 8252), for desktop
/// platforms `google_sign_in` has no plugin implementation on (Linux, Windows -- ADR-011 Unit 7,
/// `docs/architecture/011-desktop-engineer-agent.md`). macOS and mobile keep using
/// `google_sign_in`, where it works.
///
/// `dart:io`'s `HttpServer`/`HttpClient` (the loopback listener and the token-exchange request)
/// are unavailable on web, so the real implementation lives behind the same conditional-export
/// pattern `core/database/connection/open_connection.dart` and
/// `core/security/encryption_runtime_io.dart`/`_web.dart` already use: this file exports the stub
/// unconditionally, overridden by the real `dart:io`-backed implementation wherever
/// `dart.library.ffi` is available (every non-web target, including mobile -- compiling there is
/// harmless even though only Linux/Windows callers actually invoke it).
library;

export 'desktop_oauth_client_stub.dart'
    if (dart.library.ffi) 'desktop_oauth_client_io.dart';
