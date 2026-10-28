/// Real POSIX pseudo-terminal session for Linux/macOS -- the non-Windows counterpart to
/// [PtySession] (`pty_session.dart`'s Win32 ConPTY implementation). See docs/architecture/015-
/// desktop-ide.md (D4) and the 2026-09-25 Phase 0 audit §3, which flagged the absence of any
/// POSIX PTY backend at all -- `hostd-ci.yml` ran `ubuntu-latest` but the terminal feature was
/// entirely Windows-only in source until this file.
///
/// **STATUS: exercised on Linux, unverified on macOS.** `test/posix_pty_session_test.dart` runs a
/// real shell through this class on `ubuntu-latest` (Host Daemon CI). Nothing here has run on
/// macOS yet. The Darwin ioctl request numbers for `TIOCSWINSZ`/`TIOCSCTTY` are cited against
/// Apple's own `swift/stdlib/public/Platform/TiocConstants.swift`, the Linux ones against
/// `asm-generic/ioctls.h`, and `struct winsize`/`struct pollfd` have the same layout on both, but a
/// real macOS run is still the only verification for that platform.
///
/// **Shutdown.** The master is read by a blocking `poll()`+`read()` loop in its own isolate, and
/// nothing outside a native call can interrupt it: `Isolate.kill` is only honoured once the
/// isolate is back in Dart code, and `close()` from another thread does not wake a `read()`
/// already blocked on that fd on Linux. If the child dies before the helper has opened the slave
/// (dispose straight after start), the master never even sees a hangup. An earlier version of
/// this file relied on exactly those three things, and the stuck reader kept `dart test` from ever
/// exiting (Host Daemon CI ran into its job timeout every run). So the loop also polls a wake pipe,
/// [dispose] writes one byte to it, and the master and pipe fds are closed only after the reader
/// has reported that it has stopped touching them -- closing them earlier would let the fd number
/// be reused by an unrelated open() while a stray `read()` still names it.
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

// fcntl/poll/signal constants below are the same numbers on Linux and Darwin.
const int _fSetfd = 2; // F_SETFD
const int _fdCloexec = 1; // FD_CLOEXEC
const int _pollIn = 0x001; // POLLIN
const int _pollErr = 0x008; // POLLERR
const int _pollHup = 0x010; // POLLHUP
const int _pollNval = 0x020; // POLLNVAL
const int _sigkill = 9; // SIGKILL

/// How long [PosixPtySession.dispose] waits for the reader isolate to acknowledge the wake byte.
/// It is woken by a `poll()` on a pipe, so in practice this takes well under a millisecond.
const Duration _readerStopTimeout = Duration(seconds: 5);

/// How long [PosixPtySession.dispose] waits for the SIGKILLed child to be reaped, so [exitCode]
/// has completed by the time dispose returns.
const Duration _childReapTimeout = Duration(seconds: 5);

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

/// `struct pollfd` -- `{int fd; short events; short revents;}` on both Linux and Darwin.
final class _PollFd extends Struct {
  @Int32()
  external int fd;
  @Int16()
  external int events;
  @Int16()
  external int revents;
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
typedef _PipeNative = Int32 Function(Pointer<Int32>);
typedef _PipeDart = int Function(Pointer<Int32>);
// fcntl is variadic; on Darwin/arm64 variadic arguments travel on the stack, so the third
// argument must be declared as VarArgs rather than as a plain fixed parameter.
typedef _FcntlNative = Int32 Function(Int32, Int32, VarArgs<(Int32,)>);
typedef _FcntlDart = int Function(int, int, int);
// nfds_t is `unsigned long` on Linux and `unsigned int` on Darwin.
typedef _PollLinuxNative = Int32 Function(Pointer<_PollFd>, UnsignedLong, Int32);
typedef _PollDarwinNative = Int32 Function(Pointer<_PollFd>, UnsignedInt, Int32);
typedef _PollDart = int Function(Pointer<_PollFd>, int, int);
typedef _KillNative = Int32 Function(Int32, Int32);
typedef _KillDart = int Function(int, int);

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
    pipe = lib.lookupFunction<_PipeNative, _PipeDart>('pipe');
    fcntl = lib.lookupFunction<_FcntlNative, _FcntlDart>('fcntl');
    poll = Platform.isMacOS
        ? lib.lookupFunction<_PollDarwinNative, _PollDart>('poll')
        : lib.lookupFunction<_PollLinuxNative, _PollDart>('poll');
    kill = lib.lookupFunction<_KillNative, _KillDart>('kill');
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
  late final _PipeDart pipe;
  late final _FcntlDart fcntl;
  late final _PollDart poll;
  late final _KillDart kill;

