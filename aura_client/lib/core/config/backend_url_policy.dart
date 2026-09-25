/// Rules for what may be baked into a shipped client as [AppConfig.backendUrl],
/// and which hosts may be selected at runtime via Settings.
///
/// The compile-time default is localhost so `flutter run` never talks to
/// production. Release builds must use `https://$AURA_DOMAIN`, matching the TLS
/// certificate and nginx `server_name`. That hostname is the production domain
/// (`aureon-ai.tech`). A bare IP is rejected
/// because Android/iOS cannot present a matching HTTPS certificate for it.
class BackendUrlPolicy {
  BackendUrlPolicy._();

  static const String localDevUrl = 'http://localhost:8000';

  /// Known AURA gateway hosts users may switch between in release builds.
  static const Set<String> runtimeAllowlistedHosts = {
    'aureon-ai.tech',
    'dev.aureon-ai.tech',
    'aura-ai-production-03a4.up.railway.app',
  };

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
          'Use https://aureon-ai.tech (prod) or https://dev.aureon-ai.tech (dev).';
    }
    return null;
  }

  static bool isSafeForRelease(String url) => rejectionReason(url) == null;

  /// Runtime switch validation: HTTPS hostname rules plus the preset allowlist.
  static String? runtimeRejectionReason(String url) {
    final baseReason = rejectionReason(url);
    if (baseReason != null) return baseReason;

    final host = Uri.tryParse(url)?.host.toLowerCase();
    if (host == null || !runtimeAllowlistedHosts.contains(host)) {
      return 'Backend URL host is not in the runtime allowlist: $url';
    }
    return null;
  }

  static bool isRuntimeAllowed(String url) => runtimeRejectionReason(url) == null;

  static void assertSafeForRelease(String url) {
    final reason = rejectionReason(url);
    if (reason != null) {
      throw StateError(reason);
    }
  }

  static void assertRuntimeAllowed(String url) {
    final reason = runtimeRejectionReason(url);
    if (reason != null) {
      throw StateError(reason);
    }
  }

  static bool _isLoopback(String host) {
    final lowered = host.toLowerCase();
    return lowered == 'localhost' || lowered == '127.0.0.1' || lowered == '::1';
  }
}
