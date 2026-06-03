import 'dart:convert';
import 'package:encrypt/encrypt.dart' as encrypt;
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

class EncryptionService {
  static const _keyName = 'aura_db_encryption_key';
  static encrypt.Key? _cryptKey;
  static const _storage = FlutterSecureStorage();

  /// Initializes the encryption key. Loads from secure storage,
  /// or generates a new one if it doesn't exist.
  static Future<void> init() async {
    if (_cryptKey != null) return;

    try {
      String? keyB64 = await _storage.read(key: _keyName);
      if (keyB64 == null) {
        // Generate a cryptographically secure 256-bit (32-byte) key
        final newKey = encrypt.Key.fromSecureRandom(32);
        keyB64 = newKey.base64;
        await _storage.write(key: _keyName, value: keyB64);
        _cryptKey = newKey;
      } else {
        _cryptKey = encrypt.Key.fromBase64(keyB64);
      }
    } catch (e) {
      // Fallback in-memory key generation in case secure storage fails on the host system.
      // This prevents the application from crashing.
      print("EncryptionService: Secure storage error: $e. Falling back to in-memory key.");
      _cryptKey = encrypt.Key.fromSecureRandom(32);
    }
  }

  /// Encrypts plain text using AES-256 (CBC mode with PKCS7 padding)
  /// Prepend the IV (base64) to the ciphertext to allow decryption.
  static String encryptText(String plainText) {
    final key = _cryptKey;
    if (key == null) {
      return plainText;
    }
    try {
      final iv = encrypt.IV.fromSecureRandom(16);
      final encrypter = encrypt.Encrypter(encrypt.AES(key, mode: encrypt.AESMode.cbc));
      final encrypted = encrypter.encrypt(plainText, iv: iv);
      return '${iv.base64}:${encrypted.base64}';
    } catch (e) {
      print("EncryptionService: Encryption error: $e");
      return plainText;
    }
  }

  /// Decrypts text encrypted using [encryptText].
  /// Gracefully falls back to the original text if decryption fails or format is invalid.
  static String decryptText(String cipherText) {
    final key = _cryptKey;
    if (key == null) {
      return cipherText;
    }
    // Check if it's formatted as iv:ciphertext
    final parts = cipherText.split(':');
    if (parts.length != 2) {
      return cipherText;
    }
    try {
      final iv = encrypt.IV.fromBase64(parts[0]);
      final encrypter = encrypt.Encrypter(encrypt.AES(key, mode: encrypt.AESMode.cbc));
      return encrypter.decrypt(encrypt.Encrypted.fromBase64(parts[1]), iv: iv);
    } catch (e) {
      // Graceful fallback for pre-existing unencrypted rows
      return cipherText;
    }
  }
}
