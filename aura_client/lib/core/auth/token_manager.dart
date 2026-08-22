import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;
import '../config/config.dart';
import '../security/device_validation_service.dart';
import '../../features/auth/presentation/auth_provider.dart';
import 'refresh_token_store.dart';

/// Manages the backend-issued access/refresh JWT pair (Doc/V2_BLUEPRINT.md
/// §9), shared by every v3 API client (SyncClient, InsightsClient,
/// SseAgentClient) so token exchange/caching/refresh logic lives in one
/// place instead of being duplicated three times.
class TokenManager {
  final Ref _ref;
  final http.Client? _http;
  final RefreshTokenStore _store;
  static const String _authUrl = "${AppConfig.backendUrl}/api/v3/auth";

  String? _accessToken;
  DateTime? _accessTokenExpiry;
  String? _refreshToken;

  /// Shared by concurrent [getValidAccessToken] callers so a rotated
  /// refresh token is not spent twice (AURA-010).
  Future<String?>? _inFlightAccessToken;

  TokenManager(
    this._ref, {
    http.Client? httpClient,
    RefreshTokenStore? store,
  })  : _http = httpClient,
        _store = store ?? const SecureRefreshTokenStore();

  Future<void> _loadCachedRefreshToken() async {
    _refreshToken ??= await _store.read();
  }

  /// True when a refresh token is on disk. Does not hit the network;
  /// callers that need a live JWT should use [getValidAccessToken].
  Future<bool> hasCachedRefreshToken() async {
    await _loadCachedRefreshToken();
    return _refreshToken != null && _refreshToken!.isNotEmpty;
  }

  bool _hasFreshAccessToken() {
    return _accessToken != null &&
        _accessTokenExpiry != null &&
        DateTime.now().isBefore(_accessTokenExpiry!.subtract(const Duration(seconds: 30)));
  }

  /// Returns a valid access token, transparently refreshing or
  /// re-exchanging as needed. Returns null if no path succeeded (backend
  /// hasn't deployed /api/v3/auth yet, device offline, etc.) — every v3
  /// client already degrades gracefully when its HTTP call fails, so a
  /// null token just becomes "sync/insights/chat unavailable right now"
  /// rather than a crash.
  ///
  /// Concurrent callers share one in-flight refresh/exchange. The refresh
  /// token is not cleared here or by [invalidateAccessToken]; only a
  /// failed `/refresh` response does that.
  Future<String?> getValidAccessToken() {
    if (_hasFreshAccessToken()) {
      debugPrint("[TokenManager] using cached access token");
      return Future<String?>.value(_accessToken);
    }

    final inFlight = _inFlightAccessToken;
    if (inFlight != null) return inFlight;

    final request = _refreshOrExchange();
    _inFlightAccessToken = request;
    return request.whenComplete(() {
      if (identical(_inFlightAccessToken, request)) {
        _inFlightAccessToken = null;
      }
    });
  }

  Future<String?> _refreshOrExchange() async {
    if (_hasFreshAccessToken()) return _accessToken;

    await _loadCachedRefreshToken();
    if (_refreshToken != null) {
      final refreshed = await _tryRefresh();
      if (refreshed) return _accessToken;
    }

    return _tryExchange();
  }

  /// Drop the in-memory access token so the next [getValidAccessToken]
  /// refreshes or re-exchanges. Call this when a v3 endpoint returns 401
  /// "Invalid access token" — otherwise the same rejected JWT is reused
  /// until its local expiry (up to 15 minutes). Does not touch the refresh
  /// token; rotation only happens when `/refresh` itself fails.
  void invalidateAccessToken() {
    debugPrint("[TokenManager] clearing rejected access token");
    _accessToken = null;
    _accessTokenExpiry = null;
  }

  Future<http.Response> _post(String url, {Map<String, String>? headers, Object? body}) {
    final uri = Uri.parse(url);
    final client = _http;
    final sent = client != null
        ? client.post(uri, headers: headers, body: body)
        : http.post(uri, headers: headers, body: body);
    return sent.timeout(const Duration(seconds: 10));
  }

  Future<bool> _tryRefresh() async {
    try {
      final response = await _post(
        "$_authUrl/refresh",
        headers: {"Content-Type": "application/json"},
        body: jsonEncode({"refresh_token": _refreshToken}),
      );

      if (response.statusCode != 200) {
        debugPrint(
          "[TokenManager] refresh failed: ${response.statusCode} ${response.body}",
        );
        // Refresh token rejected (expired/revoked/already rotated) —
        // clear it so the next call falls through to a fresh exchange
        // instead of retrying the same dead token forever.
        await _clearRefreshToken();
        return false;
      }
      await _applyTokenResponse(jsonDecode(response.body) as Map<String, dynamic>);
      debugPrint("[TokenManager] refresh succeeded");
      return true;
    } catch (e) {
      debugPrint("[TokenManager] refresh failed: $e");
      return false;
    }
  }

  Future<String?> _tryExchange() async {
    try {
      final idToken = await _ref.read(authProvider.notifier).getIdToken();
      if (idToken == null) {
        debugPrint("[TokenManager] token exchange skipped: no Google ID token");
        return null;
      }

      final headers = {"Authorization": "Bearer $idToken"};
      headers.addAll(await DeviceValidationService.getAttestationHeaders());

      final response = await _post("$_authUrl/token", headers: headers);
      if (response.statusCode != 200) {
        debugPrint(
          "[TokenManager] token exchange failed: ${response.statusCode} ${response.body}",
        );
        return null;
      }

      await _applyTokenResponse(jsonDecode(response.body) as Map<String, dynamic>);
      debugPrint("[TokenManager] token exchange succeeded");
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
    await _store.write(_refreshToken!);
  }

  Future<void> _clearRefreshToken() async {
    _refreshToken = null;
    await _store.delete();
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
