/// Daemon configuration: backend URL and the bearer token used to authenticate the WebSocket
/// connection.
///
/// `authToken` is populated one of two ways: `aura_hostd auth pair` (`pairing_client.dart`,
/// ADR-011's one-time-code exchange -- the app shows a code, this daemon trades it for a
/// device-scoped token) or `aura_hostd auth set-token <token>` for supplying one out-of-band
/// (a token minted through some other admin/dev path). Either way, everything downstream of
/// `authToken` -- the WebSocket connection in `rpc_client.dart` -- is unaffected by which one was
/// used.
///
/// `remoteControl` (`remote_control` in the file, WP8 / D-10b) says whether the Engineer may drive
/// this computer from the phone or the web app. It is off unless the file says `true`, and it is
/// changed only through the local control server's `POST /remote_control` -- never by anything the
/// backend sends: the opt-in is enforced on this machine (`task_handler.dart`).
library;

import 'dart:convert';
import 'dart:io';

class DaemonConfig {
  DaemonConfig({required this.backendUrl, required this.authToken, this.remoteControl = false});

  final String backendUrl;
  final String? authToken;

  /// Whether a turn started on the phone or the web (`create_task` with `origin: "remote"`) may
  /// act on this computer at all. Off by default; see the library doc.
  final bool remoteControl;

  DaemonConfig copyWith({String? backendUrl, String? authToken, bool? remoteControl}) => DaemonConfig(
        backendUrl: backendUrl ?? this.backendUrl,
        authToken: authToken ?? this.authToken,
        remoteControl: remoteControl ?? this.remoteControl,
      );

  /// The `wss://.../api/v3/host/agent` URL derived from [backendUrl], which is stored as the
  /// plain `https://host[:port]` base the same way every other AURA client config does. Throws
  /// [InvalidBackendUrlError] for a URL [requireSecureBackendUrl] refuses (F53): the first frame
  /// on this socket is the device token.
  String get hostAgentWsUrl {
    final uri = Uri.parse(requireSecureBackendUrl(backendUrl));
    final secure = uri.scheme.toLowerCase() == 'https' || uri.scheme.toLowerCase() == 'wss';
    return uri.replace(scheme: secure ? 'wss' : 'ws', path: '/api/v3/host/agent').toString();
  }

  Map<String, Object?> toJson() =>
      {'backend_url': backendUrl, 'auth_token': authToken, 'remote_control': remoteControl};

  static DaemonConfig fromJson(Map<String, dynamic> json) => DaemonConfig(
        backendUrl: json['backend_url'] as String? ?? 'https://localhost:8000',
        authToken: json['auth_token'] as String?,
        // Only a JSON `true` turns it on: an older file without the key, or anything malformed,
        // leaves this computer refusing remote turns.
        remoteControl: json['remote_control'] == true,
      );
}

/// A backend URL this daemon will not talk to: see [requireSecureBackendUrl].
class InvalidBackendUrlError implements Exception {
  InvalidBackendUrlError(this.message);

  final String message;

  @override
  String toString() => message;
}

/// [raw], unchanged, if this daemon may send its credentials there; otherwise throws
/// [InvalidBackendUrlError] (F53).
///
/// Pairing receives the device token over this URL and every WebSocket connection sends it as
/// its first frame, so it may only travel encrypted: `https` (or `wss`) to any host. Plain `http`
/// (or `ws`) is accepted only for a loopback host -- `localhost`, `127.0.0.0/8` or `::1` -- where
/// the bytes never leave this machine (a backend under development). Every place a backend URL
/// enters -- `--backend-url`, the control server's `/pair/start`, the pairing client, and the
/// WebSocket connection itself -- calls this, so a URL in a hand-edited `config.json` is refused
/// at the point it would be used.
String requireSecureBackendUrl(String raw) {
  final uri = Uri.tryParse(raw.trim());
  final scheme = uri?.scheme.toLowerCase() ?? '';
  final host = uri?.host ?? '';
  if (uri != null && host.isNotEmpty) {
    if (scheme == 'https' || scheme == 'wss') return raw;
    if ((scheme == 'http' || scheme == 'ws') && _isLoopbackHost(host)) return raw;
  }
  throw InvalidBackendUrlError(
    'backend URL "$raw" refused: use https:// or wss:// (plain http:// or ws:// is allowed only '
    'for localhost, 127.0.0.1 or ::1) -- the device token is sent over this connection',
  );
}

