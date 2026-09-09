/// Web build of `desktop_oauth_client.dart` -- the loopback listener needs `dart:io`, which
/// doesn't exist on web, and web already has a working `google_sign_in` implementation with
/// nothing to fall back from.
library;

class DesktopOAuthCancelledException implements Exception {
  DesktopOAuthCancelledException(this.message);

  final String message;

  @override
  String toString() => 'DesktopOAuthCancelledException: $message';
}

class DesktopOAuthResult {
  DesktopOAuthResult({required this.idToken});

  final String idToken;
}

Future<DesktopOAuthResult> signInWithGoogleLoopback({
  required String clientId,
  Duration timeout = const Duration(minutes: 5),
}) {
  throw DesktopOAuthCancelledException(
    'Loopback OAuth login is not available on this platform.',
  );
}
