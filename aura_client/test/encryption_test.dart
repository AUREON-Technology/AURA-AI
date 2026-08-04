import 'package:flutter_test/flutter_test.dart';
import 'package:aura_client/core/security/encryption_service.dart';

void main() {
  group('EncryptionService Tests', () {
    test('Initialization and Encryption/Decryption Flow', () async {
      await EncryptionService.init();

      const plainText = "Hello AURA secure database!";
      
      // Encrypt the text
      final cipherText = EncryptionService.encryptText(plainText);
      expect(cipherText, isNot(equals(plainText)));
      expect(cipherText.contains("Hello AURA"), isFalse);
      
      // The output should be formatted as gcm:base64IV:base64Ciphertext
      final parts = cipherText.split(':');
      expect(parts.length, equals(3));
      expect(parts[0], equals('gcm'));
      
      // Decrypt the text
      final decryptedText = EncryptionService.decryptText(cipherText);
      expect(decryptedText, equals(plainText));
    });

    test('Decryption Fallback for Unencrypted Data', () async {
      await EncryptionService.init();

      const rawText = "Pre-existing unencrypted task name";
      
      // Decrypting an unencrypted string should return the string itself
      final decrypted = EncryptionService.decryptText(rawText);
      expect(decrypted, equals(rawText));
    });

    group('fails closed', () {
      // encryptText's return value is written directly into the title_enc /
      // description_enc / fact_enc columns (core/sync/sync_payloads.dart) and
      // POSTed to a backend that treats those as opaque ciphertext. If
      // encryption ever falls back to returning the input, real user data is
      // uploaded into the one field the architecture guarantees never holds
      // any. These tests pin the fail-closed contract.
      tearDown(() async {
        // Restore a usable key so ordering between tests cannot leak a null.
        await EncryptionService.init();
      });

      test('throws instead of returning plaintext when key is unavailable', () async {
        await EncryptionService.init();
        EncryptionService.debugResetKeyForTesting();
        expect(EncryptionService.isInitialized, isFalse);

        const secret = "Sensitive task title";
        expect(
          () => EncryptionService.encryptText(secret),
          throwsA(isA<EncryptionException>()),
          reason: 'must never return plaintext for a field synced as ciphertext',
        );
      });

      test('never returns its input unchanged for non-empty plaintext', () async {
        await EncryptionService.init();
        const secret = "Sensitive task title";
        final out = EncryptionService.encryptText(secret);

        expect(out, isNot(equals(secret)));
        expect(out.startsWith('gcm:'), isTrue);
      });

      test('isInitialized reflects key availability', () async {
        await EncryptionService.init();
        expect(EncryptionService.isInitialized, isTrue);

        EncryptionService.debugResetKeyForTesting();
        expect(EncryptionService.isInitialized, isFalse);
      });
    });
  });
}
