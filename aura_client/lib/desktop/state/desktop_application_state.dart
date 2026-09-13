/// Desktop IDE state model.
///
/// Immutable state classes for each domain, owned by Riverpod notifiers.
library;

import 'package:collection/collection.dart';
import 'package:flutter/foundation.dart';

import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/desktop/editor/editor_abstraction.dart';

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

/// Lightweight workspace model for state.
@immutable
class DesktopWorkspace {
  const DesktopWorkspace({
    required this.id,
    required this.rootPath,
    required this.displayName,
    required this.lastOpenedAt,
  });

  final String id;
  final String rootPath;
  final String displayName;
  final DateTime lastOpenedAt;

  factory DesktopWorkspace.fromDb(DesktopWorkspacesData data) => DesktopWorkspace(
    id: data.id,
    rootPath: data.rootPath,
    displayName: data.displayName,
    lastOpenedAt: data.lastOpenedAt,
  );
}

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

/// Terminal state (placeholder for D4).
@immutable
class TerminalState {
  const TerminalState({
    this.sessions = const [],
  });

  final List<TerminalSessionState> sessions;

  TerminalState copyWith({List<TerminalSessionState>? sessions}) {
    return TerminalState(sessions: sessions ?? this.sessions);
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
  });

  final String id;
  final String workspaceId;
  final String? command;
  final String output;
  final bool isRunning;
  final int? exitCode;
}

/// Git state (placeholder for D5).
@immutable
class GitState {
  const GitState({
    this.branch,
    this.status,
    this.workspaceId,
  });

  final String? branch;
  final String? status;
  final String? workspaceId;

  GitState copyWith({String? branch, String? status, String? workspaceId}) {
    return GitState(
      branch: branch ?? this.branch,
      status: status ?? this.status,
      workspaceId: workspaceId ?? this.workspaceId,
    );
  }
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
    this.workspaceId,
  });

  final double primarySidebarWidth;
  final double secondarySidebarWidth;
  final double bottomPanelHeight;
  final bool showPrimarySidebar;
  final bool showSecondarySidebar;
  final bool showBottomPanel;
  final String? workspaceId;

  LayoutState copyWith({
    double? primarySidebarWidth,
    double? secondarySidebarWidth,
    double? bottomPanelHeight,
    bool? showPrimarySidebar,
    bool? showSecondarySidebar,
    bool? showBottomPanel,
    String? workspaceId,
  }) {
    return LayoutState(
      primarySidebarWidth: primarySidebarWidth ?? this.primarySidebarWidth,
      secondarySidebarWidth: secondarySidebarWidth ?? this.secondarySidebarWidth,
      bottomPanelHeight: bottomPanelHeight ?? this.bottomPanelHeight,
      showPrimarySidebar: showPrimarySidebar ?? this.showPrimarySidebar,
      showSecondarySidebar: showSecondarySidebar ?? this.showSecondarySidebar,
      showBottomPanel: showBottomPanel ?? this.showBottomPanel,
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