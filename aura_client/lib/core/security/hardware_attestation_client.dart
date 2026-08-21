/// Produces a hardware attestation token for `X-Device-Attestation`.
///
/// Debug builds never call this; they send [DeviceValidationService.devToken].
/// Release builds send whatever the platform SDK returns, or the unsupported
/// marker when the SDK cannot produce a token.
abstract class HardwareAttestationClient {
  /// Integrity token, App Attest JSON envelope, or `null` if unavailable.
  Future<String?> requestToken({String? nonce});
}

/// Shared MethodChannel used by Play Integrity (Android) and App Attest (iOS).
const String kAttestationMethodChannel = 'com.auraai.aura_client/attestation';
