/// Riverpod notifiers for Desktop IDE state.
///
/// Each domain has its own notifier to avoid a giant monolithic state.
library;

import 'dart:async';
import 'dart:convert';

import 'package:collection/collection.dart';
import 'package:crypto/crypto.dart' show sha256;
import 'package:drift/drift.dart';
import 'package:flutter/foundation.dart' show debugPrint, defaultTargetPlatform;
import 'package:flutter_riverpod/legacy.dart';
import 'package:path/path.dart' as p;
import 'package:xterm/xterm.dart' as xterm;
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/hostd/hostd_control_client.dart';
import 'package:aura_client/core/hostd/hostd_providers.dart' hide HostdRoot;
import 'package:aura_client/core/hostd/hostd_terminal_client.dart';
import 'package:aura_client/core/security/encryption_service.dart';
import 'package:aura_client/desktop/editor/editor_abstraction.dart';
import 'package:aura_client/desktop/shell/ide_platform_commands.dart';
import 'package:aura_client/desktop/state/desktop_application_state.dart';
import 'package:aura_client/desktop/state/git_notifier.dart';
import 'package:aura_client/desktop/events/ide_event_bus.dart';
import 'package:aura_client/desktop/events/ide_event.dart';
import 'package:aura_client/desktop/editor/editor_tab.dart';
import 'package:aura_client/desktop/workspace/workspace_file_service.dart';

/// Workspace state notifier.
class WorkspaceNotifier extends StateNotifier<WorkspaceState> {
  WorkspaceNotifier(this._db, this._eventBus) : super(const WorkspaceState()) {
    _loadWorkspaces();
  }

  final AppDatabase _db;
  final IdeEventBus _eventBus;

  // Note: this notifier does NOT subscribe to WorkspaceOpened on the event
  // bus — setActiveWorkspace() below is the only place that changes
  // activeWorkspaceId, and it publishes WorkspaceOpened itself. Reacting to
  // that same event here would call setActiveWorkspace() again, which
  // publishes again, forever.

  Future<void> _loadWorkspaces() async {
    final workspaces = await _db.select(_db.desktopWorkspaces).get();
    state = state.copyWith(workspaces: workspaces);
  }

  Future<void> createWorkspace({
    required String id,
    required String rootPath,
    required String displayName,
  }) async {
    await _db.into(_db.desktopWorkspaces).insert(
      DesktopWorkspacesCompanion.insert(
        id: id,
        rootPath: rootPath,
        displayName: displayName,
        lastOpenedAt: DateTime.now(),
      ),
    );
    await _loadWorkspaces();
    setActiveWorkspace(id);
  }

  Future<void> setActiveWorkspace(String workspaceId) async {
    await (_db.update(_db.desktopWorkspaces)..where((t) => t.id.equals(workspaceId))).write(
      DesktopWorkspacesCompanion(lastOpenedAt: Value(DateTime.now())),
    );
    state = state.copyWith(activeWorkspaceId: workspaceId);
    _eventBus.publish(WorkspaceOpened(workspaceId));
  }

  Future<void> removeWorkspace(String workspaceId) async {
    await (_db.delete(_db.desktopWorkspaces)..where((t) => t.id.equals(workspaceId))).go();
    await _loadWorkspaces();
    if (state.activeWorkspaceId == workspaceId) {
      state = state.copyWith(activeWorkspaceId: null);
    }
  }

  Future<void> updateHostdRoots(List<HostdRoot> roots) async {
    state = state.copyWith(hostdRoots: roots);
  }
}

/// Editor state notifier.
class EditorNotifier extends StateNotifier<EditorState> {
  EditorNotifier(
    this._db,
    this._eventBus, {
    this.backupDelay = const Duration(seconds: 1),
    this.rootPathFor,
  }) : super(const EditorState()) {
    _subscribeToEvents();
  }

  final AppDatabase _db;
  final IdeEventBus _eventBus;

  /// The root folder of a workspace, by id. Tab identity is the file's path *relative to that
  /// root*, so the same file opened from the Explorer (absolute), Quick Open or the Git panel
  /// (relative) is one tab, and a rename or delete event (absolute) can find the tabs it affects.
  /// `null` (tests that never open a folder) leaves paths as they were given.
  final String? Function(String workspaceId)? rootPathFor;

  /// Hot exit (M8.8): how long after the last edit a dirty buffer is backed up to
  /// [DesktopUnsavedBuffers]. At most this much typing is lost if the app is killed.
  final Duration backupDelay;

  final Map<String, Timer> _backupTimers = {};

  /// SHA-256 of each tab's file as it was on disk when loaded or last saved -- the base its
  /// unsaved edits were made against (see [restoredOverChangedFile]).
  final Map<String, String> _baseHashes = {};

  /// Tabs whose buffer came back from a backup at workspace open, mapped to the base hash the
  /// backup recorded (`null` when it recorded none).
  final Map<String, String?> _restored = {};

  /// Backup writes and deletes run one after another, so a write that started before a save
  /// can never land after the save's delete and resurrect an already-saved buffer as unsaved.
  Future<void> _backupQueue = Future<void>.value();

  StreamSubscription? _eventSub;

  /// Debounces [saveSession] writes triggered by cursor/selection movement,
  /// which fires on every caret move -- same reasoning as
  /// [LayoutNotifier]'s `_debouncedPersist` for drag-resize.
  Timer? _cursorSaveDebounce;

  /// In-memory buffer content per open tab, keyed by tab id. Not part of
  /// [EditorState] since it changes on every keystroke and would be far too
  /// expensive to run through Riverpod's equality/rebuild machinery.
  final Map<String, String> _tabContents = {};

  /// Returns the current in-memory buffer for [tabId], or `null` if nothing
  /// has been cached yet (e.g. the tab was just opened and the initial file
  /// read hasn't completed).
  String? contentFor(String tabId) => _tabContents[tabId];

  /// Updates the in-memory buffer for [tabId]. Called on every edit; does not
  /// touch [EditorState] or trigger a rebuild by itself.
  void setContent(String tabId, String content) {
    _tabContents[tabId] = content;
    _scheduleBackup(tabId);
  }

  // -- tab identity ---------------------------------------------------------------------------

  /// Windows paths are case-insensitive, so `Src/A.dart` and `src/a.dart` are the same file there.
  bool get _foldsCase => p.style == p.Style.windows;

  String? _rootFor(String? workspaceId) => workspaceId == null ? null : rootPathFor?.call(workspaceId);

  /// [path] as a workspace-relative path with forward slashes: what a tab stores and what
  /// `WorkspaceFileService` is given. An absolute path inside the workspace is made relative; one
  /// that is not inside it (or a workspace with no known root) is left absolute.
  String normalizeTabPath(String path, {String? workspaceId}) {
    var value = path;
    final root = _rootFor(workspaceId ?? state.workspaceId);
    if (root != null && p.isAbsolute(value) && p.isWithin(root, value)) {
      value = p.relative(value, from: root);
    }
    if (p.style == p.Style.windows) value = value.replaceAll('\\', '/');
    return value;
  }

  /// The comparison key of a normalized path: the path itself, lower-cased on Windows.
  String tabKeyFor(String normalizedPath) => _foldsCase ? normalizedPath.toLowerCase() : normalizedPath;

  /// Whether [a] and [b] name the same file once normalized.
  bool isSameTabPath(String a, String b) => tabKeyFor(normalizeTabPath(a)) == tabKeyFor(normalizeTabPath(b));

  bool _isSameOrUnder(String path, String ancestor) {
    final key = tabKeyFor(path);
    final ancestorKey = tabKeyFor(ancestor);
    return key == ancestorKey || key.startsWith('$ancestorKey/');
  }

  EditorTab? _tabForPath(String normalizedPath) {
    final key = tabKeyFor(normalizedPath);
    return state.tabs.where((t) => tabKeyFor(normalizeTabPath(t.path)) == key).firstOrNull;
  }

