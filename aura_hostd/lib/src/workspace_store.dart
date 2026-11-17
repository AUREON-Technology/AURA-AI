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

  WorkspaceRoots? _live;

  /// The one in-memory [WorkspaceRoots] the daemon's agent-facing side (`TaskHandler`,
  /// `IsolatedTaskRegistry`) resolves every RPC against (audit INT-002). It is created from disk
  /// on first use and then kept current in place: every [save] through this store (the control
  /// server's grant/revoke/trust/untrust) updates it immediately, and [refreshLive] re-reads the
  /// file so a change made by another process (the `aura_hostd revoke` CLI while `run` is up)
  /// is seen too. Handing out a fresh [load] snapshot instead -- what `run` used to do -- meant a
  /// root the user revoked in the desktop UI stayed writable by the agent until a restart.
  WorkspaceRoots get live => _live ??= load();

  /// Re-reads the file into [live], in place. `TaskHandler` calls this at the start of every RPC.
  /// A file that cannot be parsed throws rather than keeping the previous grants: a revocation
  /// the daemon cannot read must not leave the old access standing.
  void refreshLive() {
    final fresh = load();
    final live = _live;
    if (live == null) {
      _live = fresh;
    } else {
      live.replaceAll(fresh.all);
    }
  }

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

  void save(WorkspaceRoots roots) {
    _file.parent.createSync(recursive: true);
    final json = jsonEncode(roots.all.map((r) => r.toJson()).toList());
    _file.writeAsStringSync(json);
    final live = _live;
    if (live != null && !identical(live, roots)) live.replaceAll(roots.all);
  }
}
