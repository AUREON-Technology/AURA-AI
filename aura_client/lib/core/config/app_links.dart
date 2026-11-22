// Links into the customer app (SaaS P1-F2): where an invite link points.
//
// `--dart-define=APP_BASE_URL=https://app.aureon-ai.tech` sets it for every build; the web build
// falls back to the origin it was served from. With neither, there is no link to share and the
// invite token is shown instead.

import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../platform/browser_address.dart';

const _appBaseUrl = String.fromEnvironment('APP_BASE_URL');

String appBaseUrl() {
  if (_appBaseUrl.isNotEmpty) return _appBaseUrl.replaceAll(RegExp(r'/+$'), '');
  if (kIsWeb) return webAppBaseUrl(Uri.base.origin, browserBasePath());
  return '';
}

/// Where a web build is served from: its origin plus the path in its `<base href>`, without a
/// trailing slash. The app lives at `https://web.aureon-ai.tech/app/`, so a link built from the
/// bare origin would point outside it (and 404 at the host).
@visibleForTesting
String webAppBaseUrl(String origin, String basePath) {
  final path = basePath.replaceAll(RegExp(r'/+$'), '');
  return path.isEmpty ? origin : '$origin${path.startsWith('/') ? path : '/$path'}';
}

/// [appBaseUrl] for widgets and notifiers, so a test can stand in a web app address the build
/// was not given.
final appBaseUrlProvider = Provider<String>((ref) => appBaseUrl());

/// The web page the desktop app opens to sign in with Firebase through the browser (module
/// readiness WP12; `features/auth/presentation/desktop_sign_in_page.dart`). Empty when this
/// build does not know where the web app is, in which case that sign-in is not offered.
const desktopSignInPath = '/desktop-sign-in';

String desktopSignInPageFor(String base) => base.isEmpty ? '' : '$base$desktopSignInPath';

/// The link a person opens to join a team, or just the token when no base URL is known.
String inviteLinkFor(String token) {
  final base = appBaseUrl();
  return base.isEmpty ? token : '$base/invite?token=${Uri.encodeQueryComponent(token)}';
}