  EditorTab? _tab(String tabId) => state.tabs.where((t) => t.id == tabId).firstOrNull;

  static String _titleOf(String normalizedPath) => p.posix.basename(normalizedPath);

  // -- disk state -----------------------------------------------------------------------------

  /// Whether the file's content on disk is no longer the one [tabId]'s edits were made against.
  /// `false` when there is no recorded base to compare with.
  bool hasDiskChanged(String tabId, String diskContent) {
    final base = _baseHashes[tabId];
    return base != null && base != _hash(diskContent);
  }

  /// Records that [savedContent] was written to disk for [tabId]. The tab is marked clean only if
  /// the buffer still *is* that text: anything typed while the write was in flight stays unsaved
  /// (and backed up), instead of being declared saved along with it.
  Future<void> markSaved(String tabId, String savedContent) async {
    if (_tab(tabId) == null) return;
    _baseHashes[tabId] = _hash(savedContent);
    _restored.remove(tabId);
    if ((_tabContents[tabId] ?? savedContent) == savedContent) {
      await markDirty(tabId, false);
    } else {
      _scheduleBackup(tabId);
    }
  }

  /// Replaces [tabId]'s buffer with [diskContent] -- the file as it is on disk -- discarding any
  /// unsaved edits, and rebuilds the editor view around it.
  void reloadFromDisk(String tabId, String diskContent) {
    final tab = _tab(tabId);
    if (tab == null) return;
    _tabContents[tabId] = diskContent;
    _baseHashes[tabId] = _hash(diskContent);
    _dropBackup(tabId);
    state = state.copyWith(
      tabs: state.tabs
          .map((t) => t.id == tabId ? t.copyWith(isDirty: false, revision: t.revision + 1) : t)
          .toList(),
    );
    _eventBus.publish(TabDirtyChanged(tabId, false, state.workspaceId ?? ''));
  }

  /// Reloads a *clean* tab whose file changed on disk since it was read (the Engineer agent, a
  /// `git checkout`, a formatter), so the tab does not keep showing -- and later offer to save
  /// over -- text that is no longer the file. A dirty tab is left alone; the save-time check
  /// (see `saveActiveFile`) asks the user before it would overwrite the change.
  Future<void> refreshIfChangedOnDisk(String tabId) async {
    final workspaceId = state.workspaceId;
    final root = _rootFor(workspaceId);
    final tab = _tab(tabId);
    final base = _baseHashes[tabId];
    if (root == null || tab == null || tab.isDirty || base == null) return;
    final String onDisk;
    try {
      onDisk = await WorkspaceFileService(root).readFile(tab.path);
    } catch (_) {
      return; // Deleted or unreadable: the open buffer is still the best copy there is.
    }
    final current = _tab(tabId);
    if (current == null || current.isDirty || state.workspaceId != workspaceId) return;
    if (_baseHashes[tabId] != base || _hash(onDisk) == base) return;
    reloadFromDisk(tabId, onDisk);
  }

  /// Moves the in-memory buffer, base hash and backup of [from] to [to] (a renamed file).
  void _moveBuffer(String from, String to) {
    final content = _tabContents.remove(from);
    final base = _baseHashes.remove(from);
    final wasRestored = _restored.containsKey(from);
    final restoredBase = _restored[from];
    _dropBackup(from);
    if (content != null) _tabContents[to] = content;
    if (base != null) _baseHashes[to] = base;
    if (wasRestored) _restored[to] = restoredBase;
  }

  void _handleFileRenamed(FileRenamed event) {
    if (event.workspaceId != state.workspaceId) return;
    final from = normalizeTabPath(event.oldPath);
    final to = normalizeTabPath(event.newPath);
    var changed = false;
    var activeId = state.activeTabId;
    final tabs = <EditorTab>[];
    final moved = <String>[];
    for (final tab in state.tabs) {
      final path = normalizeTabPath(tab.path);
      if (!_isSameOrUnder(path, from)) {
        tabs.add(tab);
        continue;
      }
      final newPath = to + path.substring(from.length);
      // A tab whose id *is* its path follows the file; one with a custom id keeps it.
      final newId = tab.id == tabKeyFor(path) ? tabKeyFor(newPath) : tab.id;
      if (newId != tab.id) {
        _moveBuffer(tab.id, newId);
        if (activeId == tab.id) activeId = newId;
      }
      tabs.add(tab.copyWith(id: newId, path: newPath, title: _titleOf(newPath)));
      if (tab.isDirty) moved.add(newId);
      changed = true;
    }
    if (!changed) return;
    state = state.copyWith(tabs: tabs, activeTabId: activeId);
    for (final id in moved) {
      _scheduleBackup(id);
    }
    unawaited(saveSession());
  }

  void _handleFileDeleted(FileDeleted event) {
    if (event.workspaceId != state.workspaceId) return;
    final gone = normalizeTabPath(event.path);
    for (final tab in List<EditorTab>.of(state.tabs)) {
      // A clean tab of a deleted file has nothing left to show. A dirty one stays: its unsaved
      // text is the only copy, and saving it is the user's call.
      if (!tab.isDirty && _isSameOrUnder(normalizeTabPath(tab.path), gone)) {
        unawaited(closeTab(tab.id));
      }
    }
  }

  // -- hot exit (M8.8) ------------------------------------------------------------------------

  static String _hash(String content) => sha256.convert(utf8.encode(content)).toString();

  /// Records [diskContent] as the base [tabId]'s edits are made against: the file as read when
  /// the tab opened.
  void recordDiskBase(String tabId, String diskContent) {
    _baseHashes[tabId] = _hash(diskContent);
  }

  /// Whether [tabId]'s buffer was restored from a backup when its workspace opened.
  bool wasRestored(String tabId) => _restored.containsKey(tabId);

  /// For a restored tab: whether the file on disk is no longer the one the unsaved edits were
  /// made against, so saving would overwrite someone else's change. `false` when unknown.
  bool restoredOverChangedFile(String tabId, String diskContent) {
    if (!_restored.containsKey(tabId)) return false;
    final base = _restored[tabId];
    return base != null && base != _hash(diskContent);
  }

  void _scheduleBackup(String tabId) {
    _backupTimers.remove(tabId)?.cancel();
    _backupTimers[tabId] = Timer(backupDelay, () {
      _backupTimers.remove(tabId);
      _enqueue(() async {
        final snapshot = _snapshot(tabId);
        if (snapshot != null) await _persist(snapshot);
      });
    });
  }

  void _enqueue(Future<void> Function() operation) {
    _backupQueue = _backupQueue.then((_) => operation()).catchError((Object error) {
      // A failed backup must never break editing; the next edit tries again.
      debugPrint('[EditorNotifier] unsaved-buffer backup failed: $error');
    });
  }

  /// What a backup of [tabId] would write, taken now; `null` when there is nothing to back up
  /// (the tab is clean, gone, or has no workspace). Taken synchronously so [dispose] can capture
  /// pending backups while [state] is still readable.
  DesktopUnsavedBuffersCompanion? _snapshot(String tabId) {
    final workspaceId = state.workspaceId;
    final tab = state.tabs.where((t) => t.id == tabId).firstOrNull;
    final content = _tabContents[tabId];
    if (workspaceId == null || tab == null || !tab.isDirty || content == null) return null;
    final String sealed;
    try {
      sealed = EncryptionService.encryptText(content);
    } on EncryptionException catch (error) {
      // Fail closed: no key means no backup, never a plaintext copy of the user's file.
      debugPrint('[EditorNotifier] not backing up ${tab.path}: $error');
      return null;
    }
    return DesktopUnsavedBuffersCompanion.insert(
      workspaceId: workspaceId,
      tabId: tabId,
      path: tab.path,
      contentEnc: sealed,
      baseHash: Value(_baseHashes[tabId]),
      updatedAt: DateTime.now(),
    );
  }

