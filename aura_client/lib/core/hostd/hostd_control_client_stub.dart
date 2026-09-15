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
  Future<List<Map<String, dynamic>>> listApprovals() async => _unavailable();
  Future<void> respondToApproval(String id, bool approved) async => _unavailable();
  Future<Map<String, dynamic>> gitStatus({required String root}) async => _unavailable();
  Future<Map<String, dynamic>> gitDiff({required String root, String? file, bool staged = false}) async =>
      _unavailable();
  Future<Map<String, dynamic>> gitLog({required String root, int limit = 30}) async => _unavailable();
  Future<Map<String, dynamic>> gitStage({required String root, required List<String> paths}) async =>
      _unavailable();
  Future<Map<String, dynamic>> gitUnstage({required String root, required List<String> paths}) async =>
      _unavailable();
  Future<Map<String, dynamic>> gitCommit({required String root, required String message}) async =>
      _unavailable();
  Future<Map<String, dynamic>> gitCheckout({required String root, required String branch, bool create = false}) async =>
      _unavailable();
  Future<Map<String, dynamic>> diagnosticsDetect({required String root}) async => _unavailable();
  Future<Map<String, dynamic>> diagnosticsRun({required String root, required String tool}) async =>
      _unavailable();
  Future<List<Map<String, dynamic>>> tasksList() async => _unavailable();
  Future<Map<String, dynamic>> gitTaskDiff({required String taskKey, required String base}) async =>
      _unavailable();
  Future<Map<String, dynamic>> gitTaskDiffStat({required String taskKey, required String base}) async =>
      _unavailable();
  Future<Map<String, dynamic>> gitMergeTask({
    required String root,
    required String taskBranch,
    bool squash = false,
  }) async =>
      _unavailable();
  Future<Map<String, dynamic>> gitDiscardTask({required String taskKey}) async => _unavailable();
  Future<Map<String, dynamic>> startPairing({String? backendUrl}) async => _unavailable();
  Future<Map<String, dynamic>> pairingStatus() async => _unavailable();
  Future<void> shutdown() async => _unavailable();
  void close() {}
}
