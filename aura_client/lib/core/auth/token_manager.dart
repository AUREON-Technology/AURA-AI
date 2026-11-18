import 'dart:convert';
import 'dart:math';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;
import '../config/backend_url_policy.dart';
import '../config/backend_url_provider.dart';
import '../security/device_validation_service.dart';
import '../../features/auth/presentation/auth_provider.dart';
import 'refresh_token_store.dart';

/// Why the most recent token exchange failed, so the UI can distinguish "this
/// device cannot authenticate at all" (won't self-resolve on retry) from a
/// transient network hiccup (will).
enum AuthExchangeFailure { none, deviceAttestationRejected, other }

class AuthExchangeFailureNotifier extends Notifier<AuthExchangeFailure> {
  @override
  AuthExchangeFailure build() => AuthExchangeFailure.none;

  void set(AuthExchangeFailure value) => state = value;
}

final authExchangeFailureProvider =
    NotifierProvider<AuthExchangeFailureNotifier, AuthExchangeFailure>(() {
  return AuthExchangeFailureNotifier();
});

/// How [TokenManager.startSession] ended.
enum SessionStart {
  /// The backend issued a session for this account.
  started,

  /// The backend answered, but rejected this device's attestation. The sign-in goes ahead (the
  /// app stays usable offline, and main.dart shows the "could not be verified" banner, C1), but
  /// nothing that needs the backend will work.
  deviceUnverified,

  /// The backend said no to this account (400/401/403). Signing in must stop here: letting the
  /// user in would leave them on a session nothing can renew (audit F-33).
  refused,

  /// Nothing conclusive came back (network, timeout, 5xx). The sign-in goes ahead and the
  /// exchange is retried on the first API call, as it always was.
  unreachable,
}

/// A device-scoped token issued by `POST /api/v3/auth/desktop/login` (ADR-011 Unit 7) --
/// see [TokenManager.exchangeDesktopLogin].
class DesktopDeviceToken {
  DesktopDeviceToken({required this.accessToken, required this.expiry});

  final String accessToken;
  final DateTime expiry;
}

/// Manages the backend-issued access/refresh JWT pair (Doc/V2_BLUEPRINT.md
/// §9), shared by every v3 API client (SyncClient, InsightsClient,
/// SseAgentClient) so token exchange/caching/refresh logic lives in one
/// place instead of being duplicated three times.
/// The attestation headers for one `/token` call. Injectable so tests can watch what each
/// exchange sends; the default never reuses an attestation (INT-011).
typedef TokenExchangeAttestation = Future<Map<String, String>> Function(String backendUrl);

Future<Map<String, String>> _freshAttestationHeaders(String backendUrl) =>
    DeviceValidationService.getAttestationHeaders(backendUrl: backendUrl, singleUse: true);

/// Sent on `/token` when a person has just signed in, as opposed to a silent re-exchange
/// (`api/v2/auth.py`'s `SIGN_IN_HEADER`). Only the former may start a fresh account under a
/// subject that was deleted (INT-010).
const signInHeader = 'X-Aura-Sign-In';

class TokenManager {
  final Ref _ref;
  final http.Client? _http;
  final RefreshTokenStore _store;
  final TokenExchangeAttestation _attestationHeaders;
  TokenManager(
    this._ref, {
    http.Client? httpClient,
    RefreshTokenStore? store,
    TokenExchangeAttestation? attestationHeaders,
    bool? isWeb,
  })  : _http = httpClient,
        _store = store ?? const SecureRefreshTokenStore(),
        _attestationHeaders = attestationHeaders ?? _freshAttestationHeaders,
        _isWeb = isWeb ?? kIsWeb;

  /// A browser has no device attestation, so it signs in through the customer route, whose
  /// sessions the backend caps at the `User` role (SaaS P1-B). Phones keep the attested route.
  final bool _isWeb;

  static String exchangePathFor({required bool isWeb}) => isWeb ? '/consumer/token' : '/token';

  /// Bumped whenever the session this manager holds is replaced or ended ([clear],
  /// [startSession]). A refresh or exchange still in flight from before compares it when its
  /// answer arrives and drops that answer, instead of writing a token into a session that was
  /// signed out meanwhile (audit INT-027).
  int _generation = 0;

  /// The `retry_key` of a `/refresh` that got no answer, with the token it was for. The next
  /// refresh of that same token re-sends it, so the server can tell a retry after a lost response
  /// from a replay of a rotated token (INT-012, `services/jwt_auth.REFRESH_RETRY_GRACE`).
  ({String token, String key})? _unansweredRefresh;

