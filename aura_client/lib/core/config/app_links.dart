// Links into the customer app (SaaS P1-F2): where an invite link points.
//
// `--dart-define=APP_BASE_URL=https://app.aureon-ai.tech` sets it for every build; the web build
// falls back to the origin it was served from. With neither, there is no link to share and the
// invite token is shown instead.

import 'package:flutter/foundation.dart';

const _appBaseUrl = String.fromEnvironment('APP_BASE_URL');

String appBaseUrl() {
  if (_appBaseUrl.isNotEmpty) return _appBaseUrl.replaceAll(RegExp(r'/+$'), '');
  if (kIsWeb) return Uri.base.origin;
  return '';
}

/// The link a person opens to join a team, or just the token when no base URL is known.
String inviteLinkFor(String token) {
  final base = appBaseUrl();
  return base.isEmpty ? token : '$base/invite?token=${Uri.encodeQueryComponent(token)}';
}
