import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

/// iOS release settings that no build in this repo's CI exercises (there is no macOS job), so a
/// regression would surface only at App Review or on a user's phone.
void main() {
  final plist = File('ios/Runner/Info.plist').readAsStringSync();

  String? purpose(String key) =>
      RegExp('<key>$key</key>\\s*<string>([^<]*)</string>').firstMatch(plist)?.group(1);

  // Each key: a plugin in pubspec.yaml that needs it.
  const required = {
    // record / live voice
    'NSMicrophoneUsageDescription': 'microphone',
    // local_auth with biometricOnly on the login screen: without it Face ID is refused.
    'NSFaceIDUsageDescription': 'Face ID',
    // file_picker links its photo and media pickers by default (ITMS-90683 otherwise).
    'NSPhotoLibraryUsageDescription': 'photo',
    'NSAppleMusicUsageDescription': 'media',
  };

  required.forEach((key, word) {
    test('Info.plist declares $key', () {
      final value = purpose(key);
      expect(value, isNotNull, reason: '$key missing from Info.plist');
      expect(value!.toLowerCase(), contains(word.toLowerCase()));
    });
  });

  group('Google Sign-In', () {
    // auth_provider.dart passes clientId: null on iOS, so google_sign_in reads GIDClientID, and
    // Google's redirect back into the app needs the reversed client id as a URL scheme.
    final xcconfig = File('ios/Flutter/GoogleSignIn.xcconfig').readAsStringSync();

    String setting(String name) =>
        RegExp('^$name\\s*=\\s*(\\S*)\\s*\$', multiLine: true).firstMatch(xcconfig)?.group(1) ?? '';

    test('Info.plist reads the client id and callback scheme from build settings', () {
      expect(purpose('GIDClientID'), r'$(GOOGLE_IOS_CLIENT_ID)');
      expect(plist, contains(r'<string>$(GOOGLE_IOS_REVERSED_CLIENT_ID)</string>'));
    });

    test('both build configurations include GoogleSignIn.xcconfig', () {
      for (final config in ['Debug', 'Release']) {
        expect(
          File('ios/Flutter/$config.xcconfig').readAsStringSync(),
          contains('#include "GoogleSignIn.xcconfig"'),
          reason: '$config builds would get an empty GIDClientID',
        );
      }
    });

    test('once set, the client id and the callback scheme belong to the same client', () {
      final clientId = setting('GOOGLE_IOS_CLIENT_ID');
      final reversed = setting('GOOGLE_IOS_REVERSED_CLIENT_ID');
      expect(clientId.isEmpty, reversed.isEmpty, reason: 'set both or neither');
      if (clientId.isEmpty) {
        markTestSkipped('iOS OAuth client not configured yet (ios/Flutter/GoogleSignIn.xcconfig)');
        return;
      }
      expect(clientId, endsWith('.apps.googleusercontent.com'));
      final reversedExpected =
          'com.googleusercontent.apps.${clientId.substring(0, clientId.length - '.apps.googleusercontent.com'.length)}';
      expect(reversed, reversedExpected);
    });
  });

  test('deployment target meets firebase_core\'s iOS minimum (15.0)', () {
    final pbx = File('ios/Runner.xcodeproj/project.pbxproj').readAsStringSync();
    final targets = RegExp(r'IPHONEOS_DEPLOYMENT_TARGET = ([0-9.]+);')
        .allMatches(pbx)
        .map((m) => double.parse(m.group(1)!))
        .toList();
    expect(targets, isNotEmpty);
    for (final t in targets) {
      expect(t, greaterThanOrEqualTo(15.0));
    }
  });
}
