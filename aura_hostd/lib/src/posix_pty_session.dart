/// Real POSIX pseudo-terminal session for Linux/macOS -- the non-Windows counterpart to
/// [PtySession] (`pty_session.dart`'s Win32 ConPTY implementation). See docs/architecture/015-
/// desktop-ide.md (D4) and the 2026-09-25 Phase 0 audit §3, which flagged the absence of any
/// POSIX PTY backend at all -- `hostd-ci.yml` ran `ubuntu-latest` but the terminal feature was
/// entirely Windows-only in source until this file.
///
/// **STATUS: UNTESTED / UNVERIFIED.** Written without a Dart toolchain available to compile or
/// run it (this environment has neither `dart` nor a Linux/macOS box). The ioctl request numbers
/// for `TIOCSWINSZ`/`TIOCSCTTY` are cited against real sources (Linux: the universally-stable
/// `asm-generic/ioctls.h` values; Darwin: Apple's own `swift/stdlib/public/Platform/
/// TiocConstants.swift`), and `struct winsize`'s 4x-`unsigned short` layout is identical on both
/// platforms, but nothing here has executed on a real Linux or macOS machine. Do not treat this
/// as proven; a real run (or CI on `ubuntu-latest` / a macOS runner) is the actual verification,
/// same "built, not proven" discipline this repo's own roadmap applies everywhere else.
///
/// **Deliberately does *not* call `fork()` from Dart.** An earlier draft of this file did, and
/// was caught before landing: the Dart VM is multi-threaded internally (GC, JIT, async I/O
/// helper threads), and POSIX `fork()` only duplicates the *calling* thread -- any lock another
/// VM thread holds at the moment of `fork()` stays locked forever in the child, which is a
/// well-known deadlock hazard specific to forking a multi-threaded process. `dart:io`'s own
/// `Process.start` never exposes raw `fork()` to userland Dart for exactly this reason; its
/// internal fork+exec is engineered by the Dart SDK team to only call async-signal-safe
/// functions in the fork/exec window. This file gets the same safety by never doing that dance
/// itself: it opens the pty master (a handful of ordinary, non-forking libc calls -- safe from
/// any isolate), then spawns a tiny Perl one-liner through `Process.start` (the VM's own vetted
/// fork+exec). Perl -- present by default on every currently-shipping macOS and the overwhelming
/// majority of Linux distributions (Alpine-based minimal containers are the known gap) -- runs in
/// a freshly `exec`'d, single-threaded process image at that point, where it is completely safe
/// for *it* to call `setsid()`/`ioctl(TIOCSCTTY)`/redirect its own stdio and `exec` the real
/// shell in place (same OS pid throughout, so `Process.pid`/`Process.exitCode` below correctly
/// track the eventual shell).
library;

import 'dart:async';
import 'dart:ffi';
import 'dart:io';
import 'dart:isolate';
import 'dart:typed_data';

import 'package:ffi/ffi.dart';

import 'pty_session_interface.dart';

const int _oRdwr = 2; // O_RDWR -- portable POSIX value, identical on Linux and Darwin.

/// `TIOCSWINSZ`/`TIOCSCTTY` ioctl request numbers. Not portable constants -- BSD-style ioctl
/// encoding differs between Linux's `asm-generic` numbering and Darwin's `_IOW`/`_IO` macros --
/// so both platforms are hand-pinned here rather than computed, each against a real source:
/// Linux from `asm-generic/ioctls.h` (stable for decades, used identically by every PTY
/// implementation from Python's `pty` module to `tmux`); Darwin from Apple's own
/// `TiocConstants.swift` in the Swift standard library source, plus corroborated by `darwin-xnu`'s
/// `bsd/sys/ioctl.h`.
final int _tiocswinsz = Platform.isMacOS ? 0x80087467 : 0x5414;
final int _tiocsctty = Platform.isMacOS ? 0x20007461 : 0x540E;

final class _Winsize extends Struct {
  @Uint16()
  external int wsRow;
  @Uint16()
  external int wsCol;
  @Uint16()
  external int wsXpixel;
  @Uint16()
  external int wsYpixel;
}

typedef _Int32FnInt32 = Int32 Function(Int32);
typedef _IntFnInt = int Function(int);
typedef _PtsNameRNative = Int32 Function(Int32, Pointer<Uint8>, IntPtr);
typedef _PtsNameRDart = int Function(int, Pointer<Uint8>, int);
typedef _ReadWriteNative = IntPtr Function(Int32, Pointer<Uint8>, IntPtr);
typedef _ReadWriteDart = int Function(int, Pointer<Uint8>, int);
typedef _CloseNative = Int32 Function(Int32);
typedef _CloseDart = int Function(int);
typedef _IoctlWinsizeNative = Int32 Function(Int32, IntPtr, Pointer<_Winsize>);
typedef _IoctlWinsizeDart = int Function(int, int, Pointer<_Winsize>);

