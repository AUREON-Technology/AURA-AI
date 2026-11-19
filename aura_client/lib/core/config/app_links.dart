// Links into the customer app (SaaS P1-F2): where an invite link points.
//
// `--dart-define=APP_BASE_URL=https://app.aureon-ai.tech` sets it for every build; the web build
// falls back to the origin it was served from. With neither, there is no link to share and the
// invite token is shown instead.

import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

const _appBaseUrl = String.fromEnvironment('APP_BASE_URL');

String appBaseUrl() {
  if (_appBaseUrl.isNotEmpty) return _appBaseUrl.replaceAll(RegExp(r'/+$'), '');
  if (kIsWeb) return Uri.base.origin;
  return '';
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
