// WP7 (D-08, D-08b): the Desktop IDE runs on Windows and Linux. macOS, mobile and web do not
// get it (web is `kIsWeb`, a compile-time constant this VM test cannot flip; the gate checks it
// before the platform, so no web build can reach the IDE whatever `defaultTargetPlatform` says).
import 'package:aura_client/core/platform/aura_platform.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  tearDown(() => debugDefaultTargetPlatformOverride = null);

  for (final platform in [TargetPlatform.windows, TargetPlatform.linux]) {
    test('${platform.name} runs the Desktop IDE', () {
      debugDefaultTargetPlatformOverride = platform;
      expect(isDesktopIde, isTrue);
      expect(canRunDesktopIde, isTrue);
    });
  }

  for (final platform in [
    TargetPlatform.macOS,
    TargetPlatform.android,
    TargetPlatform.iOS,
    TargetPlatform.fuchsia,
  ]) {
    test('${platform.name} does not run the Desktop IDE', () {
      debugDefaultTargetPlatformOverride = platform;
      expect(isDesktopIde, isFalse);
      expect(canRunDesktopIde, isFalse);
    });
  }
}
