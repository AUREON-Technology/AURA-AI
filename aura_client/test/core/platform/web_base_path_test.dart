import 'package:aura_client/core/config/app_links.dart';
import 'package:aura_client/core/routing/router.dart';
import 'package:flutter_test/flutter_test.dart';

/// The web app is served at https://web.aureon-ai.tech/app/ (`flutter build web --base-href
/// /app/`), not from a host root. Every real-path link into it has to survive that prefix.
void main() {
  group('webDeepLinkLocation under a base path', () {
    const base = '/app/';

    test('an invite opened under /app/ starts at the invite, token intact', () {
      expect(
        webDeepLinkLocation(Uri.parse('https://web.aureon-ai.tech/app/invite?token=abc'), basePath: base),
        '/invite?token=abc',
      );
    });

    test('the desktop sign-in page opened under /app/ keeps its query', () {
      expect(
        webDeepLinkLocation(
          Uri.parse('https://web.aureon-ai.tech/app/desktop-sign-in?state=s&x=1'),
          basePath: base,
        ),
        '/desktop-sign-in?state=s&x=1',
      );
    });

    test('the app root and the hash route start as they always did', () {
      expect(webDeepLinkLocation(Uri.parse('https://web.aureon-ai.tech/app/'), basePath: base), isNull);
      expect(webDeepLinkLocation(Uri.parse('https://web.aureon-ai.tech/app'), basePath: base), isNull);
      expect(webDeepLinkLocation(Uri.parse('https://web.aureon-ai.tech/app/#/chat'), basePath: base), isNull);
    });

    test('a path outside the base is not an app link, even when its name matches', () {
      expect(webDeepLinkLocation(Uri.parse('https://web.aureon-ai.tech/invite?token=abc'), basePath: base), isNull);
      expect(webDeepLinkLocation(Uri.parse('https://web.aureon-ai.tech/application/invite'), basePath: base), isNull);
    });

    test('a build served from the root behaves exactly as before', () {
      expect(
        webDeepLinkLocation(Uri.parse('https://app.example.test/invite?token=abc')),
        '/invite?token=abc',
      );
      expect(webDeepLinkLocation(Uri.parse('https://app.example.test/invite?token=abc'), basePath: '/'),
          '/invite?token=abc');
    });
  });

  group('webAppBaseUrl', () {
    test('adds the base path to the origin, without a trailing slash', () {
      expect(webAppBaseUrl('https://web.aureon-ai.tech', '/app/'), 'https://web.aureon-ai.tech/app');
      expect(webAppBaseUrl('https://web.aureon-ai.tech', '/app'), 'https://web.aureon-ai.tech/app');
    });

    test('is the bare origin for a root-hosted build', () {
      expect(webAppBaseUrl('https://app.example.test', '/'), 'https://app.example.test');
      expect(webAppBaseUrl('https://app.example.test', ''), 'https://app.example.test');
    });

    test('an invite link built from it points inside the app', () {
      final base = webAppBaseUrl('https://web.aureon-ai.tech', '/app/');
      expect('$base/invite?token=abc', 'https://web.aureon-ai.tech/app/invite?token=abc');
    });
  });
}