/// Thin lookup table over already-linked-in libc symbols. `DynamicLibrary.process()` (rather than
/// a hardcoded `.so`/`.dylib` path) is the documented, cross-platform-correct way to reach libc
/// from Dart FFI on both Linux and macOS: every Dart executable already dynamically links against
/// it, so no `dlopen` of a separate library is needed.
class _Libc {
  _Libc._() {
    final lib = DynamicLibrary.process();
    posixOpenpt = lib.lookupFunction<_Int32FnInt32, _IntFnInt>('posix_openpt');
    grantpt = lib.lookupFunction<_Int32FnInt32, _IntFnInt>('grantpt');
    unlockpt = lib.lookupFunction<_Int32FnInt32, _IntFnInt>('unlockpt');
    ptsnameR = lib.lookupFunction<_PtsNameRNative, _PtsNameRDart>('ptsname_r');
    read = lib.lookupFunction<_ReadWriteNative, _ReadWriteDart>('read');
    write = lib.lookupFunction<_ReadWriteNative, _ReadWriteDart>('write');
    close = lib.lookupFunction<_CloseNative, _CloseDart>('close');
    ioctlWinsize = lib.lookupFunction<_IoctlWinsizeNative, _IoctlWinsizeDart>('ioctl');
  }

  /// One instance per isolate (each isolate has independent static state in Dart, so the reader
  /// isolate spawned below re-resolves its own copy of these symbols -- harmless, matches how the
  /// Windows implementation re-derives its own state across the isolate boundary too).
  static final _Libc instance = _Libc._();

  late final int Function(int oflag) posixOpenpt;
  late final int Function(int fd) grantpt;
  late final int Function(int fd) unlockpt;
  late final _PtsNameRDart ptsnameR;
  late final _ReadWriteDart read;
  late final _ReadWriteDart write;
  late final int Function(int fd) close;
  late final _IoctlWinsizeDart ioctlWinsize;
}

/// Perl one-liner run as the freshly-`exec`'d child (see this file's top doc comment for why).
/// Args (`@ARGV`): `[slavePath, tiocsctty, shellExecutable]`. No shell is ever invoked to build
/// this command line -- `Process.start` passes argv directly via `execve`, so there is no
/// shell-quoting/injection surface for `slavePath`/`shellExecutable` at all.
const String _perlHelperScript = r'''
use POSIX qw(setsid);
my ($slave, $tiocsctty, $shell) = @ARGV;
setsid() or die "setsid: $!";
open(my $fh, "+<", $slave) or die "open $slave: $!";
ioctl($fh, $tiocsctty + 0, 0) or die "ioctl TIOCSCTTY: $!";
open(STDIN, "<&", $fh) or die "dup stdin: $!";
open(STDOUT, ">&", $fh) or die "dup stdout: $!";
open(STDERR, ">&", $fh) or die "dup stderr: $!";
close($fh);
exec { $shell } $shell or die "exec $shell: $!";
''';

class PosixPtySession implements PtySessionInterface {
  PosixPtySession({
    required this.sessionId,
    required this.workingDirectory,
    String? shellExecutable,
    this.initialCols = 80,
    this.initialRows = 24,
  }) : shellExecutable = shellExecutable ?? _defaultShell();

  /// `$SHELL` reflects the user's actual configured shell (zsh on modern macOS, whatever the
  /// human picked on Linux) -- falls back to a fixed path only when unset, matching the plan's
  /// "`$SHELL` or `/bin/bash`" / "`$SHELL` or `/bin/zsh`" guidance.
  static String _defaultShell() {
    final fromEnv = Platform.environment['SHELL'];
    if (fromEnv != null && fromEnv.isNotEmpty) return fromEnv;
    return Platform.isMacOS ? '/bin/zsh' : '/bin/bash';
  }

  @override
  final String sessionId;
  @override
  final String workingDirectory;
  final String shellExecutable;
  final int initialCols;
  final int initialRows;

  final StreamController<Uint8List> _outputController = StreamController<Uint8List>.broadcast();
  final Completer<int> _exitCodeCompleter = Completer<int>();

  @override
  Stream<Uint8List> get output => _outputController.stream;
  @override
  Future<int> get exitCode => _exitCodeCompleter.future;

  int _masterFd = -1;
  Process? _process;
  bool _disposed = false;
  bool _exited = false;
  ReceivePort? _receivePort;
  Isolate? _readerIsolate;

  @override
  bool get isChildAlive => _process != null && !_exited;

