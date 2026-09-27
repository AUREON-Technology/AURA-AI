/// Real Windows ConPTY-backed pseudo-terminal session.
///
/// Spawns a shell attached to a Windows pseudoconsole (ConPTY), giving it raw
/// terminal semantics (resize, ANSI passthrough) that plain `Process.start`
/// piping cannot provide. See docs/architecture/015-desktop-ide.md (D4).
library;

import 'dart:async';
import 'dart:ffi';
import 'dart:isolate';
import 'dart:typed_data';

import 'package:ffi/ffi.dart';
import 'package:win32/win32.dart';

import 'pty_session_interface.dart';

/// One live ConPTY session: a pseudoconsole plus the child process attached
/// to it. Output is read on a dedicated [Isolate] because `ReadFile` on the
/// pipe handle blocks its calling thread for the life of the session --
/// running that on the main isolate would freeze every other RPC this daemon
/// serves for as long as a terminal tab is open.
class PtySession implements PtySessionInterface {
  PtySession({
    required this.sessionId,
    required this.workingDirectory,
    this.shellExecutable = 'powershell.exe',
    this.initialCols = 80,
    this.initialRows = 24,
  });

  final String sessionId;
  final String workingDirectory;
  final String shellExecutable;
  final int initialCols;
  final int initialRows;

  final StreamController<Uint8List> _outputController = StreamController<Uint8List>.broadcast();
  final Completer<int> _exitCodeCompleter = Completer<int>();

  Stream<Uint8List> get output => _outputController.stream;
  Future<int> get exitCode => _exitCodeCompleter.future;

  /// Whether the attached process is still running, checked via
  /// `GetExitCodeProcess` without blocking.
  bool get isChildAlive {
    if (_hProcess == 0) return false;
    final code = calloc<Uint32>();
    try {
      GetExitCodeProcess(_hProcess, code);
      return code.value == STILL_ACTIVE;
    } finally {
      calloc.free(code);
    }
  }

  /// The attached process's OS pid, once [start] has completed. 0 before then.
  int get pid => _pid;

  int _hPC = 0;
  int _pipeInWrite = 0;
  int _pipeOutRead = 0;
  int _hProcess = 0;
  int _hThread = 0;
  int _pid = 0;
  Pointer<Uint8>? _attributeList;
  ReceivePort? _receivePort;
  Isolate? _readerIsolate;
  Timer? _exitWatcher;
  bool _disposed = false;

  /// Starts the pseudoconsole and attaches [shellExecutable] to it.
  ///
  /// Sequence mirrors Microsoft's own ConPTY sample
  /// (microsoft/terminal/samples/ConPTY/MiniTerm): two pipes, then
  /// `CreatePseudoConsole`, then a `STARTUPINFOEX` carrying the pseudoconsole
  /// handle via `UpdateProcThreadAttribute`, then `CreateProcessW`.
  Future<void> start() async {
    final pipeInRead = calloc<IntPtr>();
    final pipeInWrite = calloc<IntPtr>();
    final pipeOutRead = calloc<IntPtr>();
    final pipeOutWrite = calloc<IntPtr>();
    try {
      if (CreatePipe(pipeInRead, pipeInWrite, nullptr, 0) == 0) {
        throw PtyException('CreatePipe (input) failed: ${GetLastError()}');
      }
      if (CreatePipe(pipeOutRead, pipeOutWrite, nullptr, 0) == 0) {
        throw PtyException('CreatePipe (output) failed: ${GetLastError()}');
      }
      _pipeInWrite = pipeInWrite.value;
      _pipeOutRead = pipeOutRead.value;

      final hPCPtr = calloc<IntPtr>();
      final sizePtr = calloc<COORD>();
      try {
        sizePtr.ref.X = initialCols;
        sizePtr.ref.Y = initialRows;
        final hr = CreatePseudoConsole(sizePtr.ref, pipeInRead.value, pipeOutWrite.value, 0, hPCPtr);
        if (hr != S_OK) {
          throw PtyException('CreatePseudoConsole failed: HRESULT 0x${hr.toRadixString(16)}');
        }
        _hPC = hPCPtr.value;
      } finally {
        calloc.free(hPCPtr);
        calloc.free(sizePtr);
      }

      // Ownership of these two ends transferred to the console host inside
      // CreatePseudoConsole; this process must not keep them open, or the
      // child's stdin/stdout pipes never see end-of-file when we tear down.
      CloseHandle(pipeInRead.value);
      CloseHandle(pipeOutWrite.value);

      _initializeStartupInfoAndSpawn();
    } finally {
      calloc.free(pipeInRead);
      calloc.free(pipeInWrite);
      calloc.free(pipeOutRead);
      calloc.free(pipeOutWrite);
    }

    await _startReaderIsolate();
    _startExitWatcher();
  }

