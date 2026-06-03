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
      
      // The output should be formatted as base64IV:base64Ciphertext
      final parts = cipherText.split(':');
      expect(parts.length, equals(2));
      
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
  });
}
