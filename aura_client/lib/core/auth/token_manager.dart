import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:http/http.dart' as http;
import '../config/config.dart';
import '../security/device_validation_service.dart';
import '../../features/auth/presentation/auth_provider.dart';

/// Manages the backend-issued access/refresh JWT pair (Doc/V2_BLUEPRINT.md
/// §9), shared by every v2 API client (SyncClient, InsightsClient,
/// SseAgentClient) so token exchange/caching/refresh logic lives in one
/// place instead of being duplicated three times. v1 endpoints are
/// unaffected — they keep sending the raw Google ID token, unchanged.
class TokenManager {
  final Ref _ref;
  static const String _authUrl = "${AppConfig.backendUrl}/api/v2/auth";
  static const _storage = FlutterSecureStorage();
  static const _refreshTokenKey = 'v2_refresh_token';

  String? _accessToken;
  DateTime? _accessTokenExpiry;
  String? _refreshToken;

  TokenManager(this._ref);

  Future<void> _loadCachedRefreshToken() async {
    _refreshToken ??= await _storage.read(key: _refreshTokenKey);
  }

  /// Returns a valid access token, transparently refreshing or
  /// re-exchanging as needed. Returns null if no path succeeded (backend
  /// hasn't deployed /api/v2/auth yet, device offline, etc.) — every v2
  /// client already degrades gracefully when its HTTP call fails, so a
  /// null token just becomes "sync/insights/chat unavailable right now"
  /// rather than a crash.
  Future<String?> getValidAccessToken() async {
    if (_accessToken != null &&
        _accessTokenExpiry != null &&
        DateTime.now().isBefore(_accessTokenExpiry!.subtract(const Duration(seconds: 30)))) {
      return _accessToken;
    }

    await _loadCachedRefreshToken();
    if (_refreshToken != null) {
      final refreshed = await _tryRefresh();
      if (refreshed) return _accessToken;
    }

    return _tryExchange();
  }

  Future<bool> _tryRefresh() async {
    try {
      final response = await http
          .post(
            Uri.parse("$_authUrl/refresh"),
            headers: {"Content-Type": "application/json"},
            body: jsonEncode({"refresh_token": _refreshToken}),
          )
          .timeout(const Duration(seconds: 10));

      if (response.statusCode != 200) {
        // Refresh token rejected (expired/revoked/already rotated) —
        // clear it so the next call falls through to a fresh exchange
        // instead of retrying the same dead token forever.
        await _clearRefreshToken();
        return false;
      }
      await _applyTokenResponse(jsonDecode(response.body) as Map<String, dynamic>);
      return true;
    } catch (e) {
      debugPrint("[TokenManager] refresh failed: $e");
      return false;
    }
  }

  Future<String?> _tryExchange() async {
    try {
      final idToken = await _ref.read(authProvider.notifier).getIdToken();
      if (idToken == null) return null;

      final headers = {"Authorization": "Bearer $idToken"};
      headers.addAll(DeviceValidationService.getAttestationHeaders());

      final response = await http.post(Uri.parse("$_authUrl/token"), headers: headers).timeout(const Duration(seconds: 10));
      if (response.statusCode != 200) return null;

      await _applyTokenResponse(jsonDecode(response.body) as Map<String, dynamic>);
      return _accessToken;
    } catch (e) {
      debugPrint("[TokenManager] exchange failed: $e");
      return null;
    }
  }

  Future<void> _applyTokenResponse(Map<String, dynamic> body) async {
    _accessToken = body['access_token'] as String;
    _refreshToken = body['refresh_token'] as String;
    final expiresIn = body['expires_in'] as int;
    _accessTokenExpiry = DateTime.now().add(Duration(seconds: expiresIn));
    await _storage.write(key: _refreshTokenKey, value: _refreshToken);
  }

  Future<void> _clearRefreshToken() async {
    _refreshToken = null;
    await _storage.delete(key: _refreshTokenKey);
  }

  /// Call from AuthNotifier.signOut() so a signed-out session's refresh
  /// token can't be replayed after logout.
  Future<void> clear() async {
    _accessToken = null;
    _accessTokenExpiry = null;
    await _clearRefreshToken();
  }
}

final tokenManagerProvider = Provider<TokenManager>((ref) => TokenManager(ref));
