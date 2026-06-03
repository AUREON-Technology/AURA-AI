import 'dart:convert';
import 'dart:io';
import 'package:encrypt/encrypt.dart' as encrypt;
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:path_provider/path_provider.dart';

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
        // Try reading fallback file in case it was written during a previous run when secure storage failed
        final fallbackKey = await _readFallbackKey();
        if (fallbackKey != null) {
          keyB64 = fallbackKey;
          // Try to restore to secure storage just in case it starts working
          try {
            await _storage.write(key: _keyName, value: keyB64);
          } catch (_) {}
        } else {
          // Generate a cryptographically secure 256-bit (32-byte) key
          final newKey = encrypt.Key.fromSecureRandom(32);
          keyB64 = newKey.base64;
          try {
            await _storage.write(key: _keyName, value: keyB64);
          } catch (e) {
            print("EncryptionService: Secure storage write failed: $e. Writing to fallback file.");
          }
          await _writeFallbackKey(keyB64);
        }
        _cryptKey = encrypt.Key.fromBase64(keyB64);
      } else {
        _cryptKey = encrypt.Key.fromBase64(keyB64);
        // Sync it to fallback key file to ensure persistence
        await _writeFallbackKey(keyB64);
      }
    } catch (e) {
      print("EncryptionService: Secure storage error: $e. Falling back to persistent file key.");
      try {
        final fallbackKey = await _readFallbackKey();
        if (fallbackKey != null) {
          _cryptKey = encrypt.Key.fromBase64(fallbackKey);
        } else {
          final newKey = encrypt.Key.fromUtf8('aura_ai_secure_db_encryption_key');
          await _writeFallbackKey(newKey.base64);
          _cryptKey = newKey;
        }
      } catch (fileErr) {
        print("EncryptionService: Fallback file error: $fileErr. Falling back to stable in-memory key.");
        _cryptKey = encrypt.Key.fromUtf8('aura_ai_secure_db_encryption_key');
      }
    }
  }

  static Future<String?> _readFallbackKey() async {
    try {
      final dir = await getApplicationSupportDirectory();
      final file = File('${dir.path}/.aura_key');
      if (await file.exists()) {
        final content = await file.readAsString();
        return content.trim();
      }
    } catch (e) {
      print("EncryptionService: Error reading fallback key: $e");
    }
    return null;
  }

  static Future<void> _writeFallbackKey(String keyB64) async {
    try {
      final dir = await getApplicationSupportDirectory();
      final file = File('${dir.path}/.aura_key');
      await file.writeAsString(keyB64);
    } catch (e) {
      print("EncryptionService: Error writing fallback key: $e");
    }
  }

  /// Encrypts plain text using AES-256 (CBC mode with PKCS7 padding)
  /// Prepend the IV (base64) to the ciphertext to allow decryption.
  static String encryptText(String plainText) {
    final key = _cryptKey ?? encrypt.Key.fromUtf8('aura_ai_secure_db_encryption_key');
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
    // Check if it's formatted as iv:ciphertext
    final parts = cipherText.split(':');
    if (parts.length != 2) {
      return cipherText;
    }

    final key = _cryptKey;
    if (key != null) {
      try {
        final iv = encrypt.IV.fromBase64(parts[0]);
        final encrypter = encrypt.Encrypter(encrypt.AES(key, mode: encrypt.AESMode.cbc));
        return encrypter.decrypt(encrypt.Encrypted.fromBase64(parts[1]), iv: iv);
      } catch (e) {
        // Fall through to try decrypting with the stable fallback key
      }
    }

    try {
      final stableKey = encrypt.Key.fromUtf8('aura_ai_secure_db_encryption_key');
      final iv = encrypt.IV.fromBase64(parts[0]);
      final encrypter = encrypt.Encrypter(encrypt.AES(stableKey, mode: encrypt.AESMode.cbc));
      return encrypter.decrypt(encrypt.Encrypted.fromBase64(parts[1]), iv: iv);
    } catch (stableErr) {
      return cipherText;
    }
  }
}
