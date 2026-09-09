/// Web build of `hostd_control_client.dart` -- `aura_hostd` is a native process; there is nothing
/// on web for this to talk to.
library;

class HostdControlUnavailable implements Exception {
  HostdControlUnavailable(this.message);

  final String message;

  @override
  String toString() => message;
}

class HostdControlError implements Exception {
  HostdControlError(this.statusCode, this.message);

  final int statusCode;
  final String message;

  @override
  String toString() => 'HTTP $statusCode: $message';
}

class HostdControlClient {
  HostdControlClient();

  Never _unavailable() =>
      throw HostdControlUnavailable('The desktop Engineer daemon is not available on web.');

  Future<Map<String, dynamic>> status() async => _unavailable();
  Future<List<Map<String, dynamic>>> roots() async => _unavailable();
  Future<Map<String, dynamic>> grantRoot(String path, {bool trusted = false}) async => _unavailable();
  Future<void> revokeRoot(String path) async => _unavailable();
  Future<void> setTrusted(String path, bool trusted) async => _unavailable();
  Future<Map<String, dynamic>> listDirectory({required String root, String path = ''}) async =>
      _unavailable();
  Future<Map<String, dynamic>> readFile({required String root, required String path}) async =>
      _unavailable();
  Future<Map<String, dynamic>> startPairing({String? backendUrl}) async => _unavailable();
  Future<Map<String, dynamic>> pairingStatus() async => _unavailable();
  Future<void> shutdown() async => _unavailable();
  void close() {}
}
