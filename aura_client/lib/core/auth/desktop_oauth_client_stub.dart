/// Web build of `desktop_oauth_client.dart` -- the loopback listener needs `dart:io`, which
/// doesn't exist on web, and web already has a working `google_sign_in` implementation with
/// nothing to fall back from.
///
/// `signInWithGoogleLoopback`'s signature here must stay identical to `desktop_oauth_client_io
/// .dart`'s, [launch]/[tokenEndpoint] included -- `dart analyze`/the Dart analyzer resolves a
/// `dart.library.*`-conditional export statically against this default branch regardless of the
/// runtime platform being analyzed for, so a signature drift between the two shows up as an
/// "undefined named parameter" error at every call site, not just on web.
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
  Future<bool> Function(Uri uri)? launch,
  Uri? tokenEndpoint,
}) {
  throw DesktopOAuthCancelledException(
    'Loopback OAuth login is not available on this platform.',
  );
}
