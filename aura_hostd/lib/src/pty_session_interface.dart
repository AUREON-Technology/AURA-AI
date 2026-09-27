/// Shared contract between [PtySession] (Windows ConPTY, `pty_session.dart`) and
/// [PosixPtySession] (Linux/macOS, `posix_pty_session.dart`) -- lets
/// `terminal_session_manager.dart` hold one session type without caring which OS
/// backend it is, the same way the client's `EditorProvider` abstraction hides the
/// concrete editor package.
library;

import 'dart:typed_data';

abstract class PtySessionInterface {
  String get sessionId;
  String get workingDirectory;

  Stream<Uint8List> get output;
  Future<int> get exitCode;

  bool get isChildAlive;
  int get pid;

  Future<void> start();
  int write(Uint8List bytes);
  void resize(int cols, int rows);
  Future<void> dispose();
}

class PtyException implements Exception {
  PtyException(this.message);
  final String message;
  @override
  String toString() => 'PtyException: $message';
}
