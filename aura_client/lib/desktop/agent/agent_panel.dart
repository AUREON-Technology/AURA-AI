/// Agent panel for Desktop IDE.
///
/// Embeds the real chat engine (chatViewModelProvider/chatRepositoryProvider
/// — the same one Copilot uses) into the IDE's secondary sidebar, forced to
/// the Engineer persona, so the AI is reachable without leaving the IDE.
/// Replaces the static "Agent Panel (D6)" placeholder that was here before.
library;

import 'package:aura_client/features/engineering/presentation/hostd_pairing_flow.dart';
import 'package:aura_client/core/hostd/hostd_providers.dart';
import 'package:aura_client/desktop/host/host_access.dart';
import 'package:collection/collection.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/ai/engineer/engineer_consent_repository.dart';
import 'package:aura_client/core/ai/vision/chat_images.dart';
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/network/aura_failure.dart';
import 'package:aura_client/core/network/aura_failure_view.dart';
import 'package:aura_client/core/workspaces/active_workspace.dart';
import 'package:aura_client/features/chat/data/agent_config_repository.dart';
import 'package:aura_client/features/chat/data/agents_repository.dart';
import 'package:aura_client/features/chat/data/chat_repository.dart';
import 'package:aura_client/features/chat/presentation/chat_view_model.dart';
import 'package:aura_client/features/chat/presentation/widgets/chat_composer.dart';
import 'package:aura_client/features/chat/presentation/widgets/message_bubble.dart';
import 'package:aura_client/features/chat/presentation/widgets/pending_actions_card.dart';
import 'package:aura_client/features/engineering/presentation/engineer_consent_sheet.dart';
import 'package:aura_client/features/engineering/presentation/git_init_prompt.dart';
import 'package:aura_client/desktop/state/state_notifiers.dart';
import 'package:aura_client/l10n/l10n.dart';

const _engineerAgentId = kEngineerAgentId;

class AgentPanel extends ConsumerStatefulWidget {
  const AgentPanel({super.key});

  @override
  ConsumerState<AgentPanel> createState() => _AgentPanelState();
}

/// Whether [agents] shows this account may use Engineer. Absent from the list means the backend
/// does not offer it at all; an older backend without `usable` defaults to true (AgentInfo).
bool engineerUsable(List<AgentInfo> agents) =>
    agents.firstWhereOrNull((a) => a.id == _engineerAgentId)?.usable ?? false;

class _AgentPanelState extends ConsumerState<AgentPanel> {
  final _controller = TextEditingController();
  final _scrollController = ScrollController();

  /// The app-wide persona before this panel switched it to Engineer, restored on close. Null
  /// until the switch happens (and if it never does, because Engineer is locked).
  String? _previousAgentId;
  late final ActiveAgent _activeAgent;

  @override
  void initState() {
    super.initState();
    _activeAgent = ref.read(activeAgentIdProvider.notifier);
    // This panel only ever talks to Engineer, so it borrows the (app-global) active persona --
    // the same thing picking "Engineer" in Copilot's picker does -- but only once the backend
    // says this account may use it. Selecting a locked persona does not fail: the backend
    // silently answers as the orchestrator (AgentRegistry.resolve), which the persona picker
    // already refuses to do (audit 2026-11-16, F7). If the list cannot be fetched, the switch
    // still happens, as before: the backend's own capability gate is the real enforcement.
    ref.listenManual<AsyncValue<List<AgentInfo>>>(
      agentsProvider,
      (_, next) {
        final claim = switch (next) {
          AsyncData(:final value) => engineerUsable(value),
          AsyncError() => true,
          _ => false,
        };
        if (claim) _claimEngineer();
      },
      fireImmediately: true,
    );
  }

