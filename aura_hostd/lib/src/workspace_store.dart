/// Persists granted workspace roots to a local JSON file so a restart of the daemon does not
/// silently forget what the user has already granted (and, just as important, does not silently
/// re-grant something removed).
library;

import 'dart:convert';
import 'dart:io';

import 'containment.dart';

class WorkspaceStore {
  WorkspaceStore(this._file);

  factory WorkspaceStore.forConfigDir(Directory configDir) =>
      WorkspaceStore(File('${configDir.path}/workspaces.json'));

  final File _file;

  WorkspaceRoots load() {
    if (!_file.existsSync()) {
      return WorkspaceRoots(const []);
    }
    final raw = _file.readAsStringSync();
    if (raw.trim().isEmpty) {
      return WorkspaceRoots(const []);
    }
    final decoded = jsonDecode(raw);
    if (decoded is! List) {
      return WorkspaceRoots(const []);
    }
    final roots = decoded
        .whereType<Map<String, dynamic>>()
        .map(WorkspaceRoot.fromJson)
        .toList(growable: false);
    return WorkspaceRoots(roots);
  }

  /// A view that re-reads this store on every lookup (G-05, SEC-08) -- what the agent-facing
  /// `TaskHandler` and `IsolatedTaskRegistry` hold, so a grant, revoke or trust change saved by the
  /// control server or the CLI applies to the connected agent's very next call. A store file that
  /// cannot be parsed fails that call rather than falling back to anything.
  WorkspaceRoots live() => WorkspaceRoots.live(() => load().all);

  void save(WorkspaceRoots roots) {
    _file.parent.createSync(recursive: true);
    final json = jsonEncode(roots.all.map((r) => r.toJson()).toList());
    _file.writeAsStringSync(json);
  }
}
