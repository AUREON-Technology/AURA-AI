import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:encrypt/encrypt.dart' as encrypt;
import 'package:flutter/foundation.dart';
import 'package:pointycastle/digests/sha256.dart';
import 'package:pointycastle/key_derivators/api.dart';
import 'package:pointycastle/key_derivators/pbkdf2.dart';
import 'package:pointycastle/macs/hmac.dart';
import 'package:uuid/uuid.dart';

import 'encryption_key_backup.dart';

Future<String?> readEncryptionBackupKey() async {
  final dir = await EncryptionKeyBackup.supportDirectory();
  return EncryptionKeyBackup.readFrom(dir);
}

Future<void> writeEncryptionBackupKey(String keyB64) async {
  final dir = await EncryptionKeyBackup.supportDirectory();
  await EncryptionKeyBackup.writeTo(dir, keyB64);
}

Future<String?> readEncryptionLegacyKey() async {
  try {
    final dir = await EncryptionKeyBackup.supportDirectory();
    final legacyFile = File('${dir.path}/.aura_key');
    if (!await legacyFile.exists()) return null;
    return EncryptionKeyBackup.usableKey(await legacyFile.readAsString());
  } catch (_) {
    return null;
  }
}

Future<void> deleteEncryptionLegacyKey() async {
  try {
    final dir = await EncryptionKeyBackup.supportDirectory();
    final legacyFile = File('${dir.path}/.aura_key');
    if (await legacyFile.exists()) {
      await legacyFile.delete();
    }
  } catch (_) {}
}

Future<encrypt.Key?> deriveEncryptionFallbackKey() async {
  try {
    final dir = await EncryptionKeyBackup.supportDirectory();

    final saltFile = File('${dir.path}/.aura_salt');
    late final String salt;
    if (await saltFile.exists()) {
      salt = (await saltFile.readAsString()).trim();
    } else {
      salt = base64Encode(encrypt.IV.fromSecureRandom(32).bytes);
      await saltFile.writeAsString(salt);
    }

    final installFile = File('${dir.path}/.aura_install');
    late final String installId;
    if (await installFile.exists()) {
      installId = (await installFile.readAsString()).trim();
    } else {
      installId = const Uuid().v4();
      await installFile.writeAsString(installId);
    }

    final username = Platform.environment['USERNAME'] ??
        Platform.environment['USER'] ??
        'default_user';
    final userSecret =
        '$username:${Platform.localHostname}:${Platform.operatingSystem}';
    debugPrint(
      'EncryptionService: Successfully initialized PBKDF2 derived key.',
    );
    return _derivePbkdf2Key(userSecret, salt, installId);
  } catch (deriveErr) {
    debugPrint(
      'EncryptionService: PBKDF2 key derivation failed ($deriveErr). '
      'Using environmental hash.',
    );
    try {
      final username = Platform.environment['USERNAME'] ??
          Platform.environment['USER'] ??
          'default_user';
      final rawKey = sha256
          .convert(utf8.encode('$username:${Platform.localHostname}'))
          .bytes;
      return encrypt.Key(Uint8List.fromList(rawKey));
    } catch (_) {
      return null;
    }
  }
}

encrypt.Key _derivePbkdf2Key(
  String userSecret,
  String saltStr,
  String installId,
) {
  final passwordBytes = utf8.encode(userSecret);
  final saltBytes = utf8.encode('$saltStr:$installId');
  final derivator = PBKDF2KeyDerivator(HMac(SHA256Digest(), 64))
    ..init(Pbkdf2Parameters(Uint8List.fromList(saltBytes), 10000, 32));
  return encrypt.Key(derivator.process(Uint8List.fromList(passwordBytes)));
}