  Future<void> _persist(DesktopUnsavedBuffersCompanion row) async {
    await _db.into(_db.desktopUnsavedBuffers).insertOnConflictUpdate(row);
  }

  void _dropBackup(String tabId) {
    _backupTimers.remove(tabId)?.cancel();
    _restored.remove(tabId);
    final workspaceId = state.workspaceId;
    if (workspaceId == null) return;
    _enqueue(() async {
      await (_db.delete(_db.desktopUnsavedBuffers)
            ..where((t) => t.workspaceId.equals(workspaceId) & t.tabId.equals(tabId)))
          .go();
    });
  }

  /// Writes every pending backup now and waits for all backup work to finish. Called before a
  /// workspace switch, and by tests in place of waiting out [backupDelay].
  Future<void> flushBackups() async {
    for (final snapshot in _takePendingSnapshots()) {
      _enqueue(() => _persist(snapshot));
    }
    await _backupQueue;
  }

  List<DesktopUnsavedBuffersCompanion> _takePendingSnapshots() {
    final snapshots = <DesktopUnsavedBuffersCompanion>[];
    for (final tabId in _backupTimers.keys.toList()) {
      _backupTimers.remove(tabId)?.cancel();
      final snapshot = _snapshot(tabId);
      if (snapshot != null) snapshots.add(snapshot);
    }
    return snapshots;
  }

  /// Puts back the unsaved buffers backed up for [workspaceId] into [tabs], marking each tab
  /// dirty. A backup that can no longer be decrypted (the key is gone) is dropped rather than
  /// shown as ciphertext.
  ///
  /// [idRemap] maps the ids sessions saved before tab ids were canonical (an absolute or
  /// backslashed path) to the ids those tabs have now, so their backups still find their tab.
  Future<List<EditorTab>> _restoreBackups(
    String workspaceId,
    List<EditorTab> tabs, {
    Map<String, String> idRemap = const {},
  }) async {
    final rows = await (_db.select(_db.desktopUnsavedBuffers)
          ..where((t) => t.workspaceId.equals(workspaceId)))
        .get();
    if (rows.isEmpty) return tabs;
    final restored = [...tabs];
    for (final row in rows) {
      final content = EncryptionService.decryptText(row.contentEnc);
      if (content == row.contentEnc && EncryptionService.isEnvelope(row.contentEnc)) {
        await (_db.delete(_db.desktopUnsavedBuffers)
              ..where((t) => t.workspaceId.equals(workspaceId) & t.tabId.equals(row.tabId)))
            .go();
        continue;
      }
      final path = normalizeTabPath(row.path, workspaceId: workspaceId);
      // The same file may already be open under another id (the same path spelled differently).
      final byPath = restored.where((t) => tabKeyFor(normalizeTabPath(t.path, workspaceId: workspaceId)) == tabKeyFor(path));
      final id = idRemap[row.tabId] ?? byPath.firstOrNull?.id ?? row.tabId;
      if (id != row.tabId) {
        // The row is filed under the old id; refile it under the new one (the next backup of
        // this tab writes it) so it is not restored a second time on the next launch.
        await (_db.delete(_db.desktopUnsavedBuffers)
              ..where((t) => t.workspaceId.equals(workspaceId) & t.tabId.equals(row.tabId)))
            .go();
        _rekeyedBackups.add(id);
      }
      _tabContents[id] = content;
      _restored[id] = row.baseHash;
      if (row.baseHash != null) _baseHashes[id] = row.baseHash!;
      final index = restored.indexWhere((t) => t.id == id);
      if (index >= 0) {
        restored[index] = restored[index].copyWith(isDirty: true);
      } else {
        // The tab list was not saved with this tab in it; reopen it so the edits are not lost.
        restored.add(EditorTab(
          id: id,
          path: path,
          title: _titleOf(path),
          lastAccessed: DateTime.now(),
          isDirty: true,
        ));
      }
    }
    return restored;
  }

  /// Tabs whose backup row was refiled under a new id while restoring; re-backed up once the
  /// restored state is in place.
  final Set<String> _rekeyedBackups = {};

  void _subscribeToEvents() {
    _eventSub = _eventBus.stream.listen((event) {
      if (event is TabOpened) _handleTabOpened(event);
      if (event is WorkspaceOpened) _loadSession(event.workspaceId);
      if (event is FileRenamed) _handleFileRenamed(event);
      if (event is FileDeleted) _handleFileDeleted(event);
    });
    _eventSub?.onDone(() => _eventSub = null);
  }

  /// Restores the tab list persisted for [workspaceId] (see [saveSession]),
  /// or clears the editor if this workspace has never had a session saved.
  Future<void> _loadSession(String workspaceId) async {
    // Pending backups belong to the workspace being left; write them under its id first.
    await flushBackups();
    // The buffers belong to the workspace being left. Kept, they would be served to a tab of the
    // same relative path in the next workspace (a README.md, a pubspec.yaml) -- showing, and on
    // save writing, the previous workspace's text. Anything unsaved was just backed up above and
    // is restored from there if this workspace is opened again.
    _restored.clear();
    _tabContents.clear();
    _baseHashes.clear();
    _rekeyedBackups.clear();
    final row = await (_db.select(_db.desktopEditorState)
          ..where((t) => t.workspaceId.equals(workspaceId)))
        .getSingleOrNull();
    if (row == null) {
      final tabs = await _restoreBackups(workspaceId, const []);
      state = EditorState(tabs: tabs, activeTabId: tabs.firstOrNull?.id, workspaceId: workspaceId);
      _rebackupRekeyed();
      return;
    }
    try {
      final decoded = jsonDecode(row.openTabsJson) as List<dynamic>;
      final tabs = <EditorTab>[];
      final idRemap = <String, String>{};
      final idByKey = <String, String>{};
      for (final json in decoded.cast<Map<String, dynamic>>()) {
        final rawId = json['id'] as String;
        final rawPath = json['path'] as String;
        final path = normalizeTabPath(rawPath, workspaceId: workspaceId);
        final key = tabKeyFor(path);
        final duplicateOf = idByKey[key];
        if (duplicateOf != null) {
          // Two tabs of one file (saved by a version that let that happen): keep the first.
          idRemap[rawId] = duplicateOf;
          continue;
        }
        // A tab whose id was its path (every tab opened from the UI) gets the canonical id.
        final id = rawId == rawPath ? key : rawId;
        idByKey[key] = id;
        if (id != rawId) idRemap[rawId] = id;
        tabs.add(EditorTab(
          id: id,
          path: path,
          title: _titleOf(path),
          isPinned: json['isPinned'] as bool? ?? false,
          lastAccessed: DateTime.now(),
          cursorPosition: (json['cursorOffset'] as num?) != null
              ? EditorCursor(offset: (json['cursorOffset'] as num).toInt())
              : null,
          selection: (json['selectionStart'] as num?) != null && (json['selectionEnd'] as num?) != null
              ? EditorSelection(
                  start: (json['selectionStart'] as num).toInt(),
                  end: (json['selectionEnd'] as num).toInt(),
                )
              : null,
        ));
      }
      final withBackups = await _restoreBackups(workspaceId, tabs, idRemap: idRemap);
      final activePath = row.activeTabPath;
      final activeKey = activePath == null ? null : tabKeyFor(normalizeTabPath(activePath, workspaceId: workspaceId));
      final activeTab = activeKey == null
          ? null
          : withBackups.where((t) => tabKeyFor(normalizeTabPath(t.path, workspaceId: workspaceId)) == activeKey).firstOrNull;
      state = EditorState(
        tabs: withBackups,
        activeTabId: activeTab?.id,
        workspaceId: workspaceId,
      );
      _rebackupRekeyed();
    } catch (_) {
      state = EditorState(workspaceId: workspaceId);
    }
  }

