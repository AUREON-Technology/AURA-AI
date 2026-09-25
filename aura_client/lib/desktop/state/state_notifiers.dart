/// Riverpod notifiers for Desktop IDE state.
///
/// Each domain has its own notifier to avoid a giant monolithic state.
library;

import 'dart:async';
import 'dart:convert';

import 'package:collection/collection.dart';
import 'package:drift/drift.dart';
import 'package:flutter_riverpod/legacy.dart';
import 'package:xterm/xterm.dart' as xterm;
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/hostd/hostd_control_client.dart';
import 'package:aura_client/core/hostd/hostd_providers.dart' hide HostdRoot;
import 'package:aura_client/core/hostd/hostd_terminal_client.dart';
import 'package:aura_client/desktop/editor/editor_abstraction.dart';
import 'package:aura_client/desktop/state/desktop_application_state.dart';
import 'package:aura_client/desktop/state/git_notifier.dart';
import 'package:aura_client/desktop/events/ide_event_bus.dart';
import 'package:aura_client/desktop/events/ide_event.dart';
import 'package:aura_client/desktop/editor/editor_tab.dart';

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
  EditorNotifier(this._db, this._eventBus) : super(const EditorState()) {
    _subscribeToEvents();
  }

  final AppDatabase _db;
  final IdeEventBus _eventBus;

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
  }

  void _subscribeToEvents() {
    _eventSub = _eventBus.stream.listen((event) {
      if (event is TabOpened) _handleTabOpened(event);
      if (event is WorkspaceOpened) _loadSession(event.workspaceId);
    });
    _eventSub?.onDone(() => _eventSub = null);
  }

  /// Restores the tab list persisted for [workspaceId] (see [saveSession]),
  /// or clears the editor if this workspace has never had a session saved.
  Future<void> _loadSession(String workspaceId) async {
    final row = await (_db.select(_db.desktopEditorState)
          ..where((t) => t.workspaceId.equals(workspaceId)))
        .getSingleOrNull();
    if (row == null) {
      state = EditorState(workspaceId: workspaceId);
      return;
    }
    try {
      final decoded = jsonDecode(row.openTabsJson) as List<dynamic>;
      final tabs = decoded
          .cast<Map<String, dynamic>>()
          .map((json) => EditorTab(
                id: json['id'] as String,
                path: json['path'] as String,
                title: json['title'] as String,
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
              ))
          .toList();
      final activeTab = tabs.where((t) => t.path == row.activeTabPath).firstOrNull;
      state = EditorState(
        tabs: tabs,
        activeTabId: activeTab?.id,
        workspaceId: workspaceId,
      );
    } catch (_) {
      state = EditorState(workspaceId: workspaceId);
    }
  }

  void _handleTabOpened(TabOpened event) {
    final exists = state.tabs.any((t) => t.id == event.tabId);
    if (!exists) {
      final newTab = EditorTab(
        id: event.tabId,
        path: event.path,
        title: event.path.split('/').last,
        lastAccessed: DateTime.now(),
      );
      state = state.copyWith(
        tabs: [...state.tabs, newTab],
        activeTabId: event.tabId,
      );
    } else {
      state = state.copyWith(activeTabId: event.tabId);
    }
  }

  Future<void> openTab({
    required String tabId,
    required String path,
    required String workspaceId,
  }) async {
    final exists = state.tabs.any((t) => t.id == tabId);
    if (!exists) {
      final newTab = EditorTab(
        id: tabId,
        path: path,
        title: path.split('/').last,
        lastAccessed: DateTime.now(),
      );
      state = state.copyWith(
        tabs: [...state.tabs, newTab],
        activeTabId: tabId,
        workspaceId: workspaceId,
      );
    } else {
      state = state.copyWith(activeTabId: tabId);
    }
    _eventBus.publish(TabOpened(tabId, path, workspaceId));
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
    _tabContents.remove(tabId);
    _eventBus.publish(TabClosed(tabId, state.workspaceId ?? ''));
    await saveSession();
  }

  Future<void> setActiveTab(String tabId) async {
    state = state.copyWith(activeTabId: tabId);
    final tab = state.tabs.where((t) => t.id == tabId).firstOrNull;
    if (tab != null) {
      _eventBus.publish(TabActivated(tabId, state.workspaceId ?? ''));
    }
    await saveSession();
  }

  Future<void> markDirty(String tabId, bool isDirty) async {
    state = state.copyWith(
      tabs: state.tabs.map((t) => t.id == tabId ? t.copyWith(isDirty: isDirty) : t).toList(),
    );
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
      platform: xterm.TerminalTargetPlatform.windows,
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
  HostNotifier(this._eventBus, this._hostdClient) : super(const HostState()) {
    _subscribeToEvents();
  }

  final IdeEventBus _eventBus;
  final HostdControlClient _hostdClient;
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
  /// `refreshStatus()` method duplicated this via direct `_hostdClient`
  /// calls but had zero call sites anywhere in the app; removed rather than
  /// left as dead code (2026-09-25 audit follow-up).
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
  return EditorNotifier(db, eventBus);
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
  final hostdClient = ref.watch(hostdControlClientProvider);
  final notifier = HostNotifier(eventBus, hostdClient);
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