  /// Set when the backend answered `account_deleted`: the account no longer exists, and nothing
  /// may silently re-exchange its way back into it (INT-010).
  bool _accountDeleted = false;

  String get _authUrl => '${_ref.read(backendUrlProvider)}/api/v3/auth';
  String get _backendUrl => _ref.read(backendUrlProvider);

  String? _accessToken;
  DateTime? _accessTokenExpiry;
  String? _refreshToken;

  /// Shared by concurrent [getValidAccessToken] callers so a rotated
  /// refresh token is not spent twice (AURA-010).
  Future<String?>? _inFlightAccessToken;

  /// Set by [invalidateAccessToken]: the server has just answered 401, so the
  /// renewal that follows is the one that decides whether the session lives.
  bool _renewingAfterRejection = false;

  /// Whether the last renewal ended in a *refusal* -- `/refresh` or `/token`
  /// answered and said no, or there was nothing left to try with -- as
  /// opposed to a network failure, which says nothing about the session.
  bool _renewalRefused = false;

  Future<void> _loadCachedRefreshToken() async {
    _refreshToken ??= await _store.read();
  }

  /// True when a refresh token is on disk. Does not hit the network;
  /// callers that need a live JWT should use [getValidAccessToken].
  Future<bool> hasCachedRefreshToken() async {
    await _loadCachedRefreshToken();
    return _refreshToken != null && _refreshToken!.isNotEmpty;
  }

  static const _freshnessMargin = Duration(seconds: 30);

  bool _hasFreshAccessToken([Duration margin = _freshnessMargin]) {
    return _accessToken != null &&
        _accessTokenExpiry != null &&
        DateTime.now().isBefore(_accessTokenExpiry!.subtract(margin));
  }

