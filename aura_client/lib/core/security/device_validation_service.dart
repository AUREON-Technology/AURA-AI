import 'dart:convert';
import 'dart:math';

import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;

import '../config/backend_url_policy.dart';
import '../config/config.dart';
import '../platform/client_language.dart';
import 'app_attest_client.dart';
import 'hardware_attestation_client.dart';
import 'play_integrity_client.dart';

/// Honest device-attestation headers for backend `X-Device-Attestation`.
///
/// Debug builds talking to a local gateway send `aura_dev_attestation`.
/// A debug APK aimed at production (`run_client_device.ps1`) must request
/// real Play Integrity / App Attest — staging/production refuse mock tokens.
class DeviceValidationService {
  static const String devToken = 'aura_dev_attestation';
  static const String unsupportedMarker = 'aura_attestation_unsupported';

  /// How long an attestation is reused for the ordinary API calls that attach these headers
  /// (KnowledgeCenter, VoiceClient, ...). Never for `/token`, which consumes the attestation's
  /// server nonce: see [getAttestationHeaders]'s `singleUse`.
  static const Duration tokenCacheTtl = Duration(minutes: 5);

  static DeviceAttestation? _cached;
  static DateTime? _cachedAt;
  static bool? _cachedDebug;
  static String? _cachedPlatform;

  static void clearCache() {
    _cached = null;
    _cachedAt = null;
    _cachedDebug = null;
    _cachedPlatform = null;
  }

  static Future<DeviceAttestation> resolve({
    bool? debugMode,
    String? platform,
    HardwareAttestationClient? client,
    String? backendUrl,
    http.Client? httpClient,
    bool singleUse = false,
  }) async {
    final isDebug = debugMode ?? kDebugMode;
    final platformName = platform ?? _platformName();
    final targetUrl = backendUrl ?? AppConfig.backendUrl;
    final useDebugMock =
        isDebug && !BackendUrlPolicy.isSafeForRelease(targetUrl);

    if (!useDebugMock &&
        !singleUse &&
        _cached != null &&
        _cachedAt != null &&
        _cachedDebug == isDebug &&
        _cachedPlatform == platformName &&
        DateTime.now().difference(_cachedAt!) < tokenCacheTtl) {
      return _cached!;
    }

    if (useDebugMock) {
      final attestation = DeviceAttestation(
        platform: platformName,
        token: devToken,
        isHardwareAttestation: false,
        isDebugBypass: true,
      );
      _storeCache(attestation, isDebug: isDebug, platformName: platformName);
      return attestation;
    }

    final sdk = client ?? _defaultClient(platformName);
    String? hardwareToken;
    String? nonce;
    if (sdk != null) {
      // Server-issued so the backend's replay-protection cache (H1) has
      // something to correlate this attestation call against — a
      // purely client-generated nonce can never be checked server-side.
      nonce = await _serverNonce(backendUrl: backendUrl, httpClient: httpClient);
      try {
        hardwareToken = await sdk.requestToken(nonce: nonce);
      } catch (error) {
        // An attestation SDK that fails must degrade to `unsupportedMarker`, never propagate:
        // every caller of `getAttestationHeaders` (KnowledgeCenter, VoiceClient, ...) attaches
        // these headers to an ordinary API call, so a throw here fails the *request* -- the user
        // sees "Ingestion failed" or "Could not reach AURA", with the real cause only in a log.
        // "The SDK could not produce a token" is already a first-class outcome below (the
        // `hasHardware` branch), and PlayIntegrityClient/AppAttestClient each already return null
        // for the two failure modes they anticipated (MissingPluginException, PlatformException);
        // this closes the gap for every mode they did not -- a Play Services build that throws
        // something else, a channel that isn't there at all. The marker sent is identical to the
        // no-token case, so the backend's decision about what to accept is unchanged.
        debugPrint('Device attestation SDK failed for $platformName: $error');
        hardwareToken = null;
      }
    }

    final hasHardware = hardwareToken != null && hardwareToken.isNotEmpty;
    final attestation = DeviceAttestation(
      platform: platformName,
      token: hasHardware ? hardwareToken : unsupportedMarker,
      isHardwareAttestation: hasHardware,
      isDebugBypass: false,
      nonce: hasHardware ? nonce : null,
    );
    if (singleUse) {
      // The server consumes this nonce at `/token`. Drop whatever is cached as well, rather
      // than keep an attestation from before a sign-in around.
      clearCache();
    } else {
      _storeCache(attestation, isDebug: isDebug, platformName: platformName);
    }
    return attestation;
  }

