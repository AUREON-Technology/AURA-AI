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
