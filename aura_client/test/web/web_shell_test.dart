import 'dart:io';
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';

/// The pieces of the web shell (`web/`) that the app's behaviour in a browser depends on, and
/// that nothing else would notice drifting. Read as files: they are not Dart.
void main() {
  final web = Directory('web');

  test('the Firebase SDK pinned in web/firebase-sdk.js is the one firebase_core_web supports',
      () {
    final script = File('${web.path}/firebase-sdk.js').readAsStringSync();
    final pinned = RegExp(r"var version = '([^']+)'").firstMatch(script)?.group(1);
    expect(pinned, isNotNull, reason: 'web/firebase-sdk.js has no version line');

    // `flutter test` cannot resolve package URIs through the isolate; the package config lists
    // where each package lives.
    final config = jsonDecode(File('.dart_tool/package_config.json').readAsStringSync()) as Map<String, dynamic>;
    final package = (config['packages'] as List).cast<Map<String, dynamic>>().firstWhere(
          (p) => p['name'] == 'firebase_core_web',
        );
    var rootUri = package['rootUri'] as String;
    if (!rootUri.endsWith('/')) rootUri = '$rootUri/'; // a directory URI, or resolve() drops its last segment
    final root = File('.dart_tool/package_config.json').absolute.parent.uri.resolve(rootUri);
    final versionFile = File.fromUri(root.resolve('${package['packageUri'] ?? 'lib/'}src/firebase_sdk_version.dart'));
    final supported = RegExp(r"supportedFirebaseJsSdkVersion = '([^']+)'")
        .firstMatch(versionFile.readAsStringSync())
        ?.group(1);
    expect(
      pinned,
      supported,
      reason: 'firebase_core_web now supports $supported; update the version in web/firebase-sdk.js '
          '(and re-check the CSP in aura_web/nginx/security-headers-app.conf).',
    );
  });

  test('the SDK modules are placed where firebase_core_web looks, and the app waits for them', () {
    final script = File('${web.path}/firebase-sdk.js').readAsStringSync();
    expect(script, contains('window.firebase_core ='));
    expect(script, contains('window.firebase_auth ='));
    // Core first, then auth: never concurrently.
    expect(script.indexOf('firebase-app.js'), lessThan(script.indexOf('firebase-auth.js')));

    final bootstrap = File('${web.path}/flutter_bootstrap.js').readAsStringSync();
    expect(bootstrap, contains('{{flutter_js}}'));
    expect(bootstrap, contains('{{flutter_build_config}}'));
    expect(bootstrap, contains('await window.__auraFirebaseReady'));
    expect(bootstrap, contains('_flutter.loader.load('));
  });

  test('index.html loads the SDK script before the app, and uses no inline script', () {
    final html = File('${web.path}/index.html').readAsStringSync();
    expect(html.indexOf('firebase-sdk.js'), lessThan(html.indexOf('flutter_bootstrap.js')));
    expect(html, contains(r'<base href="$FLUTTER_BASE_HREF">'));
    // The host's CSP has no 'unsafe-inline' for scripts: every <script> must have a src.
    final inline = RegExp(r'<script(?![^>]*\bsrc=)[^>]*>').allMatches(html);
    expect(inline, isEmpty);
  });

  test('the manifest names the product, not the Flutter template', () {
    final manifest = File('${web.path}/manifest.json').readAsStringSync();
    expect(manifest, contains('"name": "AURA AI"'));
    expect(manifest, isNot(contains('A new Flutter project')));
    expect(manifest, isNot(contains('aura_client')));
  });
}
