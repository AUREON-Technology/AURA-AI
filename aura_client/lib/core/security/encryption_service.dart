import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:encrypt/encrypt.dart' as encrypt;

import 'aura_secure_storage.dart';
import 'encryption_key_policy.dart';
import 'encryption_runtime.dart';

/// Thrown when [EncryptionService.encryptText] cannot produce ciphertext.
///
/// Exists so encryption can fail *closed*. The previous behaviour was to
/// return the plaintext unchanged on failure: the return value of encryptText
/// is written straight into the `title_enc` / `description_enc` / `fact_enc`
/// columns (see core/sync/sync_payloads.dart) and POSTed to the backend, so a
/// failed encryption uploaded real user data in a field that is supposed to
/// hold only ciphertext.
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

/// Field encryption for the local database and the synced `*_enc` columns.
///
/// Two kinds of key (ADR-032):
///
/// * the **account data key** (DEK), fetched from `GET /api/v3/keys/data`
///   and shared by all of the user's devices *and the server*. Values sealed
///   with it are `gcm2:<kid>:<iv>:<ct+tag>`. Because the server holds it, the
///   synced fields are not zero-knowledge: the assistant can read and write
///   the user's tasks, events and records.
/// * the **legacy device key**, random per install and never uploaded. Values
///   sealed with it are `gcm:<iv>:<ct+tag>`. It is used until the device has a
///   data key, then retired by `DataKeyMigration` once nothing local still
///   needs it.
///
/// [encryptText] uses the primary data key when there is one, the legacy key
/// otherwise. [decryptText] reads both formats, and unwraps one nested layer:
/// a server-written `gcm2` row pulled before this device had the data key was
/// stored as that ciphertext under the device's legacy key, and becomes
/// readable as soon as the data key arrives.
class EncryptionService {
  static const _keyName = 'aura_db_encryption_key';
  static const dataKeysStorageName = 'aura_user_data_keys';

  /// The legacy device key.
  static encrypt.Key? _cryptKey;
  static final Map<String, encrypt.Key> _dataKeys = {};
  static String? _primaryKid;

  /// Set when a `gcm2` value named a key this device does not have (a data
  /// key rotation it has not fetched yet). The sync path refetches keys.
  static bool sawUnknownDataKey = false;

  static final RegExp _kidPattern = RegExp(r'^[0-9a-f]{8}$');

  /// Whether a key is available to encrypt with. Callers that want to avoid
  /// triggering an [EncryptionException] can check this first.
  static bool get isInitialized => _cryptKey != null || _primaryKid != null;

  /// Whether the account data key is installed (ADR-032).
  static bool get hasDataKey => _primaryKid != null;

  /// Whether the legacy device key is still held (migration not finished).
  static bool get hasLegacyKey => _cryptKey != null;

  /// Clears every in-memory key so the uninitialised branch of [encryptText]
  /// is reachable from tests. [init] is idempotent, so tests must call it
  /// again afterwards to restore normal state.
  @visibleForTesting
  static void debugResetKeyForTesting() {
    _cryptKey = null;
    _dataKeys.clear();
    _primaryKid = null;
    sawUnknownDataKey = false;
  }

  /// Installs keys directly, bypassing storage.
  @visibleForTesting
  static void debugSetKeysForTesting({
    encrypt.Key? legacy,
    Map<String, encrypt.Key> dataKeys = const {},
    String? primaryKid,
  }) {
    _cryptKey = legacy;
    _dataKeys
      ..clear()
      ..addAll(dataKeys);
    _primaryKid = primaryKid;
    sawUnknownDataKey = false;
  }