bool _isLoopbackHost(String host) {
  if (host.toLowerCase() == 'localhost') return true;
  final address = InternetAddress.tryParse(host);
  return address != null && address.isLoopback;
}

class ConfigStore {
  ConfigStore(this._file);

  factory ConfigStore.forConfigDir(Directory configDir) =>
      ConfigStore(File('${configDir.path}/config.json'));

  final File _file;

  DaemonConfig load() {
    if (!_file.existsSync()) {
      return DaemonConfig(backendUrl: 'https://localhost:8000', authToken: null);
    }
    final raw = _file.readAsStringSync();
    if (raw.trim().isEmpty) {
      return DaemonConfig(backendUrl: 'https://localhost:8000', authToken: null);
    }
    final decoded = jsonDecode(raw) as Map<String, dynamic>;
    return DaemonConfig.fromJson(decoded);
  }

  void save(DaemonConfig config) {
    _file.parent.createSync(recursive: true);
    writePrivateFile(_file, jsonEncode(config.toJson()));
  }

  /// Saves a new backend URL and token (pairing, `auth set-token`) and keeps every other setting
  /// -- `remote_control` above all: re-pairing is not a decision about remote control, so it
  /// neither switches it on nor silently off. An unreadable file keeps nothing, which leaves
  /// remote control off.
  void saveCredentials({required String backendUrl, required String? authToken}) {
    DaemonConfig current;
    try {
      current = load();
    } on Object {
      current = DaemonConfig(backendUrl: backendUrl, authToken: authToken);
    }
    save(DaemonConfig(
      backendUrl: backendUrl,
      authToken: authToken,
      remoteControl: current.remoteControl,
    ));
  }

  /// Whether remote control is on, read from disk now -- `TaskHandler` calls this on every RPC of
  /// a remote task, so turning it off in the desktop app stops the very next call. A file that
  /// cannot be read or parsed counts as off: a choice this daemon cannot read must not leave the
  /// phone in control.
  bool remoteControlEnabled() {
    try {
      return load().remoteControl;
    } on Object {
      return false;
    }
  }
}

/// Writes [content] to [file] readable by the owner only. `config.json` holds the 30-day device
/// token and `control.json` the control server's bearer token, and the default umask would leave
/// both world-readable. The mode is set on the empty file *before* the secret is written, so there
/// is no window in which the secret sits in a readable file. Windows has no POSIX mode bits; the
/// per-user `%APPDATA%` ACL is what protects the file there.
void writePrivateFile(File file, String content) {
  if (!Platform.isWindows) {
    file.writeAsStringSync('');
    final result = Process.runSync('chmod', ['600', file.path]);
    if (result.exitCode != 0) {
      throw FileSystemException('could not restrict permissions: ${result.stderr}', file.path);
    }
  }
  file.writeAsStringSync(content);
}

/// The daemon's per-user config/state directory: `$XDG_CONFIG_HOME/aura_hostd` on Linux,
/// `%APPDATA%\aura_hostd` on Windows, `~/Library/Application Support/aura_hostd` on macOS --
/// falling back to `~/.aura_hostd` when none of those environment variables are set (e.g. a
/// minimal container).
Directory resolveConfigDir() {
  final env = Platform.environment;
  if (Platform.isWindows) {
    final appData = env['APPDATA'];
    if (appData != null && appData.isNotEmpty) {
      return Directory('$appData/aura_hostd');
    }
  } else if (Platform.isMacOS) {
    final home = env['HOME'];
    if (home != null && home.isNotEmpty) {
      return Directory('$home/Library/Application Support/aura_hostd');
    }
  } else {
    final xdg = env['XDG_CONFIG_HOME'];
    if (xdg != null && xdg.isNotEmpty) {
      return Directory('$xdg/aura_hostd');
    }
  }
  final home = env['HOME'] ?? env['USERPROFILE'];
  if (home != null && home.isNotEmpty) {
    return Directory('$home/.aura_hostd');
  }
  return Directory.systemTemp.createTempSync('aura_hostd_');
}
