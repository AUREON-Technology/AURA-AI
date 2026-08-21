import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import '../security/aura_secure_storage.dart';

/// Persistence for the v2 refresh token. Production uses Keystore/Keychain
/// via [auraSecureStorage]; tests inject an in-memory implementation so a
/// refresh round-trip does not touch the platform plugin.
abstract class RefreshTokenStore {
  Future<String?> read();
  Future<void> write(String token);
  Future<void> delete();
}

class SecureRefreshTokenStore implements RefreshTokenStore {
  const SecureRefreshTokenStore([this._storage = auraSecureStorage]);

  static const _key = 'v2_refresh_token';
  final FlutterSecureStorage _storage;

  @override
  Future<String?> read() => _storage.read(key: _key);

  @override
  Future<void> write(String token) => _storage.write(key: _key, value: token);

  @override
  Future<void> delete() => _storage.delete(key: _key);
}
