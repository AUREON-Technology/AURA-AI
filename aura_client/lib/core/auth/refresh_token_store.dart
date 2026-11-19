import 'dart:convert';

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

/// A `/refresh` that was sent and got no answer: the token it presented and its `retry_key`.
typedef UnansweredRefresh = ({String token, String key});

/// Persistence for the [UnansweredRefresh] (INT-105), so it outlives the process.
///
/// A refresh whose response was lost may already have rotated the token on the server; only a
/// retry with the *same* `retry_key` is accepted as that request again
/// (`aura_backend/services/jwt_auth.py`, `REFRESH_RETRY_GRACE`). Held only in memory, the key was
/// gone once the app was killed, the next launch presented the token with a new key, and the
/// server treated it as a replay and revoked the whole token family. A [RefreshTokenStore] that
/// also implements this keeps the key next to the token; one that does not (test doubles) keeps
/// the in-memory behaviour.
abstract class RefreshRetryKeyStore {
  Future<UnansweredRefresh?> readUnanswered();
  Future<void> writeUnanswered(UnansweredRefresh pending);
  Future<void> deleteUnanswered();
}

class SecureRefreshTokenStore implements RefreshTokenStore, RefreshRetryKeyStore {
  const SecureRefreshTokenStore([this._storage = auraSecureStorage]);

  static const _key = 'v2_refresh_token';
  static const _unansweredKey = 'v2_refresh_unanswered';
  final FlutterSecureStorage _storage;

  @override
  Future<String?> read() => _storage.read(key: _key);

  @override
  Future<void> write(String token) => _storage.write(key: _key, value: token);

  @override
  Future<void> delete() => _storage.delete(key: _key);

  @override
  Future<UnansweredRefresh?> readUnanswered() async {
    try {
      final raw = await _storage.read(key: _unansweredKey);
      if (raw == null) return null;
      final decoded = jsonDecode(raw);
      if (decoded is Map && decoded['token'] is String && decoded['key'] is String) {
        return (token: decoded['token'] as String, key: decoded['key'] as String);
      }
    } catch (_) {
      // Unreadable: a fresh key is the old behaviour, not a failure.
    }
    return null;
  }

  @override
  Future<void> writeUnanswered(UnansweredRefresh pending) => _storage.write(
        key: _unansweredKey,
        value: jsonEncode({'token': pending.token, 'key': pending.key}),
      );

  @override
  Future<void> deleteUnanswered() => _storage.delete(key: _unansweredKey);
}