  /// Marks [fd] close-on-exec. Without it the fd is inherited by every process this daemon
  /// starts afterwards -- the Perl helper and the shell it becomes, other sessions' shells, every
  /// `git` -- so a disposed session's pty would stay referenced by unrelated processes.
  bool setCloexec(int fd) => fcntl(fd, _fSetfd, _fdCloexec) == 0;
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

/// Sent by the reader isolate once it will never touch its fds again, after its loop. As a
/// backstop, `Isolate.spawn`'s `onExit` also delivers `null` once the isolate itself has gone.
const String _readerStoppedMessage = 'reader-stopped';

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
  final Completer<void> _readerStopped = Completer<void>();

  @override
  Stream<Uint8List> get output => _outputController.stream;
  @override
  Future<int> get exitCode => _exitCodeCompleter.future;

  int _masterFd = -1;
  int _wakeReadFd = -1;
  int _wakeWriteFd = -1;
  Process? _process;
  bool _disposed = false;
  bool _exited = false;
  ReceivePort? _receivePort;

  @override
  bool get isChildAlive => _process != null && !_exited;

  /// The eventual shell's OS pid -- correct because the Perl helper `exec`s in place rather than
  /// forking again, so the pid `Process.start` reports never changes.
  @override
  int get pid => _process?.pid ?? 0;

