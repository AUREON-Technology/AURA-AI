import CryptoKit
import DeviceCheck
import Flutter
import Foundation

/// App Attest / DeviceCheck channel. Returns a JSON envelope the backend
/// verifies with Apple's App Attest root (see services/attestation/app_attest.py).
final class AppAttestPlugin: NSObject {
  static let channelName = "com.auraai.aura_client/attestation"
  private let keyIdDefaultsKey = "aura.app_attest.key_id"
  private let attestationDefaultsKey = "aura.app_attest.attestation_b64"

  static func register(with messenger: FlutterBinaryMessenger) {
    let channel = FlutterMethodChannel(name: channelName, binaryMessenger: messenger)
    let instance = AppAttestPlugin()
    channel.setMethodCallHandler(instance.handle)
  }

  func handle(_ call: FlutterMethodCall, result: @escaping FlutterResult) {
    switch call.method {
    case "isAppAttestAvailable":
      if #available(iOS 14.0, *) {
        result(DCAppAttestService.shared.isSupported)
      } else {
        result(false)
      }
    case "requestAppAttestToken":
      let args = call.arguments as? [String: Any]
      let nonce = args?["nonce"] as? String ?? UUID().uuidString
      requestToken(nonce: nonce, result: result)
    default:
      result(FlutterMethodNotImplemented)
    }
  }

  private func requestToken(nonce: String, result: @escaping FlutterResult) {
    guard #available(iOS 14.0, *) else {
      result(
        FlutterError(
          code: "UNSUPPORTED",
          message: "App Attest requires iOS 14+",
          details: nil))
      return
    }
    let service = DCAppAttestService.shared
    guard service.isSupported else {
      result(
        FlutterError(
          code: "UNSUPPORTED",
          message: "App Attest is not supported on this device",
          details: nil))
      return
    }

    let clientDataHash = Data(SHA256.hash(data: Data(nonce.utf8)))
    if let keyId = UserDefaults.standard.string(forKey: keyIdDefaultsKey) {
      attestOrAssert(service: service, keyId: keyId, nonce: nonce, clientDataHash: clientDataHash, result: result)
      return
    }

    service.generateKey { keyId, error in
      if let error = error {
        result(FlutterError(code: "APP_ATTEST_KEY", message: error.localizedDescription, details: nil))
        return
      }
      guard let keyId = keyId else {
        result(FlutterError(code: "APP_ATTEST_KEY", message: "generateKey returned nil", details: nil))
        return
      }
      UserDefaults.standard.set(keyId, forKey: self.keyIdDefaultsKey)
      self.attestOrAssert(
        service: service,
        keyId: keyId,
        nonce: nonce,
        clientDataHash: clientDataHash,
        result: result)
    }
  }

  @available(iOS 14.0, *)
  private func attestOrAssert(
    service: DCAppAttestService,
    keyId: String,
    nonce: String,
    clientDataHash: Data,
    result: @escaping FlutterResult
  ) {
    service.attestKey(keyId, clientDataHash: clientDataHash) { attestation, error in
      if let attestation = attestation, error == nil {
        let b64 = attestation.base64EncodedString()
        UserDefaults.standard.set(b64, forKey: self.attestationDefaultsKey)
        result(self.envelope(keyId: keyId, nonce: nonce, attestation: b64, assertion: nil))
        return
      }
      // Key was already attested (or Apple refused a second attest). Send the
      // cached attestation object plus a fresh assertion when possible.
      service.generateAssertion(keyId, clientDataHash: clientDataHash) { assertion, assertError in
        if let assertError = assertError, assertion == nil {
          if let cached = UserDefaults.standard.string(forKey: self.attestationDefaultsKey) {
            result(self.envelope(keyId: keyId, nonce: nonce, attestation: cached, assertion: nil))
            return
          }
          result(
            FlutterError(
              code: "APP_ATTEST_FAILED",
              message: assertError.localizedDescription,
              details: nil))
          return
        }
        let cached = UserDefaults.standard.string(forKey: self.attestationDefaultsKey)
        result(
          self.envelope(
            keyId: keyId,
            nonce: nonce,
            attestation: cached,
            assertion: assertion?.base64EncodedString()))
      }
    }
  }

  private func envelope(keyId: String, nonce: String, attestation: String?, assertion: String?) -> String {
    var payload: [String: Any] = [
      "format": "apple-appattest",
      "key_id": keyId,
      "challenge": nonce,
    ]
    if let attestation = attestation {
      payload["attestation"] = attestation
    }
    if let assertion = assertion {
      payload["assertion"] = assertion
    }
    guard let data = try? JSONSerialization.data(withJSONObject: payload),
          let json = String(data: data, encoding: .utf8)
    else {
      return "{\"format\":\"apple-appattest\"}"
    }
    return json
  }
}