  void _claimEngineer() {
    if (_previousAgentId != null) return;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted || _previousAgentId != null) return;
      _previousAgentId = ref.read(activeAgentIdProvider);
      _activeAgent.setAgent(_engineerAgentId);
    });
  }

  @override
  void dispose() {
    // Hand the app-wide persona back, so closing the panel does not leave ordinary chat sending
    // as Engineer. Deferred: providers must not be modified while the tree is being finalized.
    final previous = _previousAgentId;
    if (previous != null && previous != _engineerAgentId) {
      final activeAgent = _activeAgent;
      Future.microtask(() => activeAgent.restoreIfStill(_engineerAgentId, previous));
    }
    _controller.dispose();
    _scrollController.dispose();
    super.dispose();
  }

  void _scrollToBottom() {
    if (!_scrollController.hasClients) return;
    _scrollController.animateTo(
      _scrollController.position.maxScrollExtent,
      duration: const Duration(milliseconds: 250),
      curve: Curves.easeOut,
    );
  }

  Future<void> _send() async {
    final text = _controller.text;
    final chat = ref.read(chatViewModelProvider);
    final isStreaming = ref.read(streamingMessageIdProvider) != null;
    final hasImages = ref.read(pendingChatImagesProvider).isNotEmpty;
    if (!chat.canSend(text, isStreaming: isStreaming, hasImages: hasImages)) return;
    // D-03: everything sent from here is an Engineer turn, so it waits for consent -- before
    // the composer is cleared, so "Not now" leaves the message there to send later.
    if (!await ensureEngineerConsent(context, ref) || !mounted) return;

    _controller.clear();
    // FSA-006: the workspace this panel sits beside is where the Engineer's commands run.
    final workspaceRoot = ref.read(workspaceNotifierProvider).activeWorkspace?.rootPath;
    final sent = await ref
        .read(chatViewModelProvider.notifier)
        // WP4 (D-09): each Engineer chat works in its own git worktree (branch `aura/task-…`),
        // reviewed and merged from the Control Center.
        .sendMessage(text, workspaceRoot: workspaceRoot, desktopTaskIsolation: true);
    if (!sent || !mounted) return;
    Future.delayed(const Duration(milliseconds: 100), () {
      if (mounted) _scrollToBottom();
    });
  }

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final conversationId = ref.watch(activeConversationIdProvider);
    final messagesAsync = ref.watch(chatMessagesStreamProvider(conversationId));
    // G-14a: a confirmation still waiting from before a restart comes back with its card.
    ref.watch(pendingConfirmationRehydrationProvider(conversationId));
    final workspaceRoot = ref.watch(workspaceNotifierProvider).activeWorkspace?.rootPath;
    final (personaLabel, personaIcon) = personaDisplay(_engineerAgentId);
    final locked = switch (ref.watch(agentsProvider)) {
      AsyncData(:final value) => !engineerUsable(value),
      _ => false,
    };

    ref.listen<AsyncValue<List<Message>>>(
      chatMessagesStreamProvider(conversationId),
      (previous, next) {
        final messages = next.asData?.value;
        if (messages == null || messages.isEmpty || !mounted) return;
        final previousLast = previous?.asData?.value.lastOrNull;
        if (previousLast != null && previousLast.id == messages.last.id) return;
        WidgetsBinding.instance.addPostFrameCallback((_) {
          if (mounted) _scrollToBottom();
        });
      },
    );

    return EngineerConsentPromptListener(
      child: Container(
        color: palette.surface,
        child: Column(
          children: [
            Container(
              padding: const EdgeInsets.symmetric(horizontal: AuraSpacing.md, vertical: AuraSpacing.sm),
              decoration: BoxDecoration(border: Border(bottom: BorderSide(color: palette.border))),
              child: Row(
                children: [
                  Icon(personaIcon, size: 16, color: palette.brandOnSurfaceText),
                  const SizedBox(width: AuraSpacing.xs),
                  Expanded(
                    child: Text(
                      personaLabel,
                      style: TextStyle(fontWeight: FontWeight.w600, color: palette.textPrimary, fontSize: 13),
                    ),
                  ),
                  _TaskSwitcher(activeSessionId: conversationId),
                ],
              ),
            ),
            if (workspaceRoot != null)
              Container(
                width: double.infinity,
                padding: const EdgeInsets.symmetric(horizontal: AuraSpacing.md, vertical: 6),
                color: palette.surfaceVariant,
                child: Text(
                  context.l10n.agentWorkspaceLabel(workspaceRoot),
                  style: TextStyle(fontSize: 10.5, color: palette.textSecondary, fontFamily: 'Courier'),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            // D-13: isolation needs git; a bound folder that is not a repository says so here.
            if (workspaceRoot != null) WorkspaceGitInitBanner(root: workspaceRoot),
            if (locked)
              Expanded(
                child: Center(
                  child: SingleChildScrollView(
                    child: _EngineerLocked(elsewhere: ref.watch(engineerWorkspacesProvider)),
                  ),
                ),
              )
            else ...[
            Expanded(
              child: messagesAsync.when(
                data: (messages) {
                  if (messages.isEmpty) {
                    // Was a one-line grey Text, which reads as a screen that
                    // failed to load rather than one waiting for you to start.
                    return Center(
                      child: SingleChildScrollView(
                        child: AuraEmptyState(
                          icon: Icons.auto_awesome_outlined,
                          title: context.l10n.agentEmptyTitle,
                          message: context.l10n.agentEmptyMessage,
                        ),
                      ),
                    );
                  }
                  return ListView.builder(
                    controller: _scrollController,
                    padding: const EdgeInsets.symmetric(horizontal: AuraSpacing.sm, vertical: AuraSpacing.md),
                    itemCount: messages.length,
                    itemBuilder: (context, index) {
                      final msg = messages[index];
                      String? userPrompt;
                      if (msg.role != 'user') {
                        for (var i = index - 1; i >= 0; i--) {
                          if (messages[i].role == 'user') {
                            userPrompt = messages[i].content;
                            break;
                          }
                        }
                      }
                      return MessageBubble(message: msg, isUser: msg.role == 'user', userPrompt: userPrompt);
                    },
                  );
                },
                loading: () => Center(child: CircularProgressIndicator(color: palette.brand)),
                // Was a bare red line with no way out of it. Classified now, so
                // a rate limit, an expired session and a dead model each say
                // their own thing and offer Retry only where retrying helps.
                error: (err, _) => Center(
                  child: SingleChildScrollView(
                    child: AuraFailureView(
                      failure: AuraFailure.from(err),
                      onRetry: () => ref.invalidate(chatMessagesStreamProvider(conversationId)),
                    ),
                  ),
                ),
              ),
            ),
            const PendingActionsCard(),
            const _HostRequiredBanner(),
            ChatComposer(controller: _controller, isMobile: true, onSend: _send),
            ],
          ],
        ),
      ),
    );
  }
}

