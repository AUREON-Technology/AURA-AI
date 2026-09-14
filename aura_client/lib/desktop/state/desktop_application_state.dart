/// Desktop IDE state model.
///
/// Immutable state classes for each domain, owned by Riverpod notifiers.
library;

import 'package:collection/collection.dart';
import 'package:flutter/foundation.dart';

import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/desktop/editor/editor_tab.dart';

/// Workspace state.
@immutable
class WorkspaceState {
  const WorkspaceState({
    this.workspaces = const [],
    this.activeWorkspaceId,
    this.hostdRoots = const [],
  });

  final List<DesktopWorkspace> workspaces;
  final String? activeWorkspaceId;
  final List<HostdRoot> hostdRoots;

  DesktopWorkspace? get activeWorkspace {
    if (activeWorkspaceId == null) return null;
    return workspaces.where((w) => w.id == activeWorkspaceId).firstOrNull;
  }

  WorkspaceState copyWith({
    List<DesktopWorkspace>? workspaces,
    String? activeWorkspaceId,
    List<HostdRoot>? hostdRoots,
  }) {
    return WorkspaceState(
      workspaces: workspaces ?? this.workspaces,
      activeWorkspaceId: activeWorkspaceId ?? this.activeWorkspaceId,
      hostdRoots: hostdRoots ?? this.hostdRoots,
    );
  }
}

// Note: no separate hand-written workspace model here — Drift's own
// generated `DesktopWorkspace` row class (from the `database.dart` import
// above) already has exactly this shape (id/rootPath/displayName/
// lastOpenedAt), so [WorkspaceState.workspaces] uses it directly.

/// Hostd root model for state.
@immutable
class HostdRoot {
  const HostdRoot({
    required this.path,
    required this.trusted,
  });

  final String path;
  final bool trusted;
}

/// Editor state.
@immutable
class EditorState {
  const EditorState({
    this.tabs = const [],
    this.activeTabId,
    this.workspaceId,
  });

  final List<EditorTab> tabs;
  final String? activeTabId;
  final String? workspaceId;

  EditorTab? get activeTab {
    if (activeTabId == null) return null;
    return tabs.where((t) => t.id == activeTabId).firstOrNull;
  }

  EditorState copyWith({
    List<EditorTab>? tabs,
    String? activeTabId,
    String? workspaceId,
  }) {
    return EditorState(
      tabs: tabs ?? this.tabs,
      activeTabId: activeTabId ?? this.activeTabId,
      workspaceId: workspaceId ?? this.workspaceId,
    );
  }
}

/// Terminal state (D4).
@immutable
class TerminalState {
  const TerminalState({
    this.sessions = const [],
    this.activeSessionId,
  });

  final List<TerminalSessionState> sessions;

  /// Which session the bottom panel's Terminal tab currently renders -- D4's
  /// first pass shows one live session at a time (see D4 plan section B4);
  /// a tab strip for concurrent sessions is a tracked follow-up.
  final String? activeSessionId;

  TerminalSessionState? get activeSession {
    if (activeSessionId == null) return null;
    for (final s in sessions) {
      if (s.id == activeSessionId) return s;
    }
    return null;
  }

  TerminalState copyWith({
    List<TerminalSessionState>? sessions,
    String? activeSessionId,
    bool clearActiveSessionId = false,
  }) {
    return TerminalState(
      sessions: sessions ?? this.sessions,
      activeSessionId: clearActiveSessionId ? null : (activeSessionId ?? this.activeSessionId),
    );
  }
}

@immutable
class TerminalSessionState {
  const TerminalSessionState({
    required this.id,
    required this.workspaceId,
    this.command,
    this.output = '',
    this.isRunning = false,
    this.exitCode,
    this.cols = 80,
    this.rows = 24,
  });

  final String id;
  final String workspaceId;
  final String? command;

  /// Scrollback buffer -- capped by the caller (mirrors `task_handler.dart`'s
  /// `_maxOutputCapChars` discipline on the daemon side) since this grows on
  /// every output chunk for the life of a session.
  final String output;
  final bool isRunning;
  final int? exitCode;
  final int cols;
  final int rows;

  TerminalSessionState copyWith({
    String? command,
    String? output,
    bool? isRunning,
    int? exitCode,
    bool clearExitCode = false,
    int? cols,
    int? rows,
  }) {
    return TerminalSessionState(
      id: id,
      workspaceId: workspaceId,
      command: command ?? this.command,
      output: output ?? this.output,
      isRunning: isRunning ?? this.isRunning,
      exitCode: clearExitCode ? null : (exitCode ?? this.exitCode),
      cols: cols ?? this.cols,
      rows: rows ?? this.rows,
    );
  }
}