  /// Resolves device validation token, platform, and nonce headers.
  ///
  /// [singleUse] is for `POST /api/v3/auth/token`, the one call the backend checks the nonce on
  /// -- and consumes it (`services/security.py`, `nonce_replay`). A cached attestation there
  /// would replay a spent nonce on the next sign-in within [tokenCacheTtl] and be refused as an
  /// unverified device (audit INT-011), so it always gets a fresh server nonce and a fresh
  /// attestation, and is never cached.
  static Future<Map<String, String>> getAttestationHeaders({
    bool? debugMode,
    String? platform,
    HardwareAttestationClient? client,
    String? backendUrl,
    http.Client? httpClient,
    bool singleUse = false,
  }) async {
    final attestation = await resolve(
      debugMode: debugMode,
      platform: platform,
      client: client,
      backendUrl: backendUrl,
      httpClient: httpClient,
      singleUse: singleUse,
    );
    final headers = {
      'X-Device-Platform': attestation.platform,
      'X-Device-Attestation': attestation.token,
      // Rides with attestation because every API client already sends these headers, sign-in
      // included; it is not part of attestation itself (see client_language.dart).
      clientLanguageHeader: ClientLanguage.current(),
    };
    if (attestation.nonce != null) {
      headers['X-Device-Attestation-Nonce'] = attestation.nonce!;
    }
    return headers;
  }

  /// Fetches a single-use nonce from POST /api/v3/auth/nonce to bind into
  /// the attestation SDK call. Falls back to a locally-generated nonce on
  /// any network failure so a backend hiccup degrades to today's
  /// (unreplay-protected) behaviour rather than blocking attestation
  /// entirely -- the server-side check only runs when a nonce is present.
  static Future<String> _serverNonce({String? backendUrl, http.Client? httpClient}) async {
    try {
      final url = "${backendUrl ?? AppConfig.backendUrl}/api/v3/auth/nonce";
      final uri = Uri.parse(url);
      final sent = httpClient != null ? httpClient.post(uri) : http.post(uri);
      final response = await sent.timeout(const Duration(seconds: 5));
      if (response.statusCode == 200) {
        final body = jsonDecode(response.body) as Map<String, dynamic>;
        final nonce = body['nonce'] as String?;
        if (nonce != null && nonce.isNotEmpty) return nonce;
      }
    } catch (_) {
      // Network error, timeout, or malformed response — fall through.
    }
    return _freshNonce();
  }

  static HardwareAttestationClient? _defaultClient(String platform) {
    switch (platform) {
      case 'android':
        return PlayIntegrityClient();
      case 'ios':
        return AppAttestClient();
      default:
        return null;
    }
  }

  static void _storeCache(
    DeviceAttestation attestation, {
    required bool isDebug,
    required String platformName,
  }) {
    _cached = attestation;
    _cachedAt = DateTime.now();
    _cachedDebug = isDebug;
    _cachedPlatform = platformName;
  }

  static String _freshNonce() {
    final random = Random.secure();
    final bytes = List<int>.generate(16, (_) => random.nextInt(256));
    return base64UrlEncode(bytes);
  }

  static String _platformName() {
    if (kIsWeb) return 'web';
    switch (defaultTargetPlatform) {
      case TargetPlatform.android:
        return 'android';
      case TargetPlatform.iOS:
        return 'ios';
      case TargetPlatform.windows:
        return 'windows';
      case TargetPlatform.macOS:
        return 'macos';
      case TargetPlatform.linux:
        return 'linux';
      default:
        return 'unknown';
    }
  }
}

class DeviceAttestation {
  final String platform;
  final String token;
  final bool isHardwareAttestation;
  final bool isDebugBypass;

  /// The server-issued nonce bound into [token], when a hardware attestation
  /// was actually produced. Echoed back as X-Device-Attestation-Nonce so the
  /// backend's replay-protection cache can check it (H1).
  final String? nonce;

  const DeviceAttestation({
    required this.platform,
    required this.token,
    required this.isHardwareAttestation,
    required this.isDebugBypass,
    this.nonce,
  });
}