/// D7 Part B prerequisite: lets a task = a chat session, the way Part B's per-task isolated git
/// worktrees need. Scoped to Engineer-persona sessions only (`agentConversationsStreamProvider`),
/// so this never shows a mobile Copilot session for a different persona.
class _TaskSwitcher extends ConsumerWidget {
  const _TaskSwitcher({required this.activeSessionId});

  final String activeSessionId;

  String _labelFor(BuildContext context, String sessionId) {
    if (sessionId == 'default_session') return context.l10n.agentTaskDefault;
    final suffix = sessionId.split('_').last;
    return context.l10n.agentTaskLabel(suffix.length >= 5 ? suffix.substring(suffix.length - 5) : suffix);
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final palette = AuraPalette.of(context);
    final sessionsAsync = ref.watch(agentConversationsStreamProvider(_engineerAgentId));
    final sessions = sessionsAsync.asData?.value ?? const <String>[];
    final allSessions = sessions.contains(activeSessionId) ? sessions : [activeSessionId, ...sessions];

    return PopupMenuButton<String>(
      tooltip: context.l10n.agentSwitchTask,
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.expand_more, size: 16, color: palette.textSecondary),
          const SizedBox(width: 2),
          ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 90),
            child: Text(
              _labelFor(context, activeSessionId),
              overflow: TextOverflow.ellipsis,
              style: TextStyle(fontSize: 11, color: palette.textSecondary),
            ),
          ),
        ],
      ),
      itemBuilder: (context) => [
        PopupMenuItem(value: '__new__', child: Text(context.l10n.agentNewTask)),
        const PopupMenuDivider(),
        ...allSessions.map((session) => PopupMenuItem(
              value: session,
              child: Row(
                children: [
                  SizedBox(width: 14, child: session == activeSessionId ? const Icon(Icons.check, size: 14) : null),
                  const SizedBox(width: 6),
                  Expanded(child: Text(_labelFor(context, session), overflow: TextOverflow.ellipsis)),
                ],
              ),
            )),
      ],
      onSelected: (value) {
        if (value == '__new__') {
          ref.read(chatViewModelProvider.notifier).startNewSession();
        } else {
          ref.read(chatViewModelProvider.notifier).selectSession(value);
        }
      },
    );
  }
}