/// Git state (D5).
@immutable
class GitState {
  const GitState({
    this.workspaceId,
    this.branch,
    this.upstream,
    this.ahead = 0,
    this.behind = 0,
    this.files = const [],
    this.diffText,
    this.diffScope,
    this.diffTruncated = false,
    this.commits = const [],
    this.busy = false,
    this.error,
  });

  final String? workspaceId;

  /// `null` covers both "no repo loaded yet" and a real detached-HEAD state -- the daemon's own
  /// `git_service.dart` reports both the same way, so the UI must not assume non-null.
  final String? branch;
  final String? upstream;
  final int ahead;
  final int behind;
  final List<GitFileChange> files;

  /// The most recently loaded diff, if any, and which [GitDiffScope] it is for -- `null` scope
  /// means nothing has been loaded yet.
  final String? diffText;
  final GitDiffScope? diffScope;
  final bool diffTruncated;

  final List<GitCommit> commits;

  /// True while any write operation (stage/unstage/commit/checkout) is in flight -- checked by
  /// [GitNotifier]'s write methods before starting (a no-op if already busy) and used by the Git
  /// panel to disable its buttons, so a double-click can't fire two concurrent commits.
  final bool busy;

  final String? error;

  GitState copyWith({
    String? workspaceId,
    String? branch,
    bool clearBranch = false,
    String? upstream,
    int? ahead,
    int? behind,
    List<GitFileChange>? files,
    String? diffText,
    GitDiffScope? diffScope,
    bool? diffTruncated,
    List<GitCommit>? commits,
    bool? busy,
    String? error,
    bool clearError = false,
  }) {
    return GitState(
      workspaceId: workspaceId ?? this.workspaceId,
      branch: clearBranch ? null : (branch ?? this.branch),
      upstream: upstream ?? this.upstream,
      ahead: ahead ?? this.ahead,
      behind: behind ?? this.behind,
      files: files ?? this.files,
      diffText: diffText ?? this.diffText,
      diffScope: diffScope ?? this.diffScope,
      diffTruncated: diffTruncated ?? this.diffTruncated,
      commits: commits ?? this.commits,
      busy: busy ?? this.busy,
      error: clearError ? null : (error ?? this.error),
    );
  }
}

enum GitDiffScope { workingTree, staged }

@immutable
class GitFileChange {
  const GitFileChange({
    required this.path,
    required this.indexStatus,
    required this.worktreeStatus,
    required this.staged,
    this.renamedFrom,
  });

  final String path;
  final String indexStatus;
  final String worktreeStatus;
  final bool staged;
  final String? renamedFrom;

  static GitFileChange fromJson(Map<String, dynamic> json) => GitFileChange(
        path: json['path'] as String,
        indexStatus: json['index_status'] as String,
        worktreeStatus: json['worktree_status'] as String,
        staged: json['staged'] as bool? ?? false,
        renamedFrom: json['renamed_from'] as String?,
      );
}

@immutable
class GitCommit {
  const GitCommit({required this.sha, required this.author, required this.date, required this.subject});

  final String sha;
  final String author;
  final String date;
  final String subject;

  static GitCommit fromJson(Map<String, dynamic> json) => GitCommit(
        sha: json['sha'] as String,
        author: json['author'] as String,
        date: json['date'] as String,
        subject: json['subject'] as String,
      );
}

/// Agent state (placeholder for D6).
@immutable
class AgentState {
  const AgentState({
    this.activeTaskId,
    this.workspaceId,
  });

  final String? activeTaskId;
  final String? workspaceId;

  AgentState copyWith({String? activeTaskId, String? workspaceId}) {
    return AgentState(
      activeTaskId: activeTaskId ?? this.activeTaskId,
      workspaceId: workspaceId ?? this.workspaceId,
    );
  }
}

/// Task state (placeholder for D8).
@immutable
class TaskState {
  const TaskState({
    this.tasks = const [],
  });

  final List<TaskStateItem> tasks;

  TaskState copyWith({List<TaskStateItem>? tasks}) {
    return TaskState(tasks: tasks ?? this.tasks);
  }
}

@immutable
class TaskStateItem {
  const TaskStateItem({
    required this.id,
    required this.title,
    required this.status,
    this.workspaceId,
  });

  final String id;
  final String title;
  final String status;
  final String? workspaceId;
}

/// Host state.
@immutable
class HostState {
  const HostState({
    this.isConnected = false,
    this.grantedRoots = const [],
    this.backendUrl,
  });

  final bool isConnected;
  final List<String> grantedRoots;
  final String? backendUrl;

  HostState copyWith({
    bool? isConnected,
    List<String>? grantedRoots,
    String? backendUrl,
  }) {
    return HostState(
      isConnected: isConnected ?? this.isConnected,
      grantedRoots: grantedRoots ?? this.grantedRoots,
      backendUrl: backendUrl ?? this.backendUrl,
    );
  }
}

