/// Agent panel for Desktop IDE.
///
/// Embeds the real chat engine (chatViewModelProvider/chatRepositoryProvider
/// — the same one Copilot uses) into the IDE's secondary sidebar, forced to
/// the Engineer persona, so the AI is reachable without leaving the IDE.
/// Replaces the static "Agent Panel (D6)" placeholder that was here before.
library;

import 'package:collection/collection.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/features/chat/data/agent_config_repository.dart';
import 'package:aura_client/features/chat/data/agents_repository.dart';
import 'package:aura_client/features/chat/data/chat_repository.dart';
import 'package:aura_client/features/chat/presentation/chat_view_model.dart';
import 'package:aura_client/features/chat/presentation/widgets/chat_composer.dart';
import 'package:aura_client/features/chat/presentation/widgets/message_bubble.dart';
import 'package:aura_client/features/chat/presentation/widgets/pending_actions_card.dart';
import 'package:aura_client/desktop/state/state_notifiers.dart';

const _engineerAgentId = 'engineer';

class AgentPanel extends ConsumerStatefulWidget {
  const AgentPanel({super.key});

  @override
  ConsumerState<AgentPanel> createState() => _AgentPanelState();
}

class _AgentPanelState extends ConsumerState<AgentPanel> {
  final _controller = TextEditingController();
  final _scrollController = ScrollController();

  @override
  void initState() {
    super.initState();
    // This panel only ever talks to Engineer — force the (app-global) active
    // persona so a message sent from here routes correctly, same as picking
    // "Engineer" from Copilot's own persona picker would.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) ref.read(activeAgentIdProvider.notifier).setAgent(_engineerAgentId);
    });
  }

  @override
  void dispose() {
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
    if (!chat.canSend(text, isStreaming: isStreaming)) return;

    _controller.clear();
    final sent = await ref.read(chatViewModelProvider.notifier).sendMessage(text);
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
    final workspaceRoot = ref.watch(workspaceNotifierProvider).activeWorkspace?.rootPath;
    final (personaLabel, personaIcon) = personaDisplay(_engineerAgentId);

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

    return Container(
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
              ],
            ),
          ),
          if (workspaceRoot != null)
            Container(
              width: double.infinity,
              padding: const EdgeInsets.symmetric(horizontal: AuraSpacing.md, vertical: 6),
              color: palette.surfaceVariant,
              child: Text(
                'Workspace: $workspaceRoot',
                style: TextStyle(fontSize: 10.5, color: palette.textSecondary, fontFamily: 'Courier'),
                overflow: TextOverflow.ellipsis,
              ),
            ),
          Expanded(
            child: messagesAsync.when(
              data: (messages) {
                if (messages.isEmpty) {
                  return Center(
                    child: Padding(
                      padding: const EdgeInsets.all(AuraSpacing.lg),
                      child: Text(
                        'Ask Engineer about this workspace.',
                        textAlign: TextAlign.center,
                        style: TextStyle(color: palette.textDisabled, fontSize: 12),
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
              error: (err, _) => Padding(
                padding: const EdgeInsets.all(AuraSpacing.md),
                child: Text('Failed to load messages: $err', style: TextStyle(color: palette.danger, fontSize: 12)),
              ),
            ),
          ),
          const PendingActionsCard(),
          ChatComposer(controller: _controller, isMobile: true, onSend: _send),
        ],
      ),
    );
  }
}