  void _rebackupRekeyed() {
    for (final id in _rekeyedBackups) {
      _scheduleBackup(id);
    }
    _rekeyedBackups.clear();
  }

  /// The tab for [path], created if it is not open yet. A file that is already open -- however
  /// its path is spelled -- is that tab, never a second one with its own buffer.
  EditorTab _resolveTab({required String tabId, required String path, String? workspaceId}) {
    final normalized = normalizeTabPath(path, workspaceId: workspaceId);
    final existing = _tabForPath(normalized) ?? _tab(tabId);
    if (existing != null) return existing;
    // Callers pass the path as the id; that becomes the canonical key. A caller that names a
    // distinct id keeps it.
    final id = tabId == path ? tabKeyFor(normalized) : tabId;
    return EditorTab(id: id, path: normalized, title: _titleOf(normalized), lastAccessed: DateTime.now());
  }

  void _handleTabOpened(TabOpened event) {
    final tab = _resolveTab(tabId: event.tabId, path: event.path, workspaceId: event.workspaceId);
    final exists = state.tabs.any((t) => t.id == tab.id);
    state = exists
        ? state.copyWith(activeTabId: tab.id)
        : state.copyWith(tabs: [...state.tabs, tab], activeTabId: tab.id);
  }

  Future<void> openTab({
    required String tabId,
    required String path,
    required String workspaceId,
  }) async {
    final tab = _resolveTab(tabId: tabId, path: path, workspaceId: workspaceId);
    final exists = state.tabs.any((t) => t.id == tab.id);
    if (!exists) {
      state = state.copyWith(
        tabs: [...state.tabs, tab],
        activeTabId: tab.id,
        workspaceId: workspaceId,
      );
    } else {
      state = state.copyWith(activeTabId: tab.id);
    }
    _eventBus.publish(TabOpened(tab.id, tab.path, workspaceId));
    if (exists && !tab.isDirty) unawaited(refreshIfChangedOnDisk(tab.id));
    await saveSession();
  }

  /// Closes [tabId]. If the tab is dirty, does nothing unless [force] is
  /// true — the caller (UI) is responsible for confirming save/discard with
  /// the user first, then retrying with `force: true`.
  Future<void> closeTab(String tabId, {bool force = false}) async {
    final tab = state.tabs.where((t) => t.id == tabId).firstOrNull;
    if (tab != null && tab.isDirty && !force) {
      // Caller must confirm save/discard first (see closeTabRequiresConfirmation)
      // and retry with force: true.
      return;
    }
    final newTabs = state.tabs.where((t) => t.id != tabId).toList();
    String? newActiveId = state.activeTabId;
    if (state.activeTabId == tabId) {
      newActiveId = newTabs.isNotEmpty ? newTabs.last.id : null;
    }
    state = state.copyWith(tabs: newTabs, activeTabId: newActiveId);
    _dropBackup(tabId);
    _tabContents.remove(tabId);
    _baseHashes.remove(tabId);
    _eventBus.publish(TabClosed(tabId, state.workspaceId ?? ''));
    await saveSession();
  }

  Future<void> setActiveTab(String tabId) async {
    state = state.copyWith(activeTabId: tabId);
    final tab = state.tabs.where((t) => t.id == tabId).firstOrNull;
    if (tab != null) {
      _eventBus.publish(TabActivated(tabId, state.workspaceId ?? ''));
      if (!tab.isDirty) unawaited(refreshIfChangedOnDisk(tabId));
    }
    await saveSession();
  }

  Future<void> markDirty(String tabId, bool isDirty) async {
    state = state.copyWith(
      tabs: state.tabs.map((t) => t.id == tabId ? t.copyWith(isDirty: isDirty) : t).toList(),
    );
    if (!isDirty) {
      // Saved (the only caller that clears dirty): what is on disk is now this buffer.
      final content = _tabContents[tabId];
      if (content != null) _baseHashes[tabId] = _hash(content);
      _dropBackup(tabId);
    }
    _eventBus.publish(TabDirtyChanged(tabId, isDirty, state.workspaceId ?? ''));
  }

  Future<void> updateTabViewState(String tabId, Map<String, dynamic> viewState) async {
    state = state.copyWith(
      tabs: state.tabs.map((t) => t.id == tabId ? t.copyWith(viewState: viewState) : t).toList(),
    );
  }

  Future<void> updateCursor(String tabId, EditorCursor cursor) async {
    state = state.copyWith(
      tabs: state.tabs.map((t) => t.id == tabId ? t.copyWith(cursorPosition: cursor) : t).toList(),
    );
    _debouncedSaveSession();
  }

  Future<void> updateSelection(String tabId, EditorSelection selection) async {
    state = state.copyWith(
      tabs: state.tabs.map((t) => t.id == tabId ? t.copyWith(selection: selection) : t).toList(),
    );
    _debouncedSaveSession();
  }

  void _debouncedSaveSession() {
    _cursorSaveDebounce?.cancel();
    _cursorSaveDebounce = Timer(const Duration(milliseconds: 400), () => unawaited(saveSession()));
  }

  Future<void> updateTab(String tabId, EditorTab Function(EditorTab) update) async {
    state = state.copyWith(
      tabs: state.tabs.map((t) => t.id == tabId ? update(t) : t).toList(),
    );
  }

  /// Persists the open tab list and active tab for the current workspace, so
  /// [_loadSession] can restore it after an app restart. Tab identity
  /// (id/path/title/pinned) and the last-known cursor/selection are saved,
  /// not buffer content — reopening a tab re-reads the file from disk, then
  /// the restored cursor/selection re-applies against that fresh content.
  Future<void> saveSession() async {
    final workspaceId = state.workspaceId;
    if (workspaceId == null) return;
    final tabsJson = jsonEncode(state.tabs
        .map((t) => {
              'id': t.id,
              'path': t.path,
              'title': t.title,
              'isPinned': t.isPinned,
              if (t.cursorPosition != null) 'cursorOffset': t.cursorPosition!.offset,
              if (t.selection != null) 'selectionStart': t.selection!.start,
              if (t.selection != null) 'selectionEnd': t.selection!.end,
            })
        .toList());
    await _db.into(_db.desktopEditorState).insertOnConflictUpdate(
          DesktopEditorStateCompanion.insert(
            workspaceId: workspaceId,
            openTabsJson: tabsJson,
            activeTabPath: Value(state.activeTab?.path),
          ),
        );
  }

  @override
  void dispose() {
    _eventSub?.cancel();
    _cursorSaveDebounce?.cancel();
    // A graceful close: capture what is pending while state is still readable, then write it
    // (best effort -- the database may close first).
    for (final snapshot in _takePendingSnapshots()) {
      _enqueue(() => _persist(snapshot));
    }
    super.dispose();
  }
}

/// Terminal state notifier (D4). Owns one [HostdTerminalClient] per live
/// session, keyed by session id, and mirrors their output/exit into
/// [TerminalState] for the UI to render.
class TerminalNotifier extends StateNotifier<TerminalState> {
  TerminalNotifier(this._db, this._eventBus) : super(const TerminalState()) {
    _subscribeToEvents();
  }

  final AppDatabase _db;
  final IdeEventBus _eventBus;
  final Map<String, HostdTerminalClient> _clients = {};
  StreamSubscription? _eventSub;
  Timer? _saveDebounce;

  /// One real VT100 emulator per live/restored session, keyed by session id --
  /// not part of [TerminalState] since it's a mutable, listenable buffer object
  /// (not an immutable value the Riverpod equality check should compare), the
  /// same reasoning [EditorNotifier]'s in-memory tab buffer already documents.
  /// `terminal_view.dart` reads this directly via [terminalFor] rather than
  /// through `state`.
  final Map<String, xterm.Terminal> _terminals = {};

