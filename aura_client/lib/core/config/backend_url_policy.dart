/// Rules for what may be baked into a shipped client as [AppConfig.backendUrl].
///
/// The compile-time default is localhost so `flutter run` never talks to
/// production. Release builds must use `https://$AURA_DOMAIN`, matching the TLS
/// certificate and nginx `server_name`. Without an owned domain that hostname
/// is the VPS nip.io name (e.g. `169.58.188.43.nip.io`). A bare IP is rejected
/// because Android/iOS cannot present a matching HTTPS certificate for it.
class BackendUrlPolicy {
  BackendUrlPolicy._();

  static const String localDevUrl = 'http://localhost:8000';

  static final RegExp _ipv4Host = RegExp(r'^\d{1,3}(\.\d{1,3}){3}$');

  /// Returns a human-readable rejection, or `null` if [url] is safe to ship.
  static String? rejectionReason(String url) {
    final uri = Uri.tryParse(url);
    if (uri == null || uri.host.isEmpty) {
      return 'BACKEND_URL is not a valid absolute URI: $url';
    }
    if (uri.scheme != 'https') {
      return 'Release BACKEND_URL must use https (Android/iOS block cleartext).';
    }
    if (_isLoopback(uri.host)) {
      return 'Release BACKEND_URL must not target localhost; '
          'set --dart-define=BACKEND_URL=https://\$AURA_DOMAIN.';
    }
    if (_ipv4Host.hasMatch(uri.host)) {
      return 'Release BACKEND_URL must be a hostname, not a raw IP. '
          'Use the VPS nip.io name (https://<ip>.nip.io) when there is no domain.';
    }
    return null;
  }

  static bool isSafeForRelease(String url) => rejectionReason(url) == null;

  static void assertSafeForRelease(String url) {
    final reason = rejectionReason(url);
    if (reason != null) {
      throw StateError(reason);
    }
  }

  static bool _isLoopback(String host) {
    final lowered = host.toLowerCase();
    return lowered == 'localhost' || lowered == '127.0.0.1' || lowered == '::1';
  }
}
