/// IDE event model for AURA Desktop.
///
/// Unified event stream enabling reactive UI without polling.
/// Events flow through [IdeEventBus] and are consumed by Riverpod notifiers.
library;

import 'package:flutter/foundation.dart';

/// Base class for all IDE events.
sealed class IdeEvent {
  const IdeEvent();
  DateTime get timestamp => DateTime.now();
}

/// Workspace events
class WorkspaceOpened extends IdeEvent {
  const WorkspaceOpened(this.workspaceId);
  final String workspaceId;
}

class WorkspaceClosed extends IdeEvent {
  const WorkspaceClosed(this.workspaceId);
  final String workspaceId;
}

class WorkspaceCreated extends IdeEvent {
  const WorkspaceCreated(this.workspaceId, this.rootPath);
  final String workspaceId;
  final String rootPath;
}

class WorkspaceRemoved extends IdeEvent {
  const WorkspaceRemoved(this.workspaceId);
  final String workspaceId;
}

/// File events
class FileChanged extends IdeEvent {
  const FileChanged(this.path, this.workspaceId, {this.content});
  final String path;
  final String workspaceId;
  final String? content;
}

class FileSaved extends IdeEvent {
  const FileSaved(this.path, this.workspaceId);
  final String path;
  final String workspaceId;
}

class FileCreated extends IdeEvent {
  const FileCreated(this.path, this.workspaceId);
  final String path;
  final String workspaceId;
}

class FileDeleted extends IdeEvent {
  const FileDeleted(this.path, this.workspaceId);
  final String path;
  final String workspaceId;
}

class FileRenamed extends IdeEvent {
  const FileRenamed(this.oldPath, this.newPath, this.workspaceId);
  final String oldPath;
  final String newPath;
  final String workspaceId;
}

/// Editor events
class TabOpened extends IdeEvent {
  const TabOpened(this.tabId, this.path, this.workspaceId);
  final String tabId;
  final String path;
  final String workspaceId;
}

class TabClosed extends IdeEvent {
  const TabClosed(this.tabId, this.workspaceId);
  final String tabId;
  final String workspaceId;
}

class TabActivated extends IdeEvent {
  const TabActivated(this.tabId, this.workspaceId);
  final String tabId;
  final String workspaceId;
}

class TabDirtyChanged extends IdeEvent {
  const TabDirtyChanged(this.tabId, this.isDirty, this.workspaceId);
  final String tabId;
  final bool isDirty;
  final String workspaceId;
}

/// Terminal events
class TerminalStarted extends IdeEvent {
  const TerminalStarted(this.sessionId, this.workspaceId, {this.command});
  final String sessionId;
  final String workspaceId;
  final String? command;
}

class TerminalOutput extends IdeEvent {
  const TerminalOutput(this.sessionId, this.data);
  final String sessionId;
  final String data;
}

class TerminalExited extends IdeEvent {
  const TerminalExited(this.sessionId, this.exitCode);
  final String sessionId;
  final int exitCode;
}

/// Agent events
class AgentStarted extends IdeEvent {
  const AgentStarted(this.taskId, this.agentId, this.workspaceId);
  final String taskId;
  final String agentId;
  final String workspaceId;
}

class AgentToolCalled extends IdeEvent {
  const AgentToolCalled(this.taskId, this.tool, this.arguments);
  final String taskId;
  final String tool;
  final Map<String, dynamic> arguments;
}

class AgentCompleted extends IdeEvent {
  const AgentCompleted(this.taskId, this.success, {this.error});
  final String taskId;
  final bool success;
  final String? error;
}

/// Approval events
class ApprovalRequested extends IdeEvent {
  const ApprovalRequested(this.approvalId, this.tool, this.arguments, this.workspaceId);
  final String approvalId;
  final String tool;
  final Map<String, dynamic> arguments;
  final String workspaceId;
}

class ApprovalResponded extends IdeEvent {
  const ApprovalResponded(this.approvalId, this.approved);
  final String approvalId;
  final bool approved;
}

/// Git events
class GitChanged extends IdeEvent {
  const GitChanged(this.workspaceId, {this.branch, this.status});
  final String workspaceId;
  final String? branch;
  final String? status;
}

/// Diagnostic events
class DiagnosticAdded extends IdeEvent {
  const DiagnosticAdded(this.path, this.diagnostics, this.workspaceId);
  final String path;
  final List<Diagnostic> diagnostics;
  final String workspaceId;
}

class DiagnosticCleared extends IdeEvent {
  const DiagnosticCleared(this.path, this.workspaceId);
  final String path;
  final String workspaceId;
}

/// Task events
class TaskUpdated extends IdeEvent {
  const TaskUpdated(this.taskId, this.status, this.workspaceId);
  final String taskId;
  final String status;
  final String workspaceId;
}

/// Layout events
class LayoutChanged extends IdeEvent {
  const LayoutChanged(this.workspaceId, this.panelSizes);
  final String workspaceId;
  final Map<String, double> panelSizes;
}

class PanelVisibilityChanged extends IdeEvent {
  const PanelVisibilityChanged(this.workspaceId, this.panelId, this.visible);
  final String workspaceId;
  final String panelId;
  final bool visible;
}

/// UI-only events (not persisted, not domain state) for one-shot shell actions.
class ShowQuickOpenRequested extends IdeEvent {
  const ShowQuickOpenRequested();
}

class FocusTerminalRequested extends IdeEvent {
  const FocusTerminalRequested();
}

/// D6 Part D: the active editor should request one inline completion at the cursor.
/// Carries no payload -- the listener (the currently-mounted editor content widget) reads its
/// own controller's current cursor/text, since that's the only place with a live reference.
class RequestInlineCompletionRequested extends IdeEvent {
  const RequestInlineCompletionRequested();
}

/// Host events
class HostConnected extends IdeEvent {
  const HostConnected(this.workspaceId);
  final String workspaceId;
}

class HostDisconnected extends IdeEvent {
  const HostDisconnected(this.workspaceId);
  final String workspaceId;
}

class HostRootGranted extends IdeEvent {
  const HostRootGranted(this.workspaceId, this.rootPath);
  final String workspaceId;
  final String rootPath;
}

class HostRootRevoked extends IdeEvent {
  const HostRootRevoked(this.workspaceId, this.rootPath);
  final String workspaceId;
  final String rootPath;
}

/// Diagnostic information.
@immutable
class Diagnostic {
  const Diagnostic({
    required this.range,
    required this.severity,
    required this.message,
    this.source,
    this.code,
  });

  final EditorRange range;
  final DiagnosticSeverity severity;
  final String message;
  final String? source;
  final String? code;
}

/// Editor range (start/end positions).
@immutable
class EditorRange {
  const EditorRange({
    required this.startLine,
    required this.startColumn,
    required this.endLine,
    required this.endColumn,
  });

  final int startLine;
  final int startColumn;
  final int endLine;
  final int endColumn;
}

/// Diagnostic severity levels.
enum DiagnosticSeverity {
  error,
  warning,
  info,
  hint,
}