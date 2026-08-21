import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// Shared [FlutterSecureStorage] so TokenManager, auth, automations, and the
/// DB encryption key all hit the same Keystore/Keychain options.
///
/// Independent `FlutterSecureStorage()` constructors can disagree after a
/// cipher change and wipe independently. The plugin defaults `resetOnError`
/// to true, which deleted all Keystore secrets after a cipher mismatch on
/// device. Tokens may still disappear (that only forces re-login). The
/// Drift DB key is dual-written to an app-support backup by
/// [EncryptionService] so a plugin failure does not rotate it.
const FlutterSecureStorage auraSecureStorage = FlutterSecureStorage(
  aOptions: AndroidOptions(resetOnError: false),
  iOptions: IOSOptions(accessibility: KeychainAccessibility.first_unlock),
  mOptions: MacOsOptions(accessibility: KeychainAccessibility.first_unlock),
  wOptions: WindowsOptions(),
);
