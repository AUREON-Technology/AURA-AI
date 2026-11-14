import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

import 'package:aura_client/core/security/encryption_key_backup.dart';

/// The plaintext key mirror (`EncryptionKeyBackup`) must never reach an Android
/// backup. On Android, `getApplicationSupportDirectory()` is `Context.getFilesDir()`
/// itself, so the file is `files/<fileName>`: an `<exclude domain="file">` rule
/// must name the bare file name. The rules used to say `app_flutter/.aura_db_key`,
/// which matches nothing, so cloud backup and device transfer carried the key.
void main() {
  const rules = [
    'android/app/src/main/res/xml/data_extraction_rules.xml',
    'android/app/src/main/res/xml/full_backup_content.xml',
  ];

  for (final path in rules) {
    test('$path excludes filesDir/${EncryptionKeyBackup.fileName}', () {
      final xml = File(path).readAsStringSync();
      final excludes = RegExp(r'<exclude\s+domain="file"\s+path="([^"]+)"\s*/>')
          .allMatches(xml)
          .map((m) => m.group(1))
          .toList();
      expect(excludes, contains(EncryptionKeyBackup.fileName));
    });
  }

  test('data extraction rules cover both cloud backup and device transfer', () {
    final xml = File(rules.first).readAsStringSync();
    for (final section in ['cloud-backup', 'device-transfer']) {
      final body = RegExp('<$section>(.*?)</$section>', dotAll: true).firstMatch(xml)?.group(1);
      expect(body, isNotNull, reason: '$section missing');
      expect(body, contains('path="${EncryptionKeyBackup.fileName}"'), reason: section);
    }
  });
}
