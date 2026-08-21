import 'dart:convert';
import 'dart:io';
import 'dart:math';

import 'package:flutter/foundation.dart';

import 'app_attest_client.dart';
import 'hardware_attestation_client.dart';
import 'play_integrity_client.dart';

/// Honest device-attestation headers for backend `X-Device-Attestation`.
///
/// Debug/dev builds send `aura_dev_attestation`. Play Integrity cannot be
/// decoded for this package until it exists in Play Console under the same
/// Cloud project as the VPS service account. Release builds request a real
/// Play Integrity / App Attest token when the platform SDK can produce one.
class DeviceValidationService {
  static const String devToken = 'aura_dev_attestation';
  static const String unsupportedMarker = 'aura_attestation_unsupported';

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
  }) async {
    final isDebug = debugMode ?? kDebugMode;
    final platformName = platform ?? _platformName();
    final useDebugMock = isDebug;

    if (!useDebugMock &&
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
    if (sdk != null) {
      hardwareToken = await sdk.requestToken(nonce: _freshNonce());
    }

    final hasHardware = hardwareToken != null && hardwareToken.isNotEmpty;
    final attestation = DeviceAttestation(
      platform: platformName,
      token: hasHardware ? hardwareToken : unsupportedMarker,
      isHardwareAttestation: hasHardware,
      isDebugBypass: false,
    );
    _storeCache(attestation, isDebug: isDebug, platformName: platformName);
    return attestation;
  }

  /// Resolves device validation token and platform headers.
  static Future<Map<String, String>> getAttestationHeaders({
    bool? debugMode,
    String? platform,
    HardwareAttestationClient? client,
    String? backendUrl,
  }) async {
    final attestation = await resolve(
      debugMode: debugMode,
      platform: platform,
      client: client,
      backendUrl: backendUrl,
    );
    return {
      'X-Device-Platform': attestation.platform,
      'X-Device-Attestation': attestation.token,
    };
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
    if (Platform.isAndroid) return 'android';
    if (Platform.isIOS) return 'ios';
    if (Platform.isWindows) return 'windows';
    if (Platform.isMacOS) return 'macos';
    if (Platform.isLinux) return 'linux';
    return 'unknown';
  }
}

class DeviceAttestation {
  final String platform;
  final String token;
  final bool isHardwareAttestation;
  final bool isDebugBypass;

  const DeviceAttestation({
    required this.platform,
    required this.token,
    required this.isHardwareAttestation,
    required this.isDebugBypass,
  });
}