  /// ConPTY's output pipe does **not** close when the attached process exits
  /// on its own (e.g. the user types `exit`) -- only `ClosePseudoConsole`
  /// does that, and nothing calls it automatically. Without this watcher,
  /// [exitCode] would hang forever for a session nobody explicitly disposes.
  /// Polling `GetExitCodeProcess` (cheap, non-blocking) is simpler and avoids
  /// another isolate/native-handle boundary versus a blocking
  /// `WaitForSingleObject` on its own thread; 300ms latency is fine for a
  /// terminal-exited notification.
  void _startExitWatcher() {
    _exitWatcher = Timer.periodic(const Duration(milliseconds: 300), (timer) {
      if (_disposed || isChildAlive) return;
      timer.cancel();
      _handleChildExited();
    });
  }

  void _handleChildExited() {
    final code = calloc<Uint32>();
    int exitCodeValue;
    try {
      GetExitCodeProcess(_hProcess, code);
      exitCodeValue = code.value;
    } finally {
      calloc.free(code);
    }
    // Closing the pseudoconsole now is what unblocks the reader isolate's
    // blocked ReadFile call (via ERROR_BROKEN_PIPE) so it can clean up and
    // signal `_receivePort`. Zero out `_hPC` so `dispose()` doesn't double-close.
    if (_hPC != 0) {
      ClosePseudoConsole(_hPC);
      _hPC = 0;
    }
    if (!_exitCodeCompleter.isCompleted) _exitCodeCompleter.complete(exitCodeValue);
  }

  void _initializeStartupInfoAndSpawn() {
    final attributeListSize = calloc<IntPtr>();
    final startupInfoEx = calloc<STARTUPINFOEX>();
    final processInfo = calloc<PROCESS_INFORMATION>();
    Pointer<Utf16>? commandLine;
    Pointer<Utf16>? cwd;
    try {
      // First call (null list) reports the required buffer size.
      InitializeProcThreadAttributeList(nullptr, 1, 0, attributeListSize);
      _attributeList = calloc<Uint8>(attributeListSize.value);
      if (InitializeProcThreadAttributeList(_attributeList!.cast(), 1, 0, attributeListSize) == 0) {
        throw PtyException('InitializeProcThreadAttributeList failed: ${GetLastError()}');
      }

      // PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE's "value" is the HPCON handle
      // itself, passed as the lpValue pointer's bit pattern -- not a pointer
      // to a value. This is documented Win32 behavior for this one attribute.
      if (UpdateProcThreadAttribute(
            _attributeList!.cast(),
            0,
            PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE,
            Pointer<Void>.fromAddress(_hPC),
            sizeOf<IntPtr>(),
            nullptr,
            nullptr,
          ) ==
          0) {
        throw PtyException('UpdateProcThreadAttribute failed: ${GetLastError()}');
      }

      startupInfoEx.ref.StartupInfo.cb = sizeOf<STARTUPINFOEX>();
      startupInfoEx.ref.lpAttributeList = _attributeList!.cast();

      commandLine = shellExecutable.toNativeUtf16();
      cwd = workingDirectory.toNativeUtf16();

      final ok = CreateProcess(
        nullptr,
        commandLine,
        nullptr,
        nullptr,
        0, // bInheritHandles=FALSE: ConPTY handles are passed via the attribute list, not inheritance.
        EXTENDED_STARTUPINFO_PRESENT,
        nullptr,
        cwd,
        startupInfoEx.cast<STARTUPINFO>(),
        processInfo,
      );
      if (ok == 0) {
        throw PtyException('CreateProcess failed: ${GetLastError()}');
      }
      _hProcess = processInfo.ref.hProcess;
      _hThread = processInfo.ref.hThread;
      _pid = processInfo.ref.dwProcessId;
    } finally {
      calloc.free(attributeListSize);
      calloc.free(startupInfoEx);
      calloc.free(processInfo);
      if (commandLine != null) calloc.free(commandLine);
      if (cwd != null) calloc.free(cwd);
    }
  }

