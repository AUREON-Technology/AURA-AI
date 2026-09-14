/// Output log for Desktop IDE's bottom-panel "Output" tab (D7 Part A.1).
///
/// Fed entirely by the existing [IdeEventBus] -- no new backend work needed, since every event
/// worth showing here (agent activity, git changes, host connect/disconnect, task status,
/// diagnostics once D7 Part A.2 lands) is already published on it. Buckets events into channels
/// the way VS Code's Output panel does (a channel dropdown over one shared log), rather than one
/// undifferentiated stream.
library;

import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/desktop/events/ide_event.dart';
import 'package:aura_client/desktop/events/ide_event_bus.dart';

enum OutputChannel { agent, git, host, tasks, diagnostics }

extension OutputChannelLabel on OutputChannel {
  String get label => switch (this) {
        OutputChannel.agent => 'Agent',
        OutputChannel.git => 'Git',
        OutputChannel.host => 'Host',
        OutputChannel.tasks => 'Tasks',
        OutputChannel.diagnostics => 'Diagnostics',
      };
}

class OutputLogEntry {
  const OutputLogEntry({
    required this.timestamp,
    required this.channel,
    required this.message,
  });

  final DateTime timestamp;
  final OutputChannel channel;
  final String message;
}

/// Buffers mapped [IdeEvent]s into [OutputLogEntry]s, capped the same way
/// `HostdLogNotifier` caps its own buffer (`hostd_providers.dart`'s `_maxLines = 500`) -- a
/// long-running workspace must not grow this unboundedly.
class OutputLogNotifier extends Notifier<List<OutputLogEntry>> {
  static const _maxLines = 500;
  StreamSubscription<IdeEvent>? _subscription;

  @override
  List<OutputLogEntry> build() {
    _subscription?.cancel();
    _subscription = ref.watch(ideEventBusProvider).stream.listen(_handleEvent);
    ref.onDispose(() => _subscription?.cancel());
    return const [];
  }

  void _handleEvent(IdeEvent event) {
    final entry = _mapEvent(event);
    if (entry == null) return;
    final next = [...state, entry];
    state = next.length > _maxLines ? next.sublist(next.length - _maxLines) : next;
  }

  OutputLogEntry? _mapEvent(IdeEvent event) {
    final now = DateTime.now();
    OutputLogEntry entry(OutputChannel channel, String message) =>
        OutputLogEntry(timestamp: now, channel: channel, message: message);

    return switch (event) {
      AgentStarted() => entry(OutputChannel.agent, 'Agent "${event.agentId}" started (task ${event.taskId})'),
      AgentToolCalled() => entry(OutputChannel.agent, 'Tool call: ${event.tool}(${event.arguments})'),
      AgentCompleted() => entry(
          OutputChannel.agent,
          event.success
              ? 'Task ${event.taskId} completed'
              : 'Task ${event.taskId} failed${event.error != null ? ': ${event.error}' : ''}',
        ),
      ApprovalRequested() => entry(OutputChannel.agent, 'Approval requested for ${event.tool}'),
      ApprovalResponded() =>
        entry(OutputChannel.agent, 'Approval ${event.approvalId} ${event.approved ? 'approved' : 'rejected'}'),
      GitChanged() => entry(
          OutputChannel.git,
          [
            'Git changed',
            if (event.branch != null) 'branch ${event.branch}',
            if (event.status != null) event.status!,
          ].join(' -- '),
        ),
      HostConnected() => entry(OutputChannel.host, 'Host connected'),
      HostDisconnected() => entry(OutputChannel.host, 'Host disconnected'),
      HostRootGranted() => entry(OutputChannel.host, 'Root granted: ${event.rootPath}'),
      HostRootRevoked() => entry(OutputChannel.host, 'Root revoked: ${event.rootPath}'),
      TaskUpdated() => entry(OutputChannel.tasks, 'Task ${event.taskId}: ${event.status}'),
      DiagnosticAdded() =>
        entry(OutputChannel.diagnostics, '${event.diagnostics.length} diagnostic(s) in ${event.path}'),
      DiagnosticCleared() => entry(OutputChannel.diagnostics, 'Diagnostics cleared for ${event.path}'),
      _ => null,
    };
  }

  void clear() => state = const [];
}

final outputLogProvider = NotifierProvider<OutputLogNotifier, List<OutputLogEntry>>(OutputLogNotifier.new);
