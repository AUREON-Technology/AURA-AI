import 'package:flutter/foundation.dart';
import 'dart:convert';
import 'dart:io';
import 'package:encrypt/encrypt.dart' as encrypt;
import 'package:uuid/uuid.dart';
import 'package:crypto/crypto.dart';
import 'package:pointycastle/key_derivators/api.dart';
import 'package:pointycastle/key_derivators/pbkdf2.dart';
import 'package:pointycastle/digests/sha256.dart';
import 'package:pointycastle/macs/hmac.dart';

import 'aura_secure_storage.dart';
import 'encryption_key_backup.dart';

/// Thrown when [EncryptionService.encryptText] cannot produce ciphertext.
///
/// Exists so encryption can fail *closed*. The previous behaviour was to
/// return the plaintext unchanged on failure, which is silently catastrophic:
/// the return value of encryptText is written straight into the `title_enc` /
/// `description_enc` / `fact_enc` columns (see core/sync/sync_payloads.dart)
/// and POSTed to the backend, which treats those columns as opaque ciphertext
/// it is contractually forbidden to read. A failed encryption therefore did
/// not degrade the zero-knowledge property — it inverted it, uploading real
/// user data in the exact field the architecture promises never contains any.
///
/// Callers should let this propagate: losing a write is recoverable, silently
/// transmitting plaintext is not.
class EncryptionException implements Exception {
  final String message;
  final Object? cause;

  const EncryptionException(this.message, [this.cause]);

  @override
  String toString() =>
      'EncryptionException: $message${cause != null ? ' (cause: $cause)' : ''}';
}

class EncryptionService {
  static const _keyName = 'aura_db_encryption_key';
  static encrypt.Key? _cryptKey;

  /// Whether [init] has completed and a key is available. Callers that want to
  /// avoid triggering an [EncryptionException] can check this first.
  static bool get isInitialized => _cryptKey != null;

  /// Clears the in-memory key so the uninitialised branch of [encryptText] is
  /// reachable from tests. [init] is idempotent, so tests must call it again
  /// afterwards to restore normal state.
  @visibleForTesting
  static void debugResetKeyForTesting() {
    _cryptKey = null;
  }

  /// Initializes the encryption key. Loads from secure storage, restores
  /// from the app-support backup if the plugin wiped itself, migrates a
  /// leftover `.aura_key`, or generates a new key only when both stores
  /// are empty.
  static Future<void> init() async {
    if (_cryptKey != null) return;

    try {
      final dir = await EncryptionKeyBackup.supportDirectory();
      final storedKey = EncryptionKeyBackup.usableKey(await _readSecureKey());
      final backupKey = await EncryptionKeyBackup.readFrom(dir);
      final legacyKey = await _readLegacyPlaintextKey(dir);

      var keyB64 = storedKey ?? backupKey ?? legacyKey;
      if (keyB64 == null) {
        keyB64 = encrypt.Key.fromSecureRandom(32).base64;
        _cryptKey = encrypt.Key.fromBase64(keyB64);
        await _dualWriteKey(dir, keyB64);
      } else if (storedKey == null) {
        debugPrint(
          backupKey != null
              ? 'EncryptionService: Restored DB key from ${EncryptionKeyBackup.fileName} after secure-storage miss.'
              : 'EncryptionService: Migrated DB key from legacy .aura_key.',
        );
        _cryptKey = encrypt.Key.fromBase64(keyB64);
        await _dualWriteKey(dir, keyB64);
      } else if (backupKey != null && backupKey != storedKey) {
        // A newly generated stored key must not overwrite the backup until
        // the DB integrity check proves the stored key can decrypt rows.
        debugPrint(
          'EncryptionService: Stored key differs from backup; preserving '
          '${EncryptionKeyBackup.fileName} until DB integrity check.',
        );
        _cryptKey = encrypt.Key.fromBase64(storedKey);
        try {
          await auraSecureStorage.write(key: _keyName, value: storedKey);
        } catch (e) {
          debugPrint('EncryptionService: Failed to write key to secure storage ($e).');
        }
      } else {
        _cryptKey = encrypt.Key.fromBase64(keyB64);
        await _dualWriteKey(dir, keyB64);
      }
      await _deleteLegacyPlaintextKey(dir);
    } catch (e) {
      debugPrint("EncryptionService: Secure storage failed ($e). Falling back to PBKDF2 derivation.");
      try {
        final dir = await EncryptionKeyBackup.supportDirectory();
        
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
        debugPrint("EncryptionService: Successfully initialized PBKDF2 derived key.");
      } catch (deriveErr) {
        debugPrint("EncryptionService: PBKDF2 key derivation failed ($deriveErr). Using environmental hash.");
        final username = Platform.environment['USERNAME'] ?? Platform.environment['USER'] ?? 'default_user';
        final rawKey = sha256.convert(utf8.encode('$username:${Platform.localHostname}')).bytes;
        _cryptKey = encrypt.Key(Uint8List.fromList(rawKey));
      }
    }
  }