  /// Mirrors `task_handler.dart`'s `_maxOutputCapChars` discipline on the
  /// daemon side -- a long-lived session must not grow this process's
  /// memory unboundedly.
  static const _maxScrollbackChars = 500000;

  /// The live terminal emulator for [sessionId], if one has been created by
  /// [start], [restart], or restoring a persisted session. `null` only
  /// before the session's first frame builds.
  xterm.Terminal? terminalFor(String sessionId) => _terminals[sessionId];

  /// Creates (or reuses) the VT100 emulator backing [sessionId]. Reused on
  /// [restart] so scrollback survives a session ending and being restarted in
  /// place; a fresh one is only made when none exists yet (first start, or
  /// restoring a session from a previous app run -- see [_loadSession]).
  xterm.Terminal _terminalFor(String sessionId, {required int cols, required int rows}) {
    final existing = _terminals[sessionId];
    if (existing != null) return existing;
    final terminal = xterm.Terminal(
      maxLines: 10000,
      platform: ideTerminalTargetPlatform(defaultTargetPlatform),
    );
    terminal.onOutput = (data) => sendInput(sessionId, Uint8List.fromList(utf8.encode(data)));
    // xterm invokes this synchronously from RenderTerminal.performLayout, so calling
    // resize() (which mutates provider state) directly here throws Riverpod's
    // "modify a provider while the widget tree was building" error. Defer to a
    // microtask so the state update happens after the current frame finishes.
    terminal.onResize = (width, height, pixelWidth, pixelHeight) =>
        Future.microtask(() => resize(sessionId, width, height));
    terminal.resize(cols, rows);
    _terminals[sessionId] = terminal;
    return terminal;
  }

  void _subscribeToEvents() {
    _eventSub = _eventBus.stream.listen((event) {
      if (event is WorkspaceOpened) _loadSession(event.workspaceId);
    });
    _eventSub?.onDone(() => _eventSub = null);
  }

  /// Restores persisted scrollback/metadata for [workspaceId]. The live OS
  /// process cannot survive an app restart (ConPTY dies with the session
  /// that created it), so every restored session comes back `isRunning:
  /// false` regardless of what was last saved -- never a silent fake-resume.
  ///
  /// Runs off the `WorkspaceOpened` event, which fires around the same time
  /// `TerminalView`'s auto-start logic schedules a brand-new live [start] for
  /// that same workspace on the next frame -- there is no ordering guarantee
  /// between this DB read and that call. Unconditionally replacing `state`
  /// here (as this used to do) meant that whichever finished second won,
  /// silently wiping out either a just-started live session or -- since
  /// `state.sessions` spans every open workspace, not just this one -- every
  /// *other* workspace's sessions too. Bailing out whenever a session for
  /// [workspaceId] already exists (checked both before and after the await)
  /// makes this idempotent against that race: a live session, once present,
  /// always wins over restoring stale scrollback on top of it.
  Future<void> _loadSession(String workspaceId) async {
    if (state.sessions.any((s) => s.workspaceId == workspaceId)) return;
    final row = await (_db.select(_db.desktopTerminalState)
          ..where((t) => t.workspaceId.equals(workspaceId)))
        .getSingleOrNull();
    if (state.sessions.any((s) => s.workspaceId == workspaceId)) return;
    final otherSessions = state.sessions.where((s) => s.workspaceId != workspaceId).toList();
    if (row == null) {
      state = state.copyWith(sessions: otherSessions);
      return;
    }
    try {
      final decoded = jsonDecode(row.sessionsJson) as List<dynamic>;
      final sessions = decoded
          .cast<Map<String, dynamic>>()
          .map((json) => TerminalSessionState(
                id: json['id'] as String,
                workspaceId: workspaceId,
                command: json['command'] as String?,
                output: json['output'] as String? ?? '',
                isRunning: false,
                exitCode: json['exitCode'] as int?,
                cols: json['cols'] as int? ?? 80,
                rows: json['rows'] as int? ?? 24,
              ))
          .toList();
      for (final session in sessions) {
        // Replaying the raw scrollback through a fresh emulator (rather than
        // just keeping the string around for display) reconstructs the actual
        // last-rendered screen -- cursor position, colors, cleared regions --
        // instead of showing the raw escape bytes as literal text.
        _terminalFor(session.id, cols: session.cols, rows: session.rows).write(session.output);
      }
      state = TerminalState(
        sessions: [...otherSessions, ...sessions],
        activeSessionId: sessions.firstOrNull?.id ?? state.activeSessionId,
      );
    } catch (_) {
      state = state.copyWith(sessions: otherSessions);
    }
  }

  /// Starts a new session against [rootPath] and makes it active. Does not
  /// itself check workspace trust -- the daemon enforces that authoritatively
  /// (see `TerminalSessionManager` in aura_hostd) and reports a rejection as
  /// an `error` frame; callers with `ref` access (the UI) may still check
  /// `HostdRoot.trusted` first purely to skip a round trip for the common
  /// case, since that's a UX nicety, not the actual security boundary.
  Future<String> start({
    required String workspaceId,
    required String rootPath,
    String? command,
    int cols = 80,
    int rows = 24,
  }) async {
    final sessionId = 'term_${DateTime.now().microsecondsSinceEpoch}';
    final session = TerminalSessionState(
      id: sessionId,
      workspaceId: workspaceId,
      command: command,
      isRunning: true,
      cols: cols,
      rows: rows,
    );
    state = state.copyWith(
      sessions: [...state.sessions, session],
      activeSessionId: sessionId,
    );
    _terminalFor(sessionId, cols: cols, rows: rows);

    final client = HostdTerminalClient();
    _clients[sessionId] = client;
    client.output.listen((bytes) => _appendOutput(sessionId, utf8.decode(bytes, allowMalformed: true)));
    client.exitCode.listen((code) => _handleExit(sessionId, code));
    client.errors.listen((message) => _appendOutput(sessionId, '\n[error] $message\n', exited: true));

    try {
      await client.connect(rootPath: rootPath, cols: cols, rows: rows);
      _eventBus.publish(TerminalStarted(sessionId, workspaceId, command: command));
    } catch (error) {
      _appendOutput(sessionId, '[error] $error\n', exited: true);
    }
    return sessionId;
  }

  void setActive(String sessionId) {
    if (state.sessions.any((s) => s.id == sessionId)) {
      state = state.copyWith(activeSessionId: sessionId);
    }
  }

  void sendInput(String sessionId, Uint8List bytes) {
    _clients[sessionId]?.sendInput(bytes);
  }

  void resize(String sessionId, int cols, int rows) {
    _clients[sessionId]?.resize(cols, rows);
    _updateSession(sessionId, (s) => s.copyWith(cols: cols, rows: rows));
  }

  /// Restarts a session that already ended (restored from a previous run, or
  /// exited in this one) -- reuses the same session id so the UI's scrollback
  /// continues rather than spawning a new tab entry.
  Future<void> restart(String sessionId, {required String rootPath}) async {
    final existing = state.sessions.where((s) => s.id == sessionId).firstOrNull;
    if (existing == null || existing.isRunning) return;
    _updateSession(sessionId, (s) => s.copyWith(isRunning: true, clearExitCode: true));
    // Reuses the existing emulator (created by _loadSession/start) so its scrollback survives
    // the restart rather than starting from a blank screen.
    _terminalFor(sessionId, cols: existing.cols, rows: existing.rows);

    final client = HostdTerminalClient();
    _clients[sessionId] = client;
    client.output.listen((bytes) => _appendOutput(sessionId, utf8.decode(bytes, allowMalformed: true)));
    client.exitCode.listen((code) => _handleExit(sessionId, code));
    client.errors.listen((message) => _appendOutput(sessionId, '\n[error] $message\n', exited: true));

    try {
      await client.connect(rootPath: rootPath, cols: existing.cols, rows: existing.rows);
    } catch (error) {
      _appendOutput(sessionId, '[error] $error\n', exited: true);
    }
  }