  /// Bridges the blocking `ReadFile` loop on the output pipe into this
  /// isolate's [_outputController], via a long-lived reader [Isolate] fed
  /// only the raw pipe-handle `int` (a Win32 `HANDLE` is an opaque machine
  /// word, not an FFI [Pointer], so it round-trips across the isolate
  /// boundary as a plain primitive with no dangling-pointer risk).
  Future<void> _startReaderIsolate() async {
    _receivePort = ReceivePort();
    _readerIsolate = await Isolate.spawn(_readLoop, [_pipeOutRead, _receivePort!.sendPort]);
    _receivePort!.listen((message) {
      if (message is Uint8List) {
        if (!_outputController.isClosed) _outputController.add(message);
      } else {
        // Reader signals its pipe closed -- it has no idea what the real
        // exit code is (or even whether the child has exited yet: the pipe
        // can break for reasons other than the child dying), so it carries
        // no code. If the child has in fact already exited, resolve the
        // real exit code immediately via GetExitCodeProcess instead of
        // waiting for `_exitWatcher`'s next 300ms tick; if it hasn't,
        // leave `_exitCodeCompleter` alone and let that watcher resolve it
        // correctly once the child really exits.
        if (!_outputController.isClosed) _outputController.close();
        if (!_exitCodeCompleter.isCompleted && !isChildAlive) _handleChildExited();
      }
    });
  }

  static void _readLoop(List<Object> args) {
    final pipeHandle = args[0] as int;
    final sendPort = args[1] as SendPort;
    const bufferSize = 4096;
    final buffer = calloc<Uint8>(bufferSize);
    final bytesRead = calloc<Uint32>();
    try {
      while (true) {
        final ok = ReadFile(pipeHandle, buffer, bufferSize, bytesRead, nullptr);
        if (ok == 0) {
          // ERROR_BROKEN_PIPE (109) once the console host closes its end --
          // the normal, expected way this loop ends when the child exits.
          break;
        }
        final n = bytesRead.value;
        if (n == 0) break;
        sendPort.send(Uint8List.fromList(buffer.asTypedList(n)));
      }
    } finally {
      calloc.free(buffer);
      calloc.free(bytesRead);
      // No exit code here -- just "the pipe is gone now"; see the listener
      // in `_startReaderIsolate` for how the real exit code gets resolved.
      sendPort.send(null);
    }
  }

  /// Returns the actual bytes written on success, or a negative Win32 error
  /// code (as `-GetLastError()`) on failure -- diagnostic shape kept through
  /// Stage 2 while the ConPTY sequence itself is still being proven.
  int write(Uint8List bytes) {
    if (_disposed || _pipeInWrite == 0) return 0;
    final buffer = calloc<Uint8>(bytes.length);
    final bytesWritten = calloc<Uint32>();
    try {
      buffer.asTypedList(bytes.length).setAll(0, bytes);
      final ok = WriteFile(_pipeInWrite, buffer, bytes.length, bytesWritten, nullptr);
      if (ok == 0) return -GetLastError();
      return bytesWritten.value;
    } finally {
      calloc.free(buffer);
      calloc.free(bytesWritten);
    }
  }

  void resize(int cols, int rows) {
    if (_disposed || _hPC == 0) return;
    final sizePtr = calloc<COORD>();
    try {
      sizePtr.ref.X = cols;
      sizePtr.ref.Y = rows;
      ResizePseudoConsole(_hPC, sizePtr.ref);
    } finally {
      calloc.free(sizePtr);
    }
  }

  Future<void> dispose() async {
    if (_disposed) return;
    _disposed = true;
    _exitWatcher?.cancel();
    if (_hPC != 0) ClosePseudoConsole(_hPC);
    if (_pipeInWrite != 0) CloseHandle(_pipeInWrite);
    // _pipeOutRead is owned by the reader isolate's read loop; it exits on
    // its own once ClosePseudoConsole breaks the pipe, so no CloseHandle here.
    if (_hProcess != 0) {
      final stillRunning = WaitForSingleObject(_hProcess, 0) == WAIT_TIMEOUT;
      if (stillRunning) TerminateProcess(_hProcess, 1);
      CloseHandle(_hProcess);
    }
    if (_hThread != 0) CloseHandle(_hThread);
    if (_attributeList != null) {
      // win32 5.15.0 doesn't wrap DeleteProcThreadAttributeList; the memory
      // we allocated for it is still freed below, only the OS-internal
      // bookkeeping DeleteProcThreadAttributeList would release is skipped --
      // acceptable for one small (1-attribute) list per session.
      calloc.free(_attributeList!);
    }
    _readerIsolate?.kill(priority: Isolate.immediate);
    _receivePort?.close();
    if (!_outputController.isClosed) await _outputController.close();
  }
}