  static Future<String?> _readSecureKey() async {
    try {
      return await auraSecureStorage.read(key: _keyName);
    } catch (e) {
      debugPrint('EncryptionService: Secure storage read failed ($e). Trying backup.');
      return null;
    }
  }

  /// Writes the canonical key to FlutterSecureStorage and the app-support
  /// backup independently so one failure cannot skip the other — or fall
  /// through to PBKDF2 and rotate the in-memory key.
  static Future<void> _dualWriteKey(Directory dir, String keyB64) async {
    try {
      await auraSecureStorage.write(key: _keyName, value: keyB64);
    } catch (e) {
      debugPrint('EncryptionService: Failed to write key to secure storage ($e).');
    }
    try {
      await EncryptionKeyBackup.writeTo(dir, keyB64);
    } catch (e) {
      debugPrint('EncryptionService: Failed to write ${EncryptionKeyBackup.fileName} ($e).');
    }
  }

  static Future<String?> _readLegacyPlaintextKey(Directory dir) async {
    try {
      final legacyFile = File('${dir.path}/.aura_key');
      if (!await legacyFile.exists()) return null;
      return EncryptionKeyBackup.usableKey(await legacyFile.readAsString());
    } catch (_) {
      return null;
    }
  }

  static Future<void> _deleteLegacyPlaintextKey(Directory dir) async {
    try {
      final legacyFile = File('${dir.path}/.aura_key');
      if (await legacyFile.exists()) {
        await legacyFile.delete();
      }
    } catch (_) {}
  }

  static String? _keyBeforeBackupRestore;

  /// Swaps the in-memory key to `.aura_db_key` when it differs from the
  /// current key. Caller must re-check decryption and then either
  /// [persistCurrentKey] or [revertBackupKeyRestore].
  static Future<bool> tryRestoreBackupKey() async {
    try {
      final dir = await EncryptionKeyBackup.supportDirectory();
      final backupKey = await EncryptionKeyBackup.readFrom(dir);
      if (backupKey == null) return false;
      if (_cryptKey != null && backupKey == _cryptKey!.base64) return false;
      _keyBeforeBackupRestore = _cryptKey?.base64;
      _cryptKey = encrypt.Key.fromBase64(backupKey);
      debugPrint(
        'EncryptionService: Trying backup DB key from ${EncryptionKeyBackup.fileName} after decrypt mismatch.',
      );
      return true;
    } catch (e) {
      debugPrint('EncryptionService: Backup key restore failed: $e');
      return false;
    }
  }

  static void revertBackupKeyRestore() {
    final previous = _keyBeforeBackupRestore;
    _keyBeforeBackupRestore = null;
    if (previous == null) return;
    _cryptKey = encrypt.Key.fromBase64(previous);
    debugPrint('EncryptionService: Reverted in-memory key after backup restore did not decrypt rows.');
  }

  static Future<void> persistCurrentKey() async {
    final key = _cryptKey;
    if (key == null) return;
    _keyBeforeBackupRestore = null;
    final dir = await EncryptionKeyBackup.supportDirectory();
    await _dualWriteKey(dir, key.base64);
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
  /// Format output as: `gcm:<iv_base64>:<ciphertext_base64>` (which contains ciphertext + GCM tag)
  /// Throws [EncryptionException] rather than returning plaintext if the key
  /// is unavailable or encryption fails — see [EncryptionException] for why
  /// failing closed is mandatory here. In practice this should never fire:
  /// main() awaits [init] before runApp, and init has three fallback layers
  /// (secure storage, PBKDF2 derivation, environmental hash) before it can
  /// leave the key null. This is a guard, not an expected code path.
  static String encryptText(String plainText) {
    final key = _cryptKey;
    if (key == null) {
      throw const EncryptionException(
        'encryptText called before init() completed — refusing to return '
        'plaintext for a field that is stored and synced as ciphertext.',
      );
    }
    try {
      final iv = encrypt.IV.fromSecureRandom(12); // Standard 12-byte IV for GCM
      final encrypter = encrypt.Encrypter(encrypt.AES(key, mode: encrypt.AESMode.gcm));
      final encrypted = encrypter.encrypt(plainText, iv: iv);
      return 'gcm:${iv.base64}:${encrypted.base64}';
    } catch (e) {
      debugPrint("EncryptionService: Encryption failed: $e");
      throw EncryptionException('AES-GCM encryption failed', e);
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
        debugPrint("EncryptionService: GCM Decryption error: $e");
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