  Future<void> closeSession(String sessionId) async {
    final workspaceId = state.sessions.where((s) => s.id == sessionId).firstOrNull?.workspaceId;
    final client = _clients.remove(sessionId);
    await client?.dispose();
    _terminals.remove(sessionId);
    final wasActive = state.activeSessionId == sessionId;
    state = state.copyWith(
      sessions: state.sessions.where((s) => s.id != sessionId).toList(),
      clearActiveSessionId: wasActive,
    );
    if (workspaceId != null) await _persist(workspaceId);
  }

  void _appendOutput(String sessionId, String text, {bool exited = false}) {
    final session = state.sessions.where((s) => s.id == sessionId).firstOrNull;
    if (session == null) return;
    var combined = session.output + text;
    if (combined.length > _maxScrollbackChars) {
      combined = combined.substring(combined.length - _maxScrollbackChars);
    }
    _updateSession(sessionId, (s) => s.copyWith(output: combined, isRunning: exited ? false : s.isRunning));
    // Feeds the real VT100 emulator so escape sequences (clear, cursor move, color) actually
    // take effect instead of the terminal panel showing them as literal text.
    _terminals[sessionId]?.write(text);
    _eventBus.publish(TerminalOutput(sessionId, text));
    _debouncedPersist(session.workspaceId);
  }

  void _handleExit(String sessionId, int code) {
    _updateSession(sessionId, (s) => s.copyWith(isRunning: false, exitCode: code));
    _eventBus.publish(TerminalExited(sessionId, code));
    final workspaceId = state.sessions.where((s) => s.id == sessionId).firstOrNull?.workspaceId;
    if (workspaceId != null) unawaited(_persist(workspaceId));
  }

  void _updateSession(String sessionId, TerminalSessionState Function(TerminalSessionState) update) {
    state = state.copyWith(
      sessions: state.sessions.map((s) => s.id == sessionId ? update(s) : s).toList(),
    );
  }

  void _debouncedPersist(String workspaceId) {
    _saveDebounce?.cancel();
    _saveDebounce = Timer(const Duration(seconds: 2), () => unawaited(_persist(workspaceId)));
  }

  /// Persists every session for [workspaceId], always as `isRunning: false`
  /// regardless of live state -- a restored session must never look live.
  Future<void> _persist(String? workspaceId) async {
    if (workspaceId == null) return;
    final sessions = state.sessions.where((s) => s.workspaceId == workspaceId).toList();
    if (sessions.isEmpty) {
      await (_db.delete(_db.desktopTerminalState)..where((t) => t.workspaceId.equals(workspaceId))).go();
      return;
    }
    final sessionsJson = jsonEncode(sessions
        .map((s) => {
              'id': s.id,
              'command': s.command,
              'output': s.output,
              'exitCode': s.exitCode,
              'cols': s.cols,
              'rows': s.rows,
            })
        .toList());
    await _db.into(_db.desktopTerminalState).insertOnConflictUpdate(
          DesktopTerminalStateCompanion.insert(workspaceId: workspaceId, sessionsJson: sessionsJson),
        );
  }

  @override
  void dispose() {
    _eventSub?.cancel();
    _saveDebounce?.cancel();
    for (final client in _clients.values) {
      unawaited(client.dispose());
    }
    super.dispose();
  }
}

/// Layout state notifier. Persists per-workspace panel widths/heights,
/// visibility, and the active primary-sidebar panel to `desktopLayoutState`
/// (mirroring [EditorNotifier]/[TerminalNotifier]'s own WorkspaceOpened-driven
/// load, and their own debounced-write pattern for the high-frequency case --
/// here, drag-resize) so layout survives an app restart instead of always
/// resetting to [LayoutState]'s defaults.
class LayoutNotifier extends StateNotifier<LayoutState> {
  LayoutNotifier(this._db, this._eventBus) : super(const LayoutState()) {
    _subscribeToEvents();
  }

  final AppDatabase _db;
  final IdeEventBus _eventBus;
  StreamSubscription? _eventSub;
  Timer? _saveDebounce;

  static const _minSidebarWidth = 180.0;
  static const _maxSidebarWidth = 640.0;
  static const _minBottomPanelHeight = 120.0;
  static const _maxBottomPanelHeight = 640.0;

  static double _clampWidth(double value) => value.clamp(_minSidebarWidth, _maxSidebarWidth).toDouble();
  static double _clampHeight(double value) =>
      value.clamp(_minBottomPanelHeight, _maxBottomPanelHeight).toDouble();

  void _subscribeToEvents() {
    _eventSub = _eventBus.stream.listen((event) {
      if (event is WorkspaceOpened) unawaited(_loadLayout(event.workspaceId));
      if (event is LayoutChanged) {
        state = state.copyWith(
          primarySidebarWidth: event.panelSizes['primary'],
          secondarySidebarWidth: event.panelSizes['secondary'],
          bottomPanelHeight: event.panelSizes['bottom'],
        );
      }
    });
    _eventSub?.onDone(() => _eventSub = null);
  }

  /// Restores the layout persisted for [workspaceId] (see [_persist]), or falls back to
  /// [LayoutState]'s defaults if this workspace has never had one saved.
  Future<void> _loadLayout(String workspaceId) async {
    final row = await (_db.select(_db.desktopLayoutState)
          ..where((t) => t.workspaceId.equals(workspaceId)))
        .getSingleOrNull();
    if (row == null) {
      state = LayoutState(workspaceId: workspaceId);
      return;
    }
    try {
      final geometry = jsonDecode(row.windowGeometryJson) as Map<String, dynamic>;
      final visibility = jsonDecode(row.panelVisibilityJson) as Map<String, dynamic>;
      const defaults = LayoutState();
      state = LayoutState(
        workspaceId: workspaceId,
        primarySidebarWidth:
            (geometry['primarySidebarWidth'] as num?)?.toDouble() ?? defaults.primarySidebarWidth,
        secondarySidebarWidth:
            (geometry['secondarySidebarWidth'] as num?)?.toDouble() ?? defaults.secondarySidebarWidth,
        bottomPanelHeight: (geometry['bottomPanelHeight'] as num?)?.toDouble() ?? defaults.bottomPanelHeight,
        showPrimarySidebar: visibility['showPrimarySidebar'] as bool? ?? defaults.showPrimarySidebar,
        showSecondarySidebar: visibility['showSecondarySidebar'] as bool? ?? defaults.showSecondarySidebar,
        showBottomPanel: visibility['showBottomPanel'] as bool? ?? defaults.showBottomPanel,
        primarySidebarPanel: PrimarySidebarPanel.values
                .firstWhereOrNull((p) => p.name == visibility['primarySidebarPanel']) ??
            defaults.primarySidebarPanel,
      );
    } catch (_) {
      state = LayoutState(workspaceId: workspaceId);
    }
  }

  /// Persists the full current layout immediately -- used for toggles/panel
  /// switches, which are infrequent enough not to need debouncing.
  Future<void> _persist() async {
    final workspaceId = state.workspaceId;
    if (workspaceId == null) return;
    final geometryJson = jsonEncode({
      'primarySidebarWidth': state.primarySidebarWidth,
      'secondarySidebarWidth': state.secondarySidebarWidth,
      'bottomPanelHeight': state.bottomPanelHeight,
    });
    final visibilityJson = jsonEncode({
      'showPrimarySidebar': state.showPrimarySidebar,
      'showSecondarySidebar': state.showSecondarySidebar,
      'showBottomPanel': state.showBottomPanel,
      'primarySidebarPanel': state.primarySidebarPanel.name,
    });
    await _db.into(_db.desktopLayoutState).insertOnConflictUpdate(
          DesktopLayoutStateCompanion.insert(
            workspaceId: workspaceId,
            windowGeometryJson: geometryJson,
            panelVisibilityJson: visibilityJson,
          ),
        );
    _eventBus.publish(LayoutChanged(workspaceId, {
      'primary': state.primarySidebarWidth,
      'secondary': state.secondarySidebarWidth,
      'bottom': state.bottomPanelHeight,
    }));
  }

