/// Daemon configuration: backend URL and the bearer token used to authenticate the WebSocket
/// connection.
///
/// `authToken` is populated one of two ways: `aura_hostd auth pair` (`pairing_client.dart`,
/// ADR-011's one-time-code exchange -- the app shows a code, this daemon trades it for a
/// device-scoped token) or `aura_hostd auth set-token <token>` for supplying one out-of-band
/// (a token minted through some other admin/dev path). Either way, everything downstream of
/// `authToken` -- the WebSocket connection in `rpc_client.dart` -- is unaffected by which one was
/// used.
library;

import 'dart:convert';
import 'dart:io';

class DaemonConfig {
  DaemonConfig({required this.backendUrl, required this.authToken});

  final String backendUrl;
  final String? authToken;

  /// The `wss://.../api/v3/host/agent` URL derived from [backendUrl], which is stored as the
  /// plain `https://host[:port]` base the same way every other AURA client config does.
  String get hostAgentWsUrl {
    final uri = Uri.parse(backendUrl);
    final scheme = uri.scheme == 'https' ? 'wss' : 'ws';
    return uri.replace(scheme: scheme, path: '/api/v3/host/agent').toString();
  }

  Map<String, Object?> toJson() => {'backend_url': backendUrl, 'auth_token': authToken};

  static DaemonConfig fromJson(Map<String, dynamic> json) => DaemonConfig(
        backendUrl: json['backend_url'] as String? ?? 'https://localhost:8000',
        authToken: json['auth_token'] as String?,
      );
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
    _file.writeAsStringSync(jsonEncode(config.toJson()));
  }
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