  /// Like [getValidAccessToken], but renews a cached token that would expire within
  /// [minValidity] instead of handing it out.
  ///
  /// For a credential that has to stay good for a while after it is sent: the live-voice
  /// socket re-checks the token it holds every 60 s and closes with 4401 once it has expired
  /// (`aura_core/api/ws_auth.py`), so a `reauth` must carry a token with more than one
  /// re-check interval left, not one with 31 s. A separate method, not a parameter on
  /// [getValidAccessToken], so the subclasses tests stub that method with stay valid
  /// overrides; with no cached token it simply defers to [getValidAccessToken].
  Future<String?> getAccessTokenValidFor(Duration minValidity) {
    if (_accessToken == null || _accessTokenExpiry == null || _hasFreshAccessToken(minValidity)) {
      return getValidAccessToken();
    }
    final inFlight = _inFlightAccessToken;
    if (inFlight != null) return inFlight;
    final request = _refreshOrExchange(minValidity);
    _inFlightAccessToken = request;
    return request.whenComplete(() {
      if (identical(_inFlightAccessToken, request)) {
        _inFlightAccessToken = null;
      }
    });
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

  Future<String?> _refreshOrExchange([Duration margin = _freshnessMargin]) async {
    if (_hasFreshAccessToken(margin)) return _accessToken;

    final afterRejection = _renewingAfterRejection;
    _renewingAfterRejection = false;
    _renewalRefused = false;
    _accountDeleted = false;
    final generation = _generation;

    // A refresh that failed without being refused (network, 5xx) leaves the
    // session's fate unknown, whatever the exchange after it says.
    var refreshInconclusive = false;
    await _loadCachedRefreshToken();
    if (_refreshToken != null) {
      final refreshed = await _tryRefresh(generation);
      if (generation != _generation) return null; // signed out meanwhile (INT-027)
      if (refreshed) return _accessToken;
      if (_accountDeleted) return _endDeletedAccount();
      refreshInconclusive = !_renewalRefused;
    }

    final token = await _tryExchange(generation: generation);
    if (generation != _generation) return null;
    if (_accountDeleted) return _endDeletedAccount();
    // A 401, then a renewal the server refused: this session is over, and no
    // retry from here will bring it back. End it, so the router shows the
    // login page instead of every screen saying "Sign in again" with nothing
    // to press (audit F-33). A renewal that failed on the network does not
    // count -- that is "offline", not "signed out".
    if (token == null && afterRejection && _renewalRefused && !refreshInconclusive) {
      debugPrint("[TokenManager] session refused after a 401 -- ending it");
      await _ref.read(authProvider.notifier).expireSession();
    }
    return token;
  }

  /// The backend said `account_deleted` (INT-010): wipe this device's copy of the account and
  /// end the session, rather than re-exchanging into a recreated account.
  Future<String?> _endDeletedAccount() async {
    debugPrint("[TokenManager] the account was deleted -- ending the session and its local data");
    await _ref.read(authProvider.notifier).endDeletedAccount();
    return null;
  }

  static bool _isAccountDeleted(http.Response response) {
    if (response.statusCode != 401) return false;
    try {
      final detail = (jsonDecode(response.body) as Map<String, dynamic>)['detail'];
      return detail is Map && detail['error'] == 'account_deleted';
    } catch (_) {
      return false;
    }
  }

  static String _newRetryKey() {
    final random = Random.secure();
    return base64UrlEncode(List<int>.generate(24, (_) => random.nextInt(256)));
  }

  /// Drop the in-memory access token so the next [getValidAccessToken]
  /// refreshes or re-exchanges. Call this when a v3 endpoint returns 401
  /// "Invalid access token" — otherwise the same rejected JWT is reused
  /// until its local expiry (up to 15 minutes). Does not touch the refresh
  /// token; rotation only happens when `/refresh` itself fails.
  ///
  /// It also marks the next renewal as following a rejection: if that renewal
  /// is refused as well, the session is ended ([_refreshOrExchange]).
  void invalidateAccessToken() {
    debugPrint("[TokenManager] clearing rejected access token");
    _accessToken = null;
    _accessTokenExpiry = null;
    _renewingAfterRejection = true;
  }

  Future<http.Response> _post(String url, {Map<String, String>? headers, Object? body}) {
    final uri = Uri.parse(url);
    final client = _http;
    final sent = client != null
        ? client.post(uri, headers: headers, body: body)
        : http.post(uri, headers: headers, body: body);
    return sent.timeout(const Duration(seconds: 10));
  }

  Future<bool> _tryRefresh(int generation) async {
    final token = _refreshToken!;
    // INT-012: one random key per refresh attempt, re-sent unchanged if this attempt goes
    // unanswered -- a timeout cannot tell "not received" from "rotated, answer lost", and the
    // server accepts the same token again only together with the same key.
    final pending = _unansweredRefresh;
    final retryKey = (pending != null && pending.token == token) ? pending.key : _newRetryKey();
    _unansweredRefresh = (token: token, key: retryKey);
    try {
      final response = await _post(
        "$_authUrl/refresh",
        headers: {"Content-Type": "application/json"},
        body: jsonEncode({"refresh_token": token, "retry_key": retryKey}),
      );
      if (generation != _generation) {
        // Signed out (or into another session) while this was in flight (INT-027): its answer
        // belongs to a session that no longer exists here. A successor it minted is revoked so
        // it is not left live on the server.
        if (response.statusCode == 200) await _revokeIssued(response.body);
        return false;
      }
      if (response.statusCode != 429 && response.statusCode < 500) _unansweredRefresh = null;

      if (response.statusCode != 200) {
        debugPrint(
          "[TokenManager] refresh failed: ${response.statusCode} ${response.body}",
        );
        // Refresh token rejected (expired/revoked/already rotated) —
        // clear it so the next call falls through to a fresh exchange
        // instead of retrying the same dead token forever. A 5xx/429 is the
        // server failing to answer, not a verdict on the token: keep it, or
        // one bad minute upstream would cost the user their session.
        _renewalRefused = _isRefusal(response.statusCode);
        _accountDeleted = _isAccountDeleted(response);
        if (_renewalRefused) await _clearRefreshToken();
        return false;
      }
      await _applyTokenResponse(jsonDecode(response.body) as Map<String, dynamic>);
      debugPrint("[TokenManager] refresh succeeded");
      return true;
    } catch (e) {
      // No answer: the token is kept with its retry key (INT-012).
      debugPrint("[TokenManager] refresh failed: $e");
      return false;
    }
  }

  /// Best effort: revoke the refresh token in a response that arrived for a session this
  /// manager no longer holds.
  Future<void> _revokeIssued(String body) async {
    try {
      final issued = (jsonDecode(body) as Map<String, dynamic>)['refresh_token'];
      if (issued is String && issued.isNotEmpty) await _revoke(issued);
    } catch (_) {}
  }

  /// Exchanges the Google ID token a sign-in just produced for a backend session, at sign-in
  /// rather than on the first API call. Before this, a sign-in the backend would refuse still
  /// "succeeded" and left the user on a session that could never work; a refusal now surfaces
  /// on the login page, and an attestation failure shows its banner straight away.
  ///
  /// It also settles what happens to a refresh token left on this device by an earlier session
  /// (signed out in the UI, but kept so it could be resumed). Left alone, a sign-in as a
  /// *different* account would keep renewing the old account's session: the local data is
  /// wiped for the new account while every API call still goes out as the old one. So once
  /// this sign-in goes ahead, the old token is revoked (or, if the server cannot be reached,
  /// forgotten locally). On [SessionStart.refused] the sign-in is abandoned and the old
  /// session is left exactly as it was.
  Future<SessionStart> startSession(String idToken) async {
    // A renewal of the previous session still in flight must not land on top of this one.
    final generation = ++_generation;
    _inFlightAccessToken = null;
    _unansweredRefresh = null;
    await _loadCachedRefreshToken();
    final cached = _refreshToken;
    final previous = (cached != null && cached.isNotEmpty) ? cached : null;

    final token = await _tryExchange(idToken: idToken, interactive: true, generation: generation);
    // Signed out again before the answer came: report it as not reaching the server, and touch
    // nothing (INT-027).
    if (generation != _generation) return SessionStart.unreachable;
    if (token != null) {
      if (previous != null && previous != _refreshToken) await _revoke(previous);
      return SessionStart.started;
    }

    if (_renewalRefused) return SessionStart.refused;

    final unverified = _ref.read(authExchangeFailureProvider) ==
        AuthExchangeFailure.deviceAttestationRejected;
    _accessToken = null;
    _accessTokenExpiry = null;
    if (previous != null) {
      // The server just answered an attestation rejection, so it can take a revoke too. When
      // it did not answer at all, a revoke would only wait out its own timeout.
      if (unverified) await _revoke(previous);
      await _clearRefreshToken();
    }
    return unverified ? SessionStart.deviceUnverified : SessionStart.unreachable;
  }

  Future<String?> _tryExchange({required int generation, String? idToken, bool interactive = false}) async {
    try {
      idToken ??= await _ref.read(authProvider.notifier).getIdToken();
      if (idToken == null) {
        debugPrint("[TokenManager] token exchange skipped: no Google ID token");
        // Nothing left to renew with: no refresh token got us here, and
        // there is no identity token to exchange.
        _renewalRefused = true;
        return null;
      }

      final headers = {"Authorization": "Bearer $idToken"};
      if (interactive) headers[signInHeader] = 'interactive';
      // Fresh every time: the server consumes the attestation's nonce here (INT-011). A browser
      // has none, and uses the customer route instead (SaaS P1-B).
      if (!_isWeb) headers.addAll(await _attestationHeaders(_backendUrl));

      final response = await _post("$_authUrl${exchangePathFor(isWeb: _isWeb)}", headers: headers);
      if (generation != _generation) {
        // Signed out while this was in flight (INT-027).
        if (response.statusCode == 200) await _revokeIssued(response.body);
        return null;
      }
      if (response.statusCode != 200) {
        debugPrint(
          "[TokenManager] token exchange failed: ${response.statusCode} ${response.body}",
        );
        DeviceValidationService.clearCache();
        if (_isAccountDeleted(response)) {
          _accountDeleted = true;
          _renewalRefused = true;
          return null;
        }
        final failure = classifyExchangeFailure(response.statusCode, response.body);
        if (failure == AuthExchangeFailure.deviceAttestationRejected) {
          final fallback = await _tryDebugOperatorExchange(idToken, generation);
          if (fallback != null) return fallback;
        }
        // A device-attestation 403 won't self-resolve on retry the way a
        // network hiccup will (C1) — surface it distinctly so the UI can
        // tell a user "this device could not be verified" instead of
        // silently degrading to "sync/chat unavailable" forever.
        _ref.read(authExchangeFailureProvider.notifier).set(failure);
        // An attestation 403 is about this device, not the session: signing
        // in again cannot fix it, so it must not bounce the user to login.
        _renewalRefused = failure != AuthExchangeFailure.deviceAttestationRejected &&
            _isRefusal(response.statusCode);
        return null;
      }

      await _applyTokenResponse(jsonDecode(response.body) as Map<String, dynamic>);
      _ref.read(authExchangeFailureProvider.notifier).set(AuthExchangeFailure.none);
      debugPrint("[TokenManager] token exchange succeeded");
      return _accessToken;
    } catch (e) {
      debugPrint("[TokenManager] exchange failed: $e");
      _ref.read(authExchangeFailureProvider.notifier).set(AuthExchangeFailure.other);
      // A thrown exchange is a network failure, not an answer.
      _renewalRefused = false;
      return null;
    }
  }

  /// Debug APKs are debug-signed and cannot satisfy production Play Integrity.
  /// Operators can still mint a session via `/web/token` (role-gated).
  Future<String?> _tryDebugOperatorExchange(String idToken, int generation) async {
    if (!kDebugMode) return null;
    if (!BackendUrlPolicy.isSafeForRelease(_backendUrl)) return null;
    try {
      final response = await _post(
        "$_authUrl/web/token",
        headers: {"Authorization": "Bearer $idToken"},
      );
      if (generation != _generation) {
        if (response.statusCode == 200) await _revokeIssued(response.body);
        return null;
      }
      if (response.statusCode != 200) return null;
      await _applyTokenResponse(jsonDecode(response.body) as Map<String, dynamic>);
      _ref.read(authExchangeFailureProvider.notifier).set(AuthExchangeFailure.none);
      debugPrint("[TokenManager] debug operator web/token succeeded");
      return _accessToken;
    } catch (e) {
      debugPrint("[TokenManager] debug operator web/token failed: $e");
      return null;
    }
  }

  /// Exchanges a real Google ID token obtained from the desktop loopback OAuth flow
  /// (`core/auth/desktop_oauth_client.dart`, ADR-011 Unit 7) for a *device-bound session* via
  /// `POST /api/v3/auth/desktop/login` -- distinct from [_tryExchange]'s `/token` path, which
  /// needs `google_sign_in` (unavailable on Linux/Windows) and device attestation (no desktop
  /// equivalent exists). Since audit FSA-004 the response is an ordinary short-lived access
  /// token plus a refresh token bound to this device (before, a 30-day token with no refresh),
  /// so it goes through [_applyTokenResponse] like every other sign-in and
  /// [getValidAccessToken]'s refresh machinery keeps the session alive across launches. The
  /// server lists the device under [deviceName] in Settings > Desktop devices, where it can be
  /// revoked; revocation ends the session at the next refresh.
  Future<DesktopDeviceToken?> exchangeDesktopLogin(
    String googleIdToken, {
    String? deviceName,
  }) async {
    lastDesktopLoginRefusal = null;
    try {
      final response = await _post(
        "$_authUrl/desktop/login",
        headers: {
          "Authorization": "Bearer $googleIdToken",
          "Content-Type": "application/json",
        },
        body: jsonEncode({"device_name": ?deviceName}),
      );
      if (response.statusCode != 200) {
        debugPrint(
          "[TokenManager] desktop login failed: ${response.statusCode} ${response.body}",
        );
        if (_isRefusal(response.statusCode)) {
          lastDesktopLoginRefusal = _serverDetail(response.body) ?? 'HTTP ${response.statusCode}';
        }
        _ref.read(authExchangeFailureProvider.notifier).set(AuthExchangeFailure.other);
        return null;
      }
      final body = jsonDecode(response.body) as Map<String, dynamic>;
      if (body['refresh_token'] is String) {
        await _applyTokenResponse(body);
      } else {
        // An older backend still answering with the refresh-less 30-day token.
        _accessToken = body['access_token'] as String;
        _accessTokenExpiry = DateTime.now().add(Duration(seconds: body['expires_in'] as int));
      }
      _ref.read(authExchangeFailureProvider.notifier).set(AuthExchangeFailure.none);
      debugPrint("[TokenManager] desktop login succeeded");
      return DesktopDeviceToken(accessToken: _accessToken!, expiry: _accessTokenExpiry!);
    } catch (e) {
      debugPrint("[TokenManager] desktop login failed: $e");
      _ref.read(authExchangeFailureProvider.notifier).set(AuthExchangeFailure.other);
      return null;
    }
  }

  /// Takes the desktop session the browser hand-off produced (`POST
  /// /api/v3/auth/desktop/firebase-exchange`, `core/auth/desktop_oauth_client.dart`'s
  /// `signInWithBrowserHandoff`, module readiness WP12) and makes it this manager's session,
  /// through the same [_applyTokenResponse] path [exchangeDesktopLogin] uses: the refresh token is
  /// persisted, so [getValidAccessToken] keeps the session alive across launches.
  ///
  /// The exchange already happened (the code is single-use and the verifier lives in that flow),
  /// so this makes no call of its own except, like [startSession], revoking a refresh token an
  /// earlier session left on this device -- otherwise a different account signing in here would
  /// leave the old account's session renewable on the server. A renewal of that earlier session
  /// still in flight is dropped rather than written over this one (INT-027).
  Future<DesktopDeviceToken> applyDesktopSession(Map<String, dynamic> tokenResponse) async {
    _generation++;
    _inFlightAccessToken = null;
    _unansweredRefresh = null;
    await _loadCachedRefreshToken();
    final previous = _refreshToken;
    await _applyTokenResponse(tokenResponse);
    if (previous != null && previous.isNotEmpty && previous != _refreshToken) {
      await _revoke(previous);
    }
    lastDesktopLoginRefusal = null;
    _ref.read(authExchangeFailureProvider.notifier).set(AuthExchangeFailure.none);
    return DesktopDeviceToken(accessToken: _accessToken!, expiry: _accessTokenExpiry!);
  }

  /// Why the server refused the last [exchangeDesktopLogin] (its `detail`), when it answered
  /// 400/401/403 -- for instance desktop sign-in being limited to operators (F-09). Null when
  /// that call succeeded or failed without an answer.
  String? lastDesktopLoginRefusal;

  static String? _serverDetail(String body) {
    try {
      final detail = (jsonDecode(body) as Map<String, dynamic>)['detail'];
      return detail is String && detail.isNotEmpty ? detail : null;
    } catch (_) {
      return null;
    }
  }

  /// Restores a previously-persisted desktop device token into memory without a fresh browser
  /// round trip -- see [exchangeDesktopLogin]'s doc comment for why the normal refresh/exchange
  /// path can't do this on its own for a token with no refresh token.
  void applyPersistedDesktopToken({required String accessToken, required DateTime expiry}) {
    _accessToken = accessToken;
    _accessTokenExpiry = expiry;
  }

  /// Pure classification, split out from [_tryExchange] so the C1 fix (does
  /// a device-attestation rejection get told apart from an ordinary
  /// transient failure?) is testable without exercising the full Google
  /// sign-in / HTTP stack.
  @visibleForTesting
  static AuthExchangeFailure classifyExchangeFailure(int statusCode, String body) {
    if (statusCode == 403 && body.contains('Device attestation validation failed')) {
      return AuthExchangeFailure.deviceAttestationRejected;
    }
    return AuthExchangeFailure.other;
  }

  /// An answer that says "no" to these credentials. A 5xx or 429 says the
  /// server could not answer, which is not the same thing.
  static bool _isRefusal(int statusCode) =>
      statusCode == 400 || statusCode == 401 || statusCode == 403;

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
  ///
  /// With [revoke], the refresh token is also revoked server-side first
  /// (best effort): forgetting it locally alone leaves it valid on the
  /// backend for its full 30-day lifetime, so a copy taken from this
  /// device before sign-out would keep working. A failed or offline
  /// revoke never blocks sign-out.
  ///
  /// It also ends this session for anything still in flight: a refresh or exchange answered
  /// after this point is discarded (and what it issued revoked) rather than written back
  /// (INT-027), and the cached device attestation is dropped (INT-011).
  Future<void> clear({bool revoke = false}) async {
    // Synchronously, before the first await, so a response already on its way sees it.
    _generation++;
    _inFlightAccessToken = null;
    _unansweredRefresh = null;
    DeviceValidationService.clearCache();
    if (revoke) {
      await _loadCachedRefreshToken();
      final token = _refreshToken;
      if (token != null && token.isNotEmpty) await _revoke(token);
    }
    _accessToken = null;
    _accessTokenExpiry = null;
    await _clearRefreshToken();
  }

  /// Best effort: a failed or offline revoke never blocks the caller.
  Future<void> _revoke(String refreshToken) async {
    try {
      await _post(
        "$_authUrl/revoke",
        headers: {"Content-Type": "application/json"},
        body: jsonEncode({"refresh_token": refreshToken}),
      );
    } catch (e) {
      debugPrint("[TokenManager] revoke failed (continuing): $e");
    }
  }
}

final tokenManagerProvider = Provider<TokenManager>((ref) => TokenManager(ref));