/// Which panel occupies the primary sidebar (D5) -- previously `DesktopShell` hardcoded
/// `ExplorerPanel` there unconditionally, since Explorer was the only panel that had ever
/// existed.
enum PrimarySidebarPanel { explorer, sourceControl }

/// Layout state.
@immutable
class LayoutState {
  const LayoutState({
    this.primarySidebarWidth = 280.0,
    this.secondarySidebarWidth = 320.0,
    this.bottomPanelHeight = 200.0,
    this.showPrimarySidebar = true,
    this.showSecondarySidebar = false,
    this.showBottomPanel = false,
    this.primarySidebarPanel = PrimarySidebarPanel.explorer,
    this.workspaceId,
  });

  final double primarySidebarWidth;
  final double secondarySidebarWidth;
  final double bottomPanelHeight;
  final bool showPrimarySidebar;
  final bool showSecondarySidebar;
  final bool showBottomPanel;
  final PrimarySidebarPanel primarySidebarPanel;
  final String? workspaceId;

  LayoutState copyWith({
    double? primarySidebarWidth,
    double? secondarySidebarWidth,
    double? bottomPanelHeight,
    bool? showPrimarySidebar,
    bool? showSecondarySidebar,
    bool? showBottomPanel,
    PrimarySidebarPanel? primarySidebarPanel,
    String? workspaceId,
  }) {
    return LayoutState(
      primarySidebarWidth: primarySidebarWidth ?? this.primarySidebarWidth,
      secondarySidebarWidth: secondarySidebarWidth ?? this.secondarySidebarWidth,
      bottomPanelHeight: bottomPanelHeight ?? this.bottomPanelHeight,
      showPrimarySidebar: showPrimarySidebar ?? this.showPrimarySidebar,
      showSecondarySidebar: showSecondarySidebar ?? this.showSecondarySidebar,
      showBottomPanel: showBottomPanel ?? this.showBottomPanel,
      primarySidebarPanel: primarySidebarPanel ?? this.primarySidebarPanel,
      workspaceId: workspaceId ?? this.workspaceId,
    );
  }
}

/// Approval state.
@immutable
class ApprovalState {
  const ApprovalState({
    this.pendingApprovals = const [],
  });

  final List<PendingApproval> pendingApprovals;

  ApprovalState copyWith({List<PendingApproval>? pendingApprovals}) {
    return ApprovalState(pendingApprovals: pendingApprovals ?? this.pendingApprovals);
  }
}

@immutable
class PendingApproval {
  const PendingApproval({
    required this.id,
    required this.tool,
    required this.arguments,
    required this.workspaceId,
    required this.requestedAt,
  });

  final String id;
  final String tool;
  final Map<String, dynamic> arguments;
  final String workspaceId;
  final DateTime requestedAt;
}

/// Notification state.
@immutable
class NotificationState {
  const NotificationState({
    this.notifications = const [],
  });

  final List<NotificationItem> notifications;

  NotificationState copyWith({List<NotificationItem>? notifications}) {
    return NotificationState(notifications: notifications ?? this.notifications);
  }
}

@immutable
class NotificationItem {
  const NotificationItem({
    required this.id,
    required this.title,
    required this.message,
    this.type = NotificationType.info,
    this.timestamp,
    this.actionLabel,
    this.onAction,
  });

  final String id;
  final String title;
  final String message;
  final NotificationType type;
  final DateTime? timestamp;
  final String? actionLabel;
  final VoidCallback? onAction;
}

enum NotificationType { info, success, warning, error }

/// Combined desktop application state.
@immutable
class DesktopApplicationState {
  const DesktopApplicationState({
    this.workspace = const WorkspaceState(),
    this.editor = const EditorState(),
    this.terminal = const TerminalState(),
    this.git = const GitState(),
    this.agent = const AgentState(),
    this.task = const TaskState(),
    this.host = const HostState(),
    this.layout = const LayoutState(),
    this.approval = const ApprovalState(),
    this.notification = const NotificationState(),
  });

  final WorkspaceState workspace;
  final EditorState editor;
  final TerminalState terminal;
  final GitState git;
  final AgentState agent;
  final TaskState task;
  final HostState host;
  final LayoutState layout;
  final ApprovalState approval;
  final NotificationState notification;

  DesktopApplicationState copyWith({
    WorkspaceState? workspace,
    EditorState? editor,
    TerminalState? terminal,
    GitState? git,
    AgentState? agent,
    TaskState? task,
    HostState? host,
    LayoutState? layout,
    ApprovalState? approval,
    NotificationState? notification,
  }) {
    return DesktopApplicationState(
      workspace: workspace ?? this.workspace,
      editor: editor ?? this.editor,
      terminal: terminal ?? this.terminal,
      git: git ?? this.git,
      agent: agent ?? this.agent,
      task: task ?? this.task,
      host: host ?? this.host,
      layout: layout ?? this.layout,
      approval: approval ?? this.approval,
      notification: notification ?? this.notification,
    );
  }
}