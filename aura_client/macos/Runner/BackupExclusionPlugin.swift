import FlutterMacOS
import Foundation

/// Excludes a file from Time Machine/Finder device backups (C3). Used for
/// EncryptionKeyBackup's plaintext DB-key resilience file
/// (core/security/encryption_key_backup.dart) -- without this, the one
/// key that decrypts every synced field would be captured whole by any
/// standard macOS device backup.
final class BackupExclusionPlugin: NSObject {
  static let channelName = "com.auraai.aura_client/backup_exclusion"

  static func register(with messenger: FlutterBinaryMessenger) {
    let channel = FlutterMethodChannel(name: channelName, binaryMessenger: messenger)
    let instance = BackupExclusionPlugin()
    channel.setMethodCallHandler(instance.handle)
  }

  func handle(_ call: FlutterMethodCall, result: @escaping FlutterResult) {
    switch call.method {
    case "excludeFromBackup":
      guard let args = call.arguments as? [String: Any], let path = args["path"] as? String else {
        result(FlutterError(code: "BAD_ARGS", message: "path is required", details: nil))
        return
      }
      var url = URL(fileURLWithPath: path)
      var resourceValues = URLResourceValues()
      resourceValues.isExcludedFromBackup = true
      do {
        try url.setResourceValues(resourceValues)
        result(nil)
      } catch {
        result(
          FlutterError(
            code: "EXCLUDE_FAILED", message: error.localizedDescription, details: nil))
      }
    default:
      result(FlutterMethodNotImplemented)
    }
  }
}
