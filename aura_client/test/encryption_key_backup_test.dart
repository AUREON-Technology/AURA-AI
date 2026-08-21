import 'dart:io';

import 'package:aura_client/core/security/encryption_key_backup.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('EncryptionKeyBackup', () {
    test('usableKey rejects empty and the old hardcoded key', () {
      expect(EncryptionKeyBackup.usableKey(null), isNull);
      expect(EncryptionKeyBackup.usableKey(''), isNull);
      expect(EncryptionKeyBackup.usableKey('   '), isNull);
      expect(
        EncryptionKeyBackup.usableKey('aura_ai_secure_db_encryption_key'),
        isNull,
      );
    });

    test('usableKey trims a persistable key', () {
      expect(EncryptionKeyBackup.usableKey('  abc123==  '), 'abc123==');
    });

    test('readFrom restores a previously written key from app-support', () async {
      final dir = await Directory.systemTemp.createTemp('aura_db_key_');
      addTearDown(() => dir.delete(recursive: true));

      expect(await EncryptionKeyBackup.readFrom(dir), isNull);

      const key = 'dGVzdC1kYi1lbmNyeXB0aW9uLWtleQ==';
      await EncryptionKeyBackup.writeTo(dir, key);

      expect(
        EncryptionKeyBackup.fileFor(dir).uri.pathSegments.last,
        EncryptionKeyBackup.fileName,
      );
      expect(EncryptionKeyBackup.fileName, '.aura_db_key');
      expect(await EncryptionKeyBackup.readFrom(dir), key);
    });
  });
}