  /// The eventual shell's OS pid -- correct because the Perl helper `exec`s in place rather than
  /// forking again, so the pid `Process.start` reports never changes.
  @override
  int get pid => _process?.pid ?? 0;

  @override
  Future<void> start() async {
    final libc = _Libc.instance;
    final masterFd = libc.posixOpenpt(_oRdwr);
    if (masterFd < 0) {
      throw PtyException('posix_openpt failed');
    }
    if (libc.grantpt(masterFd) != 0) {
      libc.close(masterFd);
      throw PtyException('grantpt failed');
    }
    if (libc.unlockpt(masterFd) != 0) {
      libc.close(masterFd);
      throw PtyException('unlockpt failed');
    }
    final slavePath = _ptsName(masterFd);
    if (slavePath == null) {
      libc.close(masterFd);
      throw PtyException('ptsname_r failed');
    }
    _masterFd = masterFd;

    try {
      _process = await Process.start(
        'perl',
        ['-e', _perlHelperScript, slavePath, '$_tiocsctty', shellExecutable],
        workingDirectory: workingDirectory,
      );
    } catch (error) {
      libc.close(masterFd);
      _masterFd = -1;
      throw PtyException('failed to spawn pty helper process (is perl on PATH?): $error');
    }
    unawaited(_process!.exitCode.then((code) {
      _exited = true;
      if (!_exitCodeCompleter.isCompleted) _exitCodeCompleter.complete(code);
      if (!_outputController.isClosed) unawaited(_outputController.close());
    }));
    // These pipes exist (Process.start always creates them) but go unused: the Perl helper
    // redirects its own fds 0/1/2 onto the pty slave before the real shell ever writes anything,
    // so nothing arrives on them. Real output is read from [_masterFd] below instead.
    unawaited(_process!.stdout.drain<void>());
    unawaited(_process!.stderr.drain<void>());

    await _startReaderIsolate(masterFd);
  }

  String? _ptsName(int fd) {
    const bufferSize = 256;
    final buf = calloc<Uint8>(bufferSize);
    try {
      final rc = _Libc.instance.ptsnameR(fd, buf, bufferSize);
      if (rc != 0) return null;
      return buf.cast<Utf8>().toDartString();
    } finally {
      calloc.free(buf);
    }
  }

  Future<void> _startReaderIsolate(int fd) async {
    _receivePort = ReceivePort();
    _readerIsolate = await Isolate.spawn(_readLoop, [fd, _receivePort!.sendPort]);
    _receivePort!.listen((message) {
      if (message is Uint8List) {
        if (!_outputController.isClosed) _outputController.add(message);
      } else {
        // The master read end breaks (EIO/EOF) once the slave side is gone -- the normal way
        // this loop ends when the child exits, mirroring pty_session.dart's own reader isolate.
        if (!_outputController.isClosed) unawaited(_outputController.close());
      }
    });
  }

  static void _readLoop(List<Object> args) {
    final fd = args[0] as int;
    final sendPort = args[1] as SendPort;
    final libc = _Libc.instance;
    const bufferSize = 4096;
    final buffer = calloc<Uint8>(bufferSize);
    try {
      while (true) {
        final n = libc.read(fd, buffer, bufferSize);
        if (n <= 0) break;
        sendPort.send(Uint8List.fromList(buffer.asTypedList(n)));
      }
    } finally {
      calloc.free(buffer);
      sendPort.send(null);
    }
  }

  @override
  int write(Uint8List bytes) {
    if (_disposed || _masterFd < 0 || bytes.isEmpty) return 0;
    final buffer = calloc<Uint8>(bytes.length);
    try {
      buffer.asTypedList(bytes.length).setAll(0, bytes);
      return _Libc.instance.write(_masterFd, buffer, bytes.length);
    } finally {
      calloc.free(buffer);
    }
  }

  @override
  void resize(int cols, int rows) {
    if (_disposed || _masterFd < 0) return;
    final ws = calloc<_Winsize>();
    try {
      ws.ref
        ..wsRow = rows
        ..wsCol = cols
        ..wsXpixel = 0
        ..wsYpixel = 0;
      _Libc.instance.ioctlWinsize(_masterFd, _tiocswinsz, ws);
    } finally {
      calloc.free(ws);
    }
  }

  @override
  Future<void> dispose() async {
    if (_disposed) return;
    _disposed = true;
    if (_masterFd >= 0) {
      _Libc.instance.close(_masterFd);
      _masterFd = -1;
    }
    if (_process != null && !_exited) {
      _process!.kill(ProcessSignal.sigkill);
    }
    _readerIsolate?.kill(priority: Isolate.immediate);
    _receivePort?.close();
    if (!_outputController.isClosed) await _outputController.close();
  }
}