/// The Engineer is not usable in the active workspace. When another of the person's workspaces
/// includes it (a team member whose own workspace is on Free), say which and offer to switch:
/// a turn uses the active workspace's plan, while sign-in and pairing accept any of them, so
/// without this the app paired fine and then sat locked with no way forward.
class _EngineerLocked extends ConsumerWidget {
  const _EngineerLocked({required this.elsewhere});

  final List<WorkspaceSummary> elsewhere;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final l10n = context.l10n;
    if (elsewhere.isEmpty) {
      return AuraEmptyState(
        icon: Icons.lock_outline,
        title: l10n.agentLockedTitle,
        message: l10n.agentLockedMessage,
      );
    }
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        AuraEmptyState(
          icon: Icons.swap_horiz,
          title: l10n.agentLockedElsewhereTitle,
          message: l10n.agentLockedElsewhereMessage(elsewhere.first.name),
        ),
        const SizedBox(height: AuraSpacing.md),
        for (final workspace in elsewhere.take(3))
          Padding(
            padding: const EdgeInsets.only(bottom: AuraSpacing.sm),
            child: AuraButton(
              key: Key('engineer-switch-${workspace.id}'),
              label: l10n.agentLockedSwitchTo(workspace.name),
              onPressed: () => ref.read(activeWorkspaceProvider.notifier).select(workspace),
            ),
          ),
      ],
    );
  }
}

/// Says, above the composer, when Engineer cannot touch files: the daemon is down or unpaired.
/// Without it a request like "analyze this project" went out anyway, Engineer answered from the
/// knowledge base alone, and the reply read as Engineer not understanding the request.
class _HostRequiredBanner extends ConsumerStatefulWidget {
  const _HostRequiredBanner();

  @override
  ConsumerState<_HostRequiredBanner> createState() =>
      _HostRequiredBannerState();
}

class _HostRequiredBannerState extends ConsumerState<_HostRequiredBanner> {
  bool _busy = false;

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final l10n = context.l10n;
    final statusAsync = ref.watch(hostdStatusProvider);
    // Nothing until the first poll answers: a banner that flashes on every panel open would
    // teach people to ignore it.
    if (!statusAsync.hasValue) return const SizedBox.shrink();
    final step = hostAccessStepFor(statusAsync.value);
    if (step == HostAccessStep.grant) return const SizedBox.shrink();
    final notRunning = step == HostAccessStep.start;

    return Semantics(
      liveRegion: true,
      child: Container(
        margin: const EdgeInsets.fromLTRB(
          AuraSpacing.sm,
          0,
          AuraSpacing.sm,
          AuraSpacing.sm,
        ),
        padding: const EdgeInsetsDirectional.fromSTEB(
          AuraSpacing.md,
          AuraSpacing.sm,
          AuraSpacing.sm,
          AuraSpacing.sm,
        ),
        decoration: BoxDecoration(
          color: palette.warning.withValues(alpha: 0.10),
          borderRadius: BorderRadius.circular(AuraRadius.md),
          border: Border.all(color: palette.warning.withValues(alpha: 0.35)),
        ),
        child: Row(
          children: [
            Icon(
              notRunning ? Icons.cloud_off : Icons.link_off,
              size: 18,
              color: palette.warning,
            ),
            const SizedBox(width: AuraSpacing.sm),
            Expanded(
              child: Text(
                notRunning ? l10n.agentHostNotRunning : l10n.agentHostNotPaired,
                style: TextStyle(color: palette.textPrimary, fontSize: 12.5),
              ),
            ),
            const SizedBox(width: AuraSpacing.sm),
            AuraButton(
              label: notRunning ? l10n.hostStart : l10n.hostPair,
              icon: notRunning ? Icons.play_arrow : Icons.link,
              variant: AuraButtonVariant.secondary,
              loading: _busy,
              onPressed: () async {
                setState(() => _busy = true);
                try {
                  if (notRunning) {
                    await startHostDaemon(context, ref);
                  } else {
                    await pairHostDaemon(context, ref);
                  }
                } finally {
                  if (mounted) setState(() => _busy = false);
                }
              },
            ),
          ],
        ),
      ),
    );
  }
}
