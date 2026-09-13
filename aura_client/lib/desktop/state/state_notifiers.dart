/// Riverpod notifiers for Desktop IDE state.
///
/// Each domain has its own notifier to avoid a giant monolithic state.
library;

import 'dart:async';
import 'dart:convert';

import 'package:collection/collection.dart';
import 'package:drift/drift.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/hostd/hostd_control_client.dart';
import 'package:aura_client/core/hostd/hostd_providers.dart' hide HostdRoot;
import 'package:aura_client/desktop/editor/editor_abstraction.dart';
import 'package:aura_client/desktop/state/desktop_application_state.dart';
import 'package:aura_client/desktop/events/ide_event_bus.dart';
import 'package:aura_client/desktop/events/ide_event.dart';
import 'package:aura_client/desktop/editor/editor_tab.dart';

/// Workspace state notifier.
class WorkspaceNotifier extends StateNotifier<WorkspaceState> {
  WorkspaceNotifier(this._db, this._eventBus, this._hostdClient) : super(const WorkspaceState()) {
    _loadWorkspaces();
  }

  final AppDatabase _db;
  final IdeEventBus _eventBus;
  final HostdControlClient _hostdClient;

  // Note: this notifier does NOT subscribe to WorkspaceOpened on the event
  // bus — setActiveWorkspace() below is the only place that changes
  // activeWorkspaceId, and it publishes WorkspaceOpened itself. Reacting to
  // that same event here would call setActiveWorkspace() again, which
  // publishes again, forever.

  Future<void> _loadWorkspaces() async {
    final workspaces = await _db.select(_db.desktopWorkspaces).get();
    state = state.copyWith(
      workspaces: workspaces.map(DesktopWorkspace.fromDb).toList(),
    );
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
    await _db.delete(_db.desktopWorkspaces).where((t) => t.id.equals(workspaceId)).go();
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
  }

  Future<void> updateSelection(String tabId, EditorSelection selection) async {
    state = state.copyWith(
      tabs: state.tabs.map((t) => t.id == tabId ? t.copyWith(selection: selection) : t).toList(),
    );
  }

  Future<void> updateTab(String tabId, EditorTab Function(EditorTab) update) async {
    state = state.copyWith(
      tabs: state.tabs.map((t) => t.id == tabId ? update(t) : t).toList(),
    );
  }

  /// Persists the open tab list and active tab for the current workspace, so
  /// [_loadSession] can restore it after an app restart. Only tab identity
  /// (id/path/title/pinned) is saved, not buffer content — reopening a tab
  /// re-reads the file from disk.
  Future<void> saveSession() async {
    final workspaceId = state.workspaceId;
    if (workspaceId == null) return;
    final tabsJson = jsonEncode(state.tabs
        .map((t) => {
              'id': t.id,
              'path': t.path,
              'title': t.title,
              'isPinned': t.isPinned,
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
    super.dispose();
  }
}

/// Layout state notifier.
class LayoutNotifier extends StateNotifier<LayoutState> {
  LayoutNotifier(this._eventBus) : super(const LayoutState()) {
    _subscribeToEvents();
  }

  final IdeEventBus _eventBus;
  StreamSubscription? _eventSub;

  void _subscribeToEvents() {
    _eventSub = _eventBus.stream
        .whereType<LayoutChanged>()
        .listen((event) {
          state = state.copyWith(
            primarySidebarWidth: event.panelSizes['primary'],
            secondarySidebarWidth: event.panelSizes['secondary'],
            bottomPanelHeight: event.panelSizes['bottom'],
          );
        });
    _eventSub?.onDone(() => _eventSub = null);
  }

  void setPrimarySidebarWidth(double width) {
    state = state.copyWith(primarySidebarWidth: width);
  }

  void setSecondarySidebarWidth(double width) {
    state = state.copyWith(secondarySidebarWidth: width);
  }

  void setBottomPanelHeight(double height) {
    state = state.copyWith(bottomPanelHeight: height);
  }

  void togglePrimarySidebar() {
    state = state.copyWith(showPrimarySidebar: !state.showPrimarySidebar);
  }

  void toggleSecondarySidebar() {
    state = state.copyWith(showSecondarySidebar: !state.showSecondarySidebar);
  }

  void toggleBottomPanel() {
    state = state.copyWith(showBottomPanel: !state.showBottomPanel);
  }

  @override
  void dispose() {
    _eventSub?.cancel();
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
    }
  }

  Future<void> refreshStatus() async {
    try {
      // Reaching /status without an exception means aura_hostd is reachable
      // (it answers /status unauthenticated); `authenticated` reflects whether
      // pairing has completed.
      final status = await _hostdClient.status();
      state = state.copyWith(
        isConnected: status['authenticated'] == true,
        backendUrl: status['backend_url'] as String?,
      );
      if (state.isConnected) {
        final roots = await _hostdClient.roots();
        state = state.copyWith(grantedRoots: roots.map((r) => r['path'] as String).toList());
      }
    } catch (_) {
      state = state.copyWith(isConnected: false, grantedRoots: []);
    }
  }

  @override
  void dispose() {
    _eventSub?.cancel();
    super.dispose();
  }
}

/// Approval state notifier.
class ApprovalNotifier extends StateNotifier<ApprovalState> {
  ApprovalNotifier(this._eventBus) : super(const ApprovalState()) {
    _subscribeToEvents();
  }

  final IdeEventBus _eventBus;
  StreamSubscription? _eventSub;

  void _subscribeToEvents() {
    _eventSub = _eventBus.stream
        .whereType<ApprovalRequested>()
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

  void respond(String approvalId, bool approved) {
    state = state.copyWith(
      pendingApprovals: state.pendingApprovals.where((a) => a.id != approvalId).toList(),
    );
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
  final hostdClient = ref.watch(hostdControlClientProvider);
  return WorkspaceNotifier(db, eventBus, hostdClient);
});

/// Provider for editor notifier.
final editorNotifierProvider = StateNotifierProvider<EditorNotifier, EditorState>((ref) {
  final db = ref.watch(databaseProvider);
  final eventBus = ref.watch(ideEventBusProvider);
  return EditorNotifier(db, eventBus);
});

/// Provider for layout notifier.
final layoutNotifierProvider = StateNotifierProvider<LayoutNotifier, LayoutState>((ref) {
  final eventBus = ref.watch(ideEventBusProvider);
  return LayoutNotifier(eventBus);
});

/// Provider for host notifier.
final hostNotifierProvider = StateNotifierProvider<HostNotifier, HostState>((ref) {
  final eventBus = ref.watch(ideEventBusProvider);
  final hostdClient = ref.watch(hostdControlClientProvider);
  return HostNotifier(eventBus, hostdClient);
});

/// Provider for approval notifier.
final approvalNotifierProvider = StateNotifierProvider<ApprovalNotifier, ApprovalState>((ref) {
  final eventBus = ref.watch(ideEventBusProvider);
  return ApprovalNotifier(eventBus);
});

/// Provider for notification notifier.
final notificationNotifierProvider = StateNotifierProvider<NotificationNotifier, NotificationState>((ref) {
  return NotificationNotifier();
});