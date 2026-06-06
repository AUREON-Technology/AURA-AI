import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';
import 'package:encrypt/encrypt.dart' as encrypt;
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:path_provider/path_provider.dart';
import 'package:uuid/uuid.dart';
import 'package:crypto/crypto.dart';
import 'package:pointycastle/key_derivators/api.dart';
import 'package:pointycastle/key_derivators/pbkdf2.dart';
import 'package:pointycastle/digests/sha256.dart';
import 'package:pointycastle/macs/hmac.dart';
import 'package:pointycastle/api.dart' show Pbkdf2Parameters;

class EncryptionService {
  static const _keyName = 'aura_db_encryption_key';
  static encrypt.Key? _cryptKey;
  
  // Platform-native storage configuration
  static const _storage = FlutterSecureStorage(
    aOptions: AndroidOptions(encryptedSharedPreferences: true),
    iOptions: IOSOptions(accessibility: KeychainAccessibility.first_unlock),
    mOptions: MacOsOptions(accessibility: KeychainAccessibility.first_unlock),
    wOptions: WindowsOptions(),
  );

  /// Initializes the encryption key. Loads from secure storage,
  /// handles legacy migration, or generates a new key.
  static Future<void> init() async {
    if (_cryptKey != null) return;

    try {
      final dir = await getApplicationSupportDirectory();
      final legacyFile = File('${dir.path}/.aura_key');
      String? migratedKeyB64;

      // 1. Secure Migration: Read and delete legacy plaintext key
      if (await legacyFile.exists()) {
        try {
          final legacyContent = await legacyFile.readAsString();
          final trimmed = legacyContent.trim();
          if (trimmed.isNotEmpty && trimmed != 'aura_ai_secure_db_encryption_key') {
            migratedKeyB64 = trimmed;
          }
        } catch (_) {}
      }

      String? keyB64 = await _storage.read(key: _keyName);

      if (keyB64 == null && migratedKeyB64 != null) {
        // Store the migrated key in native secure storage
        await _storage.write(key: _keyName, value: migratedKeyB64);
        keyB64 = migratedKeyB64;
        
        // Delete legacy plaintext file
        try {
          await legacyFile.delete();
        } catch (_) {}
      } else if (legacyFile.existsSync()) {
        // If secure storage already has a key, clean up plaintext key
        try {
          await legacyFile.delete();
        } catch (_) {}
      }

      if (keyB64 == null) {
        // Generate a cryptographically secure 256-bit (32-byte) key
        final newKey = encrypt.Key.fromSecureRandom(32);
        keyB64 = newKey.base64;
        await _storage.write(key: _keyName, value: keyB64);
      }
      
      _cryptKey = encrypt.Key.fromBase64(keyB64);
    } catch (e) {
      print("EncryptionService: Secure storage failed ($e). Falling back to PBKDF2 derivation.");
      try {
        final dir = await getApplicationSupportDirectory();
        
        // Resolve or generate device salt
        final saltFile = File('${dir.path}/.aura_salt');
        String salt;
        if (await saltFile.exists()) {
          salt = (await saltFile.readAsString()).trim();
        } else {
          final secureRandomBytes = encrypt.IV.fromSecureRandom(32).bytes;
          salt = base64Encode(secureRandomBytes);
          await saltFile.writeAsString(salt);
        }

        // Resolve or generate installation ID
        final installFile = File('${dir.path}/.aura_install');
        String installId;
        if (await installFile.exists()) {
          installId = (await installFile.readAsString()).trim();
        } else {
          installId = const Uuid().v4();
          await installFile.writeAsString(installId);
        }

        final username = Platform.environment['USERNAME'] ?? Platform.environment['USER'] ?? 'default_user';
        final userSecret = '$username:${Platform.localHostname}:${Platform.operatingSystem}';

        _cryptKey = _deriveFallbackKey(userSecret, salt, installId);
        print("EncryptionService: Successfully initialized PBKDF2 derived key.");
      } catch (deriveErr) {
        print("EncryptionService: PBKDF2 key derivation failed ($deriveErr). Using environmental hash.");
        final username = Platform.environment['USERNAME'] ?? Platform.environment['USER'] ?? 'default_user';
        final rawKey = sha256.convert(utf8.encode('$username:${Platform.localHostname}')).bytes;
        _cryptKey = encrypt.Key(Uint8List.fromList(rawKey));
      }
    }
  }

  /// Derives a 256-bit AES key using PBKDF2-HMAC-SHA256 from user secret and salt details.
  static encrypt.Key _deriveFallbackKey(String userSecret, String saltStr, String installId) {
    final passwordBytes = utf8.encode(userSecret);
    final saltBytes = utf8.encode('$saltStr:$installId');

    final derivator = PBKDF2KeyDerivator(HMac(SHA256Digest(), 64))
      ..init(Pbkdf2Parameters(Uint8List.fromList(saltBytes), 10000, 32));

    final derivedKeyBytes = derivator.process(Uint8List.fromList(passwordBytes));
    return encrypt.Key(derivedKeyBytes);
  }

  /// Encrypts plain text using AES-256 (GCM mode).
  /// Format output as: gcm:<iv_base64>:<ciphertext_base64> (which contains ciphertext + GCM tag)
  static String encryptText(String plainText) {
    final key = _cryptKey;
    if (key == null) return plainText;
    try {
      final iv = encrypt.IV.fromSecureRandom(12); // Standard 12-byte IV for GCM
      final encrypter = encrypt.Encrypter(encrypt.AES(key, mode: encrypt.AESMode.gcm));
      final encrypted = encrypter.encrypt(plainText, iv: iv);
      return 'gcm:${iv.base64}:${encrypted.base64}';
    } catch (e) {
      print("EncryptionService: Encryption error: $e");
      return plainText;
    }
  }

  /// Decrypts text encrypted using [encryptText], supporting legacy CBC mode.
  /// Gracefully returns original text on failure.
  static String decryptText(String cipherText) {
    final parts = cipherText.split(':');

    // 1. New GCM Decryption (gcm:iv:ciphertext)
    if (parts.length == 3 && parts[0] == 'gcm') {
      final key = _cryptKey;
      if (key == null) return cipherText;
      try {
        final iv = encrypt.IV.fromBase64(parts[1]);
        final encrypter = encrypt.Encrypter(encrypt.AES(key, mode: encrypt.AESMode.gcm));
        return encrypter.decrypt(encrypt.Encrypted.fromBase64(parts[2]), iv: iv);
      } catch (e) {
        print("EncryptionService: GCM Decryption error: $e");
        return cipherText;
      }
    }

    // 2. Legacy CBC Decryption (iv:ciphertext)
    if (parts.length == 2) {
      final key = _cryptKey;
      if (key != null) {
        try {
          final iv = encrypt.IV.fromBase64(parts[0]);
          final encrypter = encrypt.Encrypter(encrypt.AES(key, mode: encrypt.AESMode.cbc));
          return encrypter.decrypt(encrypt.Encrypted.fromBase64(parts[1]), iv: iv);
        } catch (_) {}
      }
      try {
        final stableKey = encrypt.Key.fromUtf8('aura_ai_secure_db_encryption_key');
        final iv = encrypt.IV.fromBase64(parts[0]);
        final encrypter = encrypt.Encrypter(encrypt.AES(stableKey, mode: encrypt.AESMode.cbc));
        return encrypter.decrypt(encrypt.Encrypted.fromBase64(parts[1]), iv: iv);
      } catch (_) {}
    }

    return cipherText;
  }
}
