/// Web build of `hostd_terminal_client.dart` -- `aura_hostd` is a native
/// process; there is nothing on web for this to talk to.
library;

import 'dart:async';
import 'dart:typed_data';

import 'package:aura_client/l10n/l10n.dart';

class HostdTerminalUnavailable implements Exception {
  HostdTerminalUnavailable(this.message);
  final String message;
  @override
  String toString() => message;
}

class HostdTerminalClient {
  HostdTerminalClient();

  Stream<Uint8List> get output => const Stream.empty();
  Stream<int> get exitCode => const Stream.empty();
  Stream<String> get errors => const Stream.empty();
  String? get sessionId => null;
  bool get isConnected => false;

  Future<void> connect({required String rootPath, required int cols, required int rows}) async {
    throw HostdTerminalUnavailable(currentL10n.coreHostdNotOnWeb);
  }

  void sendInput(Uint8List bytes) {}
  void resize(int cols, int rows) {}
  Future<void> close() async {}
  Future<void> dispose() async {}
}
