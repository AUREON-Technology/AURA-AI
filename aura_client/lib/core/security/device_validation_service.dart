import 'dart:io';

class DeviceValidationService {
  /// Resolves device validation token and platform headers.
  /// Bypassed in dev workspaces, verified strictly in production FastAPI middleware.
  static Map<String, String> getAttestationHeaders() {
    String platform = "unknown";
    String attestationToken = "local_developer_trust_token";

    if (Platform.isAndroid) {
      platform = "android";
      attestationToken = "android_play_integrity_mock_assertion_token_string";
    } else if (Platform.isIOS) {
      platform = "ios";
      attestationToken = "ios_app_attest_mock_assertion_token_string";
    } else if (Platform.isWindows) {
      platform = "windows";
      attestationToken = "windows_hello_mock_attestation_token";
    } else if (Platform.isMacOS) {
      platform = "macos";
      attestationToken = "macos_device_mock_attestation_token";
    } else if (Platform.isLinux) {
      platform = "linux";
      attestationToken = "linux_desktop_mock_attestation_token";
    }

    return {
      "X-Device-Platform": platform,
      "X-Device-Attestation": attestationToken,
    };
  }
}
