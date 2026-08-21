import 'dart:io';

import 'package:flutter/foundation.dart';

/// Honest device-attestation headers for backend `X-Device-Attestation`.
///
/// This app does not ship Play Integrity or App Attest SDKs, so it never
/// produces a hardware attestation token. Debug/dev builds send an explicit
/// `aura_dev_attestation` marker that the backend may accept only when
/// `ALLOW_MOCK_ATTESTATION` (or debug mode) is on. Release builds send
/// `aura_attestation_unsupported` so production verification rejects it
/// instead of treating a hardcoded mock as real.
class DeviceValidationService {
  static const String devToken = 'aura_dev_attestation';
  static const String unsupportedMarker = 'aura_attestation_unsupported';

  static DeviceAttestation resolve({bool? debugMode}) {
    final isDebug = debugMode ?? kDebugMode;
    return DeviceAttestation(
      platform: _platformName(),
      token: isDebug ? devToken : unsupportedMarker,
      isHardwareAttestation: false,
      isDebugBypass: isDebug,
    );
  }

  /// Resolves device validation token and platform headers.
  static Map<String, String> getAttestationHeaders({bool? debugMode}) {
    final attestation = resolve(debugMode: debugMode);
    return {
      'X-Device-Platform': attestation.platform,
      'X-Device-Attestation': attestation.token,
    };
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
