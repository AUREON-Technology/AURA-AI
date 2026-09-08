/// Daemon configuration: backend URL, auth token, and YOLO consent flag.
///
/// Pairing (ADR-011): `aura_hostd auth pair <code>` exchanges a one-time code from the
/// desktop app for a session JWT + device-scoped token via
/// `POST /api/v3/auth/desktop/pairing/exchange`. `auth set-token` remains for operators who
/// already hold a bearer token.
library;

import 'dart:convert';
import 'dart:io';

class DaemonConfig {
  DaemonConfig({
    required this.backendUrl,
    required this.authToken,
    this.deviceToken,
    this.yolo = false,
  });

  final String backendUrl;
  final String? authToken;
  final String? deviceToken;
  final bool yolo;

  /// The `wss://.../api/v3/host/agent` URL derived from [backendUrl], which is stored as the
  /// plain `https://host[:port]` base the same way every other AURA client config does.
  String get hostAgentWsUrl {
    final uri = Uri.parse(backendUrl);
    final scheme = uri.scheme == 'https' ? 'wss' : 'ws';
    return uri.replace(scheme: scheme, path: '/api/v3/host/agent').toString();
  }

  DaemonConfig copyWith({
    String? backendUrl,
    String? authToken,
    String? deviceToken,
    bool? yolo,
  }) =>
      DaemonConfig(
        backendUrl: backendUrl ?? this.backendUrl,
        authToken: authToken ?? this.authToken,
        deviceToken: deviceToken ?? this.deviceToken,
        yolo: yolo ?? this.yolo,
      );

  Map<String, Object?> toJson() => {
        'backend_url': backendUrl,
        'auth_token': authToken,
        'device_token': deviceToken,
        'yolo': yolo,
      };

  static DaemonConfig fromJson(Map<String, dynamic> json) => DaemonConfig(
        backendUrl: json['backend_url'] as String? ?? 'https://localhost:8000',
        authToken: json['auth_token'] as String?,
        deviceToken: json['device_token'] as String?,
        yolo: json['yolo'] as bool? ?? false,
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