  /// Initializes the keys. Loads the account data keys from secure storage,
  /// then the legacy device key: from secure storage, from the app-support
  /// backup if the plugin wiped itself, from a leftover `.aura_key`, or a new
  /// one -- but a new legacy key is only generated when there is no data key
  /// to encrypt with, since a retired legacy key must not come back.
  static Future<void> init() async {
    if (isInitialized) return;
    await _loadDataKeys();

    try {
      final storedKey = EncryptionKeyPolicy.usableKey(await _readSecureKey());
      final backupKey = await readEncryptionBackupKey();
      final legacyKey = await readEncryptionLegacyKey();

      var keyB64 = storedKey ?? backupKey ?? legacyKey;
      if (keyB64 == null) {
        if (hasDataKey) return;
        keyB64 = encrypt.Key.fromSecureRandom(32).base64;
        _cryptKey = encrypt.Key.fromBase64(keyB64);
        await _dualWriteKey(keyB64);
      } else if (storedKey == null) {
        debugPrint(
          backupKey != null
              ? 'EncryptionService: Restored DB key from backup after secure-storage miss.'
              : 'EncryptionService: Migrated DB key from legacy .aura_key.',
        );
        _cryptKey = encrypt.Key.fromBase64(keyB64);
        await _dualWriteKey(keyB64);
      } else if (backupKey != null && backupKey != storedKey) {
        // A newly generated stored key must not overwrite the backup until
        // the DB integrity check proves the stored key can decrypt rows.
        debugPrint(
          'EncryptionService: Stored key differs from backup; preserving '
          'backup until DB integrity check.',
        );
        _cryptKey = encrypt.Key.fromBase64(storedKey);
        try {
          await auraSecureStorage.write(key: _keyName, value: storedKey);
        } catch (e) {
          debugPrint('EncryptionService: Failed to write key to secure storage ($e).');
        }
      } else {
        _cryptKey = encrypt.Key.fromBase64(keyB64);
        await _dualWriteKey(keyB64);
      }
      await deleteEncryptionLegacyKey();
    } catch (e) {
      if (hasDataKey) return;
      debugPrint(
        'EncryptionService: Secure storage failed ($e). Falling back to platform derivation.',
      );
      _cryptKey = await deriveEncryptionFallbackKey();
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

  static Future<void> _loadDataKeys() async {
    try {
      final stored = await auraSecureStorage.read(key: dataKeysStorageName);
      if (stored == null) return;
      final decoded = jsonDecode(stored) as Map<String, dynamic>;
      final keys = (decoded['keys'] as Map<String, dynamic>)
          .map((kid, b64) => MapEntry(kid, b64 as String));
      _install(decoded['primary'] as String, keys);
    } catch (e) {
      debugPrint('EncryptionService: no usable stored data key ($e).');
    }
  }

  /// Validates and installs the account data keys in memory.
  static void _install(String primary, Map<String, String> keysB64) {
    final keys = <String, encrypt.Key>{};
    keysB64.forEach((kid, b64) {
      if (!_kidPattern.hasMatch(kid)) {
        throw const FormatException('invalid data key id');
      }
      final bytes = base64Decode(b64);
      if (bytes.length != 32) throw const FormatException('a data key must be 32 bytes');
      keys[kid] = encrypt.Key(Uint8List.fromList(bytes));
    });
    if (!keys.containsKey(primary)) {
      throw const FormatException('the primary data key is missing');
    }
    _dataKeys
      ..clear()
      ..addAll(keys);
    _primaryKid = primary;
    sawUnknownDataKey = false;
  }

  /// Installs the account data keys (from `GET /api/v3/keys/data`) and
  /// persists them in secure storage. Throws [FormatException] on a malformed
  /// key set, leaving the current keys in place.
  static Future<void> installDataKeys(String primary, Map<String, String> keysB64) async {
    _install(primary, keysB64);
    try {
      await auraSecureStorage.write(
        key: dataKeysStorageName,
        value: jsonEncode({'primary': primary, 'keys': keysB64}),
      );
    } catch (e) {
      // Still usable for this run; refetched on the next start.
      debugPrint('EncryptionService: Failed to store the data key ($e).');
    }
  }

  /// Drops the account data keys (sign-out / account switch). If the legacy
  /// key was already retired, a fresh one is made so local writes keep
  /// working until the next account's key arrives.
  static Future<void> forgetDataKeys() async {
    _dataKeys.clear();
    _primaryKid = null;
    sawUnknownDataKey = false;
    try {
      await auraSecureStorage.delete(key: dataKeysStorageName);
    } catch (_) {}
    if (_cryptKey == null) {
      final fresh = encrypt.Key.fromSecureRandom(32).base64;
      _cryptKey = encrypt.Key.fromBase64(fresh);
      await _dualWriteKey(fresh);
    }
  }

  /// Deletes the legacy device key everywhere it is kept. Only called by
  /// `DataKeyMigration` once no local value is sealed with it any more.
  static Future<void> retireLegacyKey() async {
    if (!hasDataKey) return;
    _cryptKey = null;
    _keyBeforeBackupRestore = null;
    try {
      await auraSecureStorage.delete(key: _keyName);
    } catch (_) {}
    await deleteEncryptionBackupKey();
    await deleteEncryptionLegacyKey();
  }

  /// Writes the canonical key to FlutterSecureStorage and the platform
  /// backup independently so one failure cannot skip the other — or fall
  /// through to derivation and rotate the in-memory key.
  static Future<void> _dualWriteKey(String keyB64) async {
    try {
      await auraSecureStorage.write(key: _keyName, value: keyB64);
    } catch (e) {
      debugPrint('EncryptionService: Failed to write key to secure storage ($e).');
    }
    try {
      await writeEncryptionBackupKey(keyB64);
    } catch (e) {
      debugPrint('EncryptionService: Failed to write encryption key backup ($e).');
    }
  }

  static String? _keyBeforeBackupRestore;

  /// Swaps the in-memory legacy key to `.aura_db_key` when it differs from
  /// the current key. Caller must re-check decryption and then either
  /// [persistCurrentKey] or [revertBackupKeyRestore].
  static Future<bool> tryRestoreBackupKey() async {
    try {
      final backupKey = await readEncryptionBackupKey();
      if (backupKey == null) return false;
      if (_cryptKey != null && backupKey == _cryptKey!.base64) return false;
      _keyBeforeBackupRestore = _cryptKey?.base64;
      _cryptKey = encrypt.Key.fromBase64(backupKey);
      debugPrint(
        'EncryptionService: Trying backup DB key after decrypt mismatch.',
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
    await _dualWriteKey(key.base64);
  }

  /// Encrypts with AES-256-GCM: `gcm2:<kid>:<iv>:<ct+tag>` under the account
  /// data key when installed, else legacy `gcm:<iv>:<ct+tag>` under the
  /// device key. Throws [EncryptionException] rather than returning plaintext
  /// if no key is available or encryption fails — see [EncryptionException]
  /// for why failing closed is mandatory here.
  static String encryptText(String plainText) {
    final kid = _primaryKid;
    if (kid != null) {
      try {
        return _encryptGcm2(_dataKeys[kid]!, kid, encrypt.IV.fromSecureRandom(12), plainText);
      } catch (e) {
        debugPrint("EncryptionService: Encryption failed: $e");
        throw EncryptionException('AES-GCM encryption failed', e);
      }
    }
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

  static String _encryptGcm2(encrypt.Key key, String kid, encrypt.IV iv, String plainText) {
    final encrypter = encrypt.Encrypter(encrypt.AES(key, mode: encrypt.AESMode.gcm));
    return 'gcm2:$kid:${iv.base64}:${encrypter.encrypt(plainText, iv: iv).base64}';
  }

  /// [encryptText] with a caller-chosen IV, for cross-checking the format
  /// against the server's fixed vectors. Never use a fixed IV for real data.
  @visibleForTesting
  static String encryptGcm2WithIvForTesting(
    encrypt.Key key, String kid, encrypt.IV iv, String plainText,
  ) => _encryptGcm2(key, kid, iv, plainText);

  /// True for a value in either AES-GCM envelope this service writes.
  static bool isEnvelope(String value) =>
      value.startsWith('gcm2:') || value.startsWith('gcm:');

  /// Decrypts text encrypted using [encryptText] (either format), supporting
  /// legacy CBC mode, and one nested layer (see the class comment).
  /// Gracefully returns the original text when it cannot be decrypted.
  static String decryptText(String cipherText) {
    final outer = _decryptOnce(cipherText);
    if (outer == null) return cipherText;
    if (isEnvelope(outer)) {
      final inner = _decryptOnce(outer);
      if (inner != null) return inner;
    }
    return outer;
  }

  /// Decrypts one `gcm:` value with the legacy device key only, or null.
  /// Used by the migration, which must know which key opened a value.
  static String? tryDecryptLegacy(String value) {
    if (!value.startsWith('gcm:')) return null;
    return _decryptOnce(value);
  }

  /// Decrypts one envelope of either format, or null when this device
  /// cannot (no such key, wrong key, not an envelope).
  static String? tryDecrypt(String value) =>
      isEnvelope(value) ? _decryptOnce(value) : null;

  /// Whether a raw stored column value shows *local* key loss: a legacy
  /// `gcm:` value the device's legacy key cannot open. A `gcm2` value whose
  /// data key this device has not fetched yet is not loss -- the key can be
  /// fetched again -- and neither is another device's ciphertext held inside
  /// this device's own (openable) wrapper.
  static bool isLocalKeyLoss(String? raw) {
    if (raw == null || !raw.startsWith('gcm:')) return false;
    if (_cryptKey == null) return false;
    return _decryptOnce(raw) == null;
  }

  static String? _decryptOnce(String cipherText) {
    final parts = cipherText.split(':');

    // 0. Account data key (ADR-032): gcm2:kid:iv:ciphertext
    if (parts.length == 4 && parts[0] == 'gcm2') {
      final key = _dataKeys[parts[1]];
      if (key == null) {
        sawUnknownDataKey = true;
        return null;
      }
      try {
        final iv = encrypt.IV.fromBase64(parts[2]);
        final encrypter = encrypt.Encrypter(encrypt.AES(key, mode: encrypt.AESMode.gcm));
        return encrypter.decrypt(encrypt.Encrypted.fromBase64(parts[3]), iv: iv);
      } catch (e) {
        debugPrint("EncryptionService: GCM2 Decryption error: $e");
        return null;
      }
    }

    // 1. Legacy device key GCM (gcm:iv:ciphertext)
    if (parts.length == 3 && parts[0] == 'gcm') {
      final key = _cryptKey;
      if (key == null) return null;
      try {
        final iv = encrypt.IV.fromBase64(parts[1]);
        final encrypter = encrypt.Encrypter(encrypt.AES(key, mode: encrypt.AESMode.gcm));
        return encrypter.decrypt(encrypt.Encrypted.fromBase64(parts[2]), iv: iv);
      } catch (e) {
        debugPrint("EncryptionService: GCM Decryption error: $e");
        return null;
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

    return null;
  }
}