  /// Debounced persist for drag-resize, which fires on every pointer-move frame -- writing to
  /// Drift on every one of those would be needless disk I/O per pixel dragged.
  void _debouncedPersist() {
    _saveDebounce?.cancel();
    _saveDebounce = Timer(const Duration(milliseconds: 400), () => unawaited(_persist()));
  }

  void setPrimarySidebarWidth(double width) {
    state = state.copyWith(primarySidebarWidth: _clampWidth(width));
    _debouncedPersist();
  }

  void setSecondarySidebarWidth(double width) {
    state = state.copyWith(secondarySidebarWidth: _clampWidth(width));
    _debouncedPersist();
  }

  void setBottomPanelHeight(double height) {
    state = state.copyWith(bottomPanelHeight: _clampHeight(height));
    _debouncedPersist();
  }

  /// Adds [delta] to the *current notifier state's* width/height rather than one a caller
  /// captured earlier -- a drag-resize handle fires many updates per gesture, and Flutter
  /// batches widget rebuilds, so a widget-side `lastKnownWidth + delta` would lose deltas
  /// whenever two update callbacks land before a rebuild. Reading `state` here instead of a
  /// widget-captured snapshot makes every update authoritative regardless of rebuild timing.
  void adjustPrimarySidebarWidth(double delta) => setPrimarySidebarWidth(state.primarySidebarWidth + delta);

  void adjustSecondarySidebarWidth(double delta) =>
      setSecondarySidebarWidth(state.secondarySidebarWidth + delta);

  void adjustBottomPanelHeight(double delta) => setBottomPanelHeight(state.bottomPanelHeight + delta);

  void togglePrimarySidebar() {
    state = state.copyWith(showPrimarySidebar: !state.showPrimarySidebar);
    _publishVisibility('primary', state.showPrimarySidebar);
    unawaited(_persist());
  }

  /// Shows the Explorer panel in the primary sidebar -- opening it if it was collapsed, and
  /// switching [LayoutState.primarySidebarPanel] to it if Source Control was showing instead.
  void showExplorerPanel() {
    state = state.copyWith(showPrimarySidebar: true, primarySidebarPanel: PrimarySidebarPanel.explorer);
    _publishVisibility('primary', true);
    unawaited(_persist());
  }

  /// Shows the Source Control (Git) panel in the primary sidebar -- D5's activity-bar wiring.
  void showSourceControlPanel() {
    state = state.copyWith(showPrimarySidebar: true, primarySidebarPanel: PrimarySidebarPanel.sourceControl);
    _publishVisibility('primary', true);
    unawaited(_persist());
  }

  /// Shows the "Agent Review" panel in the primary sidebar -- D9's activity-bar wiring, closing
  /// the D6 stub on `ActivityBar`'s "AURA" item.
  void showControlCenterPanel() {
    state = state.copyWith(showPrimarySidebar: true, primarySidebarPanel: PrimarySidebarPanel.controlCenter);
    _publishVisibility('primary', true);
    unawaited(_persist());
  }

  void toggleSecondarySidebar() {
    state = state.copyWith(showSecondarySidebar: !state.showSecondarySidebar);
    _publishVisibility('secondary', state.showSecondarySidebar);
    unawaited(_persist());
  }

  void toggleBottomPanel() {
    state = state.copyWith(showBottomPanel: !state.showBottomPanel);
    _publishVisibility('bottom', state.showBottomPanel);
    unawaited(_persist());
  }

  /// No-ops silently when no workspace is open yet -- visibility toggles are reachable
  /// before [_loadLayout] ever runs (e.g. the workspace picker's own shell chrome).
  void _publishVisibility(String panelId, bool visible) {
    final workspaceId = state.workspaceId;
    if (workspaceId == null) return;
    _eventBus.publish(PanelVisibilityChanged(workspaceId, panelId, visible));
  }

  @override
  void dispose() {
    _eventSub?.cancel();
    _saveDebounce?.cancel();
    super.dispose();
  }
}

/// Host state notifier.
class HostNotifier extends StateNotifier<HostState> {
  HostNotifier(this._eventBus) : super(const HostState()) {
    _subscribeToEvents();
  }

  final IdeEventBus _eventBus;
  StreamSubscription? _eventSub;

  void _subscribeToEvents() {
    _eventSub = _eventBus.stream
        .where((e) => e is HostConnected || e is HostDisconnected || e is HostRootGranted || e is HostRootRevoked)
        .listen(_handleHostEvent);
    _eventSub?.onDone(() => _eventSub = null);
  }

  void _handleHostEvent(IdeEvent event) {
    switch (event) {
      case HostConnected():
        state = state.copyWith(isConnected: true);
      case HostDisconnected():
        state = state.copyWith(isConnected: false, grantedRoots: []);
      case HostRootGranted():
        state = state.copyWith(grantedRoots: [...state.grantedRoots, event.rootPath]);
      case HostRootRevoked():
        state = state.copyWith(grantedRoots: state.grantedRoots.where((r) => r != event.rootPath).toList());
      default:
        // The .where() filter in _subscribeToEvents already narrows to the
        // four cases above; this default only exists to satisfy exhaustive
        // switch-statement checking over the sealed IdeEvent hierarchy.
        break;
    }
  }

  /// Mirrors a polled [HostdStatusSnapshot] (from `hostdStatusProvider`,
  /// the same source `HostPanel`/`HostDaemonPage` use) into this state, so
  /// callers of [hostNotifierProvider] see live status. This is the only
  /// path that populates [HostState] from `/status` — a prior one-shot
  /// `refreshStatus()` method duplicated this via direct `HostdControlClient`
  /// calls but had zero call sites anywhere in the app; removed rather than
  /// left as dead code (2026-09-25 audit follow-up). That removal left this
  /// notifier's own `HostdControlClient` unused, so it is gone too -- this
  /// class reaches the daemon only through the polled snapshot above.
  void updateFromSnapshot(HostdStatusSnapshot snapshot) {
    state = state.copyWith(
      isConnected: snapshot.authenticated,
      backendUrl: snapshot.backendUrl,
      grantedRoots: snapshot.roots.map((r) => r.path).toList(),
    );
  }

  @override
  void dispose() {
    _eventSub?.cancel();
    super.dispose();
  }
}

/// Approval state notifier -- D5's interactive consent tier. Wired to [pendingApprovalsProvider]
/// (a 2s poll of `GET /approvals`) at the provider definition below, mirroring
/// [hostNotifierProvider]'s exact `ref.listen(..., fireImmediately: true)` pattern.
class ApprovalNotifier extends StateNotifier<ApprovalState> {
  ApprovalNotifier(this._eventBus, this._hostdClient) : super(const ApprovalState()) {
    _subscribeToEvents();
  }

  final IdeEventBus _eventBus;
  final HostdControlClient _hostdClient;
  StreamSubscription? _eventSub;

  void _subscribeToEvents() {
    _eventSub = _eventBus.stream
        .where((e) => e is ApprovalRequested)
        .cast<ApprovalRequested>()
        .listen((event) {
          state = state.copyWith(
            pendingApprovals: [...state.pendingApprovals, PendingApproval(
              id: event.approvalId,
              tool: event.tool,
              arguments: event.arguments,
              workspaceId: event.workspaceId,
              requestedAt: DateTime.now(),
            )],
          );
        });
    _eventSub?.onDone(() => _eventSub = null);
  }

