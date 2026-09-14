/// Web build of `hostd_debug_client.dart` -- `aura_hostd` is a native process; there is nothing
/// on web for this to talk to.
library;

import 'dart:async';
import 'dart:typed_data';

class HostdDebugUnavailable implements Exception {
  HostdDebugUnavailable(this.message);
  final String message;
  @override
  String toString() => message;
}

class HostdDebugClient {
  HostdDebugClient();

  Stream<Uint8List> get dapBytes => const Stream.empty();
  Stream<int> get processExitCode => const Stream.empty();
  Stream<String> get errors => const Stream.empty();
  String? get sessionId => null;
  int? get pid => null;
  bool get isConnected => false;

  Future<void> connect({
    required String rootPath,
    required String scriptPath,
    List<String> args = const [],
  }) async {
    throw HostdDebugUnavailable('The desktop Engineer daemon is not available on web.');
  }

  void sendDap(Uint8List bytes) {}
  Future<void> close() async {}
  Future<void> dispose() async {}
}
