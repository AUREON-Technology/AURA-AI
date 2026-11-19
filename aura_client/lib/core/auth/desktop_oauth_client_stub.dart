/// Web build of `desktop_oauth_client.dart` -- the loopback listener needs `dart:io`, which
/// doesn't exist on web, and web already has a working `google_sign_in` implementation with
/// nothing to fall back from.
///
/// `signInWithGoogleLoopback`'s and `signInWithBrowserHandoff`'s signatures here must stay identical to `desktop_oauth_client_io
/// .dart`'s, [launch]/[tokenEndpoint] included -- `dart analyze`/the Dart analyzer resolves a
/// `dart.library.*`-conditional export statically against this default branch regardless of the
/// runtime platform being analyzed for, so a signature drift between the two shows up as an
/// "undefined named parameter" error at every call site, not just on web.
library;

class DesktopOAuthCancelledException implements Exception {
  DesktopOAuthCancelledException(this.message, {this.statusCode, this.refusal});

  final String message;
  final int? statusCode;
  final String? refusal;

  @override
  String toString() => 'DesktopOAuthCancelledException: $message';
}

class DesktopOAuthResult {
  DesktopOAuthResult({required this.idToken});

  final String idToken;
}

class DesktopHandoffResult {
  DesktopHandoffResult({required this.tokenResponse});

  final Map<String, dynamic> tokenResponse;

  String get accessToken => tokenResponse['access_token'] as String;
}

Future<DesktopOAuthResult> signInWithGoogleLoopback({
  required String clientId,
  String clientSecret = '',
  Uri? backendExchangeEndpoint,
  Duration timeout = const Duration(minutes: 5),
  Future<bool> Function(Uri uri)? launch,
  Uri? tokenEndpoint,
}) {
  throw DesktopOAuthCancelledException(
    'Loopback OAuth login is not available on this platform.',
  );
}

Future<DesktopHandoffResult> signInWithBrowserHandoff({
  required Uri signInPage,
  required Uri exchangeEndpoint,
  String? deviceName,
  Duration timeout = const Duration(minutes: 5),
  Future<bool> Function(Uri uri)? launch,
}) {
  throw DesktopOAuthCancelledException(
    'Browser hand-off sign-in is not available on this platform.',
  );
}

/// No `dart:io` here, so no hostname: the web build signs in without a device name.
String? desktopDeviceName() => null;