  /// Reconciles the polled [snapshots] against [ApprovalState.pendingApprovals]: publishes
  /// [ApprovalRequested] for any id not already known (the listener above is the only thing that
  /// actually appends to state, so this never double-adds) and directly drops any id no longer in
  /// the snapshot (resolved or expired, possibly by another client or the daemon's own timeout --
  /// there is no specific approve/reject outcome to report for that case, so no [ApprovalResponded]
  /// is published for it). [workspaceIdForRoot] maps a daemon-side root path to this app's own
  /// workspace id, the same way `terminal_view.dart` does for terminal sessions -- the daemon has
  /// no `workspaceId` concept of its own (see `approval_queue.dart`'s doc comment).
  void updateFromSnapshot(
    List<PendingApprovalSnapshot> snapshots, {
    required String Function(String root) workspaceIdForRoot,
  }) {
    final knownIds = state.pendingApprovals.map((a) => a.id).toSet();
    for (final snapshot in snapshots) {
      if (knownIds.contains(snapshot.id)) continue;
      // D6: a write-kind snapshot carries a diff instead of a command string -- `tool` and
      // `arguments` stay the same generic shape `ApprovalRequested`/`PendingApproval` already
      // had (no client-side model change needed beyond this branch), just populated differently
      // per kind so `status_bar.dart`'s dialog can tell which one it's rendering.
      final arguments = snapshot.kind == PendingApprovalKind.write
          ? <String, dynamic>{
              'diff_preview': snapshot.diffPreview,
              'paths': snapshot.paths,
              'cwd': snapshot.cwd,
              'root': snapshot.root,
            }
          : <String, dynamic>{'command': snapshot.command, 'cwd': snapshot.cwd, 'root': snapshot.root};
      _eventBus.publish(ApprovalRequested(
        snapshot.id,
        snapshot.kind == PendingApprovalKind.write ? 'write_files' : 'run_command',
        arguments,
        workspaceIdForRoot(snapshot.root),
      ));
    }
    final snapshotIds = snapshots.map((s) => s.id).toSet();
    final stillPending = state.pendingApprovals.where((a) => snapshotIds.contains(a.id)).toList();
    if (stillPending.length != state.pendingApprovals.length) {
      state = state.copyWith(pendingApprovals: stillPending);
    }
  }

  /// Removes [approvalId] from local state immediately (optimistic -- the UI shouldn't wait on
  /// the round-trip) and tells the daemon. A failed daemon call is tolerated silently: the next
  /// poll re-adds the approval if the daemon never actually saw this response, giving the human
  /// another chance to answer it rather than losing the request.
  Future<void> respond(String approvalId, bool approved) async {
    state = state.copyWith(
      pendingApprovals: state.pendingApprovals.where((a) => a.id != approvalId).toList(),
    );
    try {
      await _hostdClient.respondToApproval(approvalId, approved);
    } catch (_) {
      // Best-effort; see doc comment above.
    }
    _eventBus.publish(ApprovalResponded(approvalId, approved));
  }

  @override
  void dispose() {
    _eventSub?.cancel();
    super.dispose();
  }
}

/// Notification state notifier.
class NotificationNotifier extends StateNotifier<NotificationState> {
  NotificationNotifier() : super(const NotificationState());

  void show(NotificationItem notification) {
    state = state.copyWith(notifications: [...state.notifications, notification]);
    // Auto-dismiss after 5 seconds for non-error notifications
    if (notification.type != NotificationType.error) {
      Future.delayed(const Duration(seconds: 5), () {
        dismiss(notification.id);
      });
    }
  }

  void dismiss(String id) {
    state = state.copyWith(
      notifications: state.notifications.where((n) => n.id != id).toList(),
    );
  }

  void clear() {
    state = state.copyWith(notifications: []);
  }
}

/// Provider for workspace notifier.
final workspaceNotifierProvider = StateNotifierProvider<WorkspaceNotifier, WorkspaceState>((ref) {
  final db = ref.watch(databaseProvider);
  final eventBus = ref.watch(ideEventBusProvider);
  final notifier = WorkspaceNotifier(db, eventBus);
  // Keep hostdRoots in sync with the daemon's actual granted/trusted roots, the same way
  // hostNotifierProvider mirrors hostdStatusProvider into its own grantedRoots. Without this,
  // hostdRoots stayed at its default empty list until the user re-ran the "Open Folder" grant
  // flow in *this* session (the only other writer, in workspace_welcome_page.dart /
  // workspace_manager_page.dart) -- so reopening an already-granted workspace from Recent left
  // GitPanel/TerminalView showing "Grant this workspace..." for a root the daemon already trusts.
  ref.listen(hostdStatusProvider, (previous, next) {
    final snapshot = next.value;
    if (snapshot != null) {
      notifier.updateHostdRoots(
        snapshot.roots.map((r) => HostdRoot(path: r.path, trusted: r.trusted)).toList(),
      );
    }
  }, fireImmediately: true);
  return notifier;
});

/// Provider for editor notifier.
final editorNotifierProvider = StateNotifierProvider<EditorNotifier, EditorState>((ref) {
  final db = ref.watch(databaseProvider);
  final eventBus = ref.watch(ideEventBusProvider);
  return EditorNotifier(
    db,
    eventBus,
    rootPathFor: (workspaceId) => ref
        .read(workspaceNotifierProvider)
        .workspaces
        .where((w) => w.id == workspaceId)
        .firstOrNull
        ?.rootPath,
  );
});

/// Provider for terminal notifier.
final terminalNotifierProvider = StateNotifierProvider<TerminalNotifier, TerminalState>((ref) {
  final db = ref.watch(databaseProvider);
  final eventBus = ref.watch(ideEventBusProvider);
  return TerminalNotifier(db, eventBus);
});

/// Provider for git notifier (D5).
final gitNotifierProvider = StateNotifierProvider<GitNotifier, GitState>((ref) {
  final eventBus = ref.watch(ideEventBusProvider);
  final hostdClient = ref.watch(hostdControlClientProvider);
  return GitNotifier(eventBus, hostdClient);
});

/// Provider for layout notifier.
final layoutNotifierProvider = StateNotifierProvider<LayoutNotifier, LayoutState>((ref) {
  final db = ref.watch(databaseProvider);
  final eventBus = ref.watch(ideEventBusProvider);
  return LayoutNotifier(db, eventBus);
});

/// Provider for host notifier.
final hostNotifierProvider = StateNotifierProvider<HostNotifier, HostState>((ref) {
  final eventBus = ref.watch(ideEventBusProvider);
  final notifier = HostNotifier(eventBus);
  // Keep this notifier's isConnected/backendUrl/grantedRoots in sync with
  // the same polled status HostPanel/HostDaemonPage show, so every host
  // status display in the app agrees.
  ref.listen(hostdStatusProvider, (previous, next) {
    final snapshot = next.value;
    if (snapshot != null) notifier.updateFromSnapshot(snapshot);
  }, fireImmediately: true);
  return notifier;
});

/// Provider for approval notifier.
final approvalNotifierProvider = StateNotifierProvider<ApprovalNotifier, ApprovalState>((ref) {
  final eventBus = ref.watch(ideEventBusProvider);
  final hostdClient = ref.watch(hostdControlClientProvider);
  final notifier = ApprovalNotifier(eventBus, hostdClient);
  // Keep this notifier in sync with the daemon's actual pending-approval list -- see
  // ApprovalNotifier.updateFromSnapshot's doc comment.
  ref.listen(pendingApprovalsProvider, (previous, next) {
    final snapshots = next.value;
    if (snapshots == null) return;
    final workspaces = ref.read(workspaceNotifierProvider).workspaces;
    notifier.updateFromSnapshot(
      snapshots,
      workspaceIdForRoot: (root) =>
          workspaces.firstWhereOrNull((w) => w.rootPath == root)?.id ?? root,
    );
  }, fireImmediately: true);
  return notifier;
});

/// Provider for notification notifier.
final notificationNotifierProvider = StateNotifierProvider<NotificationNotifier, NotificationState>((ref) {
  return NotificationNotifier();
});