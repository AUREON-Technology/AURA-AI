import 'package:aura_client/core/auth/platform_login_support.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  tearDown(() {
    debugDefaultTargetPlatformOverride = null;
  });

  test('android/iOS/macOS all have a working google_sign_in implementation', () {
    for (final platform in [TargetPlatform.android, TargetPlatform.iOS, TargetPlatform.macOS]) {
      debugDefaultTargetPlatformOverride = platform;
      expect(supportsNativeGoogleSignIn(), isTrue, reason: '$platform should be supported');
    }
  });

  test('Linux and Windows do not have a working google_sign_in implementation', () {
    for (final platform in [TargetPlatform.linux, TargetPlatform.windows]) {
      debugDefaultTargetPlatformOverride = platform;
      expect(supportsNativeGoogleSignIn(), isFalse, reason: '$platform should not be supported');
    }
  });
}