  @override
  Future<void> start() async {
    if (_disposed) throw PtyException('session already disposed');
    final libc = _Libc.instance;
    final masterFd = libc.posixOpenpt(_oRdwr);
    if (masterFd < 0) {
      throw PtyException('posix_openpt failed');
    }
    _masterFd = masterFd;
    if (!libc.setCloexec(masterFd)) {
      _closeFds();
      throw PtyException('fcntl(FD_CLOEXEC) on the pty master failed');
    }
    if (libc.grantpt(masterFd) != 0) {
      _closeFds();
      throw PtyException('grantpt failed');
    }
    if (libc.unlockpt(masterFd) != 0) {
      _closeFds();
      throw PtyException('unlockpt failed');
    }
    final slavePath = _ptsName(masterFd);
    if (slavePath == null) {
      _closeFds();
      throw PtyException('ptsname_r failed');
    }
    if (!_openWakePipe()) {
      _closeFds();
      throw PtyException('pipe() for the reader wake-up failed');
    }

    try {
      _process = await Process.start(
        'perl',
        ['-e', _perlHelperScript, slavePath, '$_tiocsctty', shellExecutable],
        workingDirectory: workingDirectory,
      );
    } catch (error) {
      _closeFds();
      throw PtyException('failed to spawn pty helper process (is perl on PATH?): $error');
    }
    unawaited(_process!.exitCode.then((code) {
      _exited = true;
      if (!_exitCodeCompleter.isCompleted) _exitCodeCompleter.complete(code);
    }));
    // These pipes exist (Process.start always creates them) but go unused: the Perl helper
    // redirects its own fds 0/1/2 onto the pty slave before the real shell ever writes anything,
    // so nothing arrives on them. Real output is read from [_masterFd] below instead.
    unawaited(_process!.stdout.drain<void>());
    unawaited(_process!.stderr.drain<void>());

    try {
      await _startReaderIsolate();
    } catch (error) {
      _killChild();
      _closeFds();
      throw PtyException('failed to start the pty reader isolate: $error');
    }
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

  bool _openWakePipe() {
    final libc = _Libc.instance;
    final fds = calloc<Int32>(2);
    try {
      if (libc.pipe(fds) != 0) return false;
      _wakeReadFd = fds[0];
      _wakeWriteFd = fds[1];
    } finally {
      calloc.free(fds);
    }
    return libc.setCloexec(_wakeReadFd) && libc.setCloexec(_wakeWriteFd);
  }

  Future<void> _startReaderIsolate() async {
    final port = ReceivePort();
    _receivePort = port;
    port.listen((message) {
      if (message is Uint8List) {
        if (!_outputController.isClosed) _outputController.add(message);
      } else if (message == _readerStoppedMessage || message == null) {
        _onReaderStopped();
      }
    });
    await Isolate.spawn(
      _readLoop,
      [_masterFd, _wakeReadFd, port.sendPort],
      onExit: port.sendPort, // delivers `null` once the isolate is gone
      debugName: 'PosixPtySession._readLoop($sessionId)',
    );
  }

  /// The reader has left its loop for good -- either the master read end broke (EIO/EOF once
  /// the slave side is gone, the normal way it ends when the child exits) or [dispose] woke it.
  /// Only now is it safe to close the fds it was polling.
  void _onReaderStopped() {
    if (_readerStopped.isCompleted) return;
    _readerStopped.complete();
    _receivePort?.close();
    _closeFds();
    if (!_outputController.isClosed) unawaited(_outputController.close());
  }

  void _closeFds() {
    final libc = _Libc.instance;
    for (final fd in [_masterFd, _wakeReadFd, _wakeWriteFd]) {
      if (fd >= 0) libc.close(fd);
    }
    _masterFd = -1;
    _wakeReadFd = -1;
    _wakeWriteFd = -1;
  }

  static void _readLoop(List<Object> args) {
    final fd = args[0] as int;
    final wakeFd = args[1] as int;
    final sendPort = args[2] as SendPort;
    final libc = _Libc.instance;
    const bufferSize = 4096;
    final buffer = calloc<Uint8>(bufferSize);
    final fds = calloc<_PollFd>(2);
    try {
      var consecutiveFailures = 0;
      while (true) {
        fds[0]
          ..fd = fd
          ..events = _pollIn
          ..revents = 0;
        fds[1]
          ..fd = wakeFd
          ..events = _pollIn
          ..revents = 0;
        final rc = libc.poll(fds, 2, -1);
        if (rc < 0) {
          // EINTR is the only expected failure (both fds stay open until this loop reports it
          // has stopped); the bound keeps any other persistent error from spinning forever.
          if (++consecutiveFailures > 100) break;
          continue;
        }
        consecutiveFailures = 0;
        // Any event on the wake pipe -- the byte dispose() writes, or its write end closing --
        // means stop now.
        if (fds[1].revents != 0) break;
        final events = fds[0].revents;
        if (events & _pollNval != 0) break;
        if (events & (_pollIn | _pollHup | _pollErr) == 0) continue;
        // Readable, or hung up with possibly some output still buffered: read drains what is
        // left, then returns -1/EIO (or 0) once the slave side is gone for good.
        final n = libc.read(fd, buffer, bufferSize);
        if (n <= 0) break;
        sendPort.send(Uint8List.fromList(buffer.asTypedList(n)));
      }
    } finally {
      calloc.free(fds);
      calloc.free(buffer);
      sendPort.send(_readerStoppedMessage);
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

  /// SIGKILLs the child's whole session as well as the child itself. The Perl helper's `setsid()`
  /// makes the shell's pid its process-group id, so `kill(-pid)` also reaches whatever the shell
  /// was running in its own group; before that `setsid()` has happened there is no such group
  /// and the call is a harmless ESRCH (it can never name this daemon's own group).
  void _killChild() {
    final process = _process;
    if (process == null || _exited) return;
    _Libc.instance.kill(-process.pid, _sigkill);
    process.kill(ProcessSignal.sigkill);
  }

  @override
  Future<void> dispose() async {
    if (_disposed) return;
    _disposed = true;
    _killChild();

    if (_receivePort != null && !_readerStopped.isCompleted) {
      // Wake the reader out of poll(); it reports back, and only then are the fds closed.
      if (_wakeWriteFd >= 0) {
        final byte = calloc<Uint8>(1);
        try {
          _Libc.instance.write(_wakeWriteFd, byte, 1);
        } finally {
          calloc.free(byte);
        }
      }
      await _readerStopped.future.timeout(_readerStopTimeout, onTimeout: () {});
    }
    if (_readerStopped.isCompleted || _receivePort == null) {
      _closeFds();
    }
    // Otherwise the reader never acknowledged the wake byte: its fds are deliberately left open
    // (a leak of three fds) rather than closed under a thread that may still be polling them,
    // where a reused fd number could hand it some unrelated file.
    _receivePort?.close();
    if (!_outputController.isClosed) await _outputController.close();

    final process = _process;
    if (process != null) {
      await process.exitCode.timeout(_childReapTimeout, onTimeout: () => -1);
    }
  }
}
