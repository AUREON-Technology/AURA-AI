import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/ai/engineer/engineer_consent_repository.dart';
import 'package:aura_client/core/ai/vision/chat_images.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/database/database.dart';

import '../../engineering/presentation/engineer_consent_sheet.dart';
import '../data/agent_config_repository.dart';
import '../data/chat_repository.dart';
import 'chat_view_model.dart';
import 'widgets/chat_composer.dart';
import 'widgets/chat_header.dart';
import 'widgets/chat_sidebar.dart';
import 'widgets/empty_state_chat.dart';
import 'widgets/message_bubble.dart';
import 'widgets/pending_actions_card.dart';
import 'package:aura_client/core/network/aura_failure.dart';
import 'package:aura_client/core/network/aura_failure_view.dart';
import 'package:aura_client/l10n/l10n.dart';

/// "Copilot" -- chat, rebuilt on the V2 design system.
///
/// Locally scoped to [AuraThemeV2] via the [Theme] wrapper in [build] rather
/// than flipping the app-wide theme, matching how home_page.dart/
/// settings_page.dart already do this (see Doc/V2_BLUEPRINT.md §13 Phase 2 --
/// a deliberate incremental migration, not a big-bang swap).
class ChatPage extends ConsumerStatefulWidget {
  final String? initialQuery;

  const ChatPage({super.key, this.initialQuery});

  @override
  ConsumerState<ChatPage> createState() => _ChatPageState();
}

class _ChatPageState extends ConsumerState<ChatPage> {
  final _messageController = TextEditingController();
  final _scrollController = ScrollController();
  final _scaffoldKey = GlobalKey<ScaffoldState>();

  @override
  void initState() {
    super.initState();
    final q = widget.initialQuery?.trim();
    if (q == null || q.isEmpty) return;
    _messageController.text = q;
    WidgetsBinding.instance.addPostFrameCallback((_) => _consumeInitialQuery(q));
  }

  /// Auto-sends the Home capture-bar query once. [ChatUiState.consumedInitialQuery]
  /// prevents a second send on rebuild; we intentionally do not call [GoRouter.go]
  /// here because re-navigating to `/chat` would dispose this page mid-callback.
  Future<void> _consumeInitialQuery(String query) async {
    if (!mounted) return;
    // The query stays in the composer when the user declines the Engineer notice.
    if (!await _engineerConsentGiven() || !mounted) return;
    _messageController.clear();
    unawaited(ref.read(chatViewModelProvider.notifier).consumeInitialQuery(query));
  }

  @override
  void dispose() {
    _messageController.dispose();
    _scrollController.dispose();
    super.dispose();
  }

  void _scrollToBottom() {
    if (_scrollController.hasClients) {
      _scrollController.animateTo(
        _scrollController.position.maxScrollExtent,
        duration: const Duration(milliseconds: 300),
        curve: Curves.easeOut,
      );
    }
  }

  /// D-03: an Engineer send waits for consent. Any other persona sends at once, with no request.
  Future<bool> _engineerConsentGiven() async {
    if (ref.read(activeAgentIdProvider) != kEngineerAgentId) return true;
    return ensureEngineerConsent(context, ref);
  }

  Future<void> _sendMessage() async {
    final text = _messageController.text;
    final chat = ref.read(chatViewModelProvider);
    final isStreaming = ref.read(streamingMessageIdProvider) != null;
    final hasImages = ref.read(pendingChatImagesProvider).isNotEmpty;
    if (!chat.canSend(text, isStreaming: isStreaming, hasImages: hasImages)) return;
    // Before the composer is cleared: after "Not now" the message is still there to send later.
    if (!await _engineerConsentGiven() || !mounted) return;

    _messageController.clear();
    if (mounted) FocusScope.of(context).unfocus();

    final sent = await ref.read(chatViewModelProvider.notifier).sendMessage(text);
    if (!sent || !mounted) return;
    Future.delayed(const Duration(milliseconds: 100), () {
      if (mounted) _scrollToBottom();
    });
  }

  @override
  Widget build(BuildContext context) {
    return _buildScaffold(context);
  }

  Widget _buildScaffold(BuildContext context) {
    final palette = AuraPalette.of(context);
    final activeSessionId = ref.watch(activeConversationIdProvider);
    final messagesAsync = ref.watch(chatMessagesStreamProvider(activeSessionId));
    final visibleLimit = ref.watch(chatVisibleMessageLimitProvider(activeSessionId));
    final sessionsAsync = ref.watch(conversationsStreamProvider);
    final isMobile = MediaQuery.of(context).size.width < 768;

    ref.listen<AsyncValue<List<Message>>>(
      chatMessagesStreamProvider(activeSessionId),
      (previous, next) {
        final messages = next.asData?.value;
        if (messages == null || messages.isEmpty || !mounted) return;
        // "Load earlier messages" also emits a new list (grown at the front) -- only auto-scroll
        // when the newest message actually changed, not on every emission, or loading history
        // would immediately yank the view back to the bottom.
        final previousMessages = previous?.asData?.value;
        final previousLast = (previousMessages != null && previousMessages.isNotEmpty)
            ? previousMessages.last
            : null;
        if (previousLast != null && previousLast.id == messages.last.id) return;
        WidgetsBinding.instance.addPostFrameCallback((_) {
          if (mounted) _scrollToBottom();
        });
      },
    );

    return EngineerConsentPromptListener(
      child: Scaffold(
        key: _scaffoldKey,
        backgroundColor: palette.background,
        drawer: isMobile
            ? Drawer(
                child: ChatSidebar(
                  activeSessionId: activeSessionId,
                  sessionsAsync: sessionsAsync,
                  isMobile: true,
                ),
              )
            : null,
        body: Row(
          children: [
            if (!isMobile)
              ChatSidebar(
                activeSessionId: activeSessionId,
                sessionsAsync: sessionsAsync,
                isMobile: false,
              ),
            Expanded(
              child: Container(
                decoration: BoxDecoration(
                  gradient: RadialGradient(
                    center: const Alignment(-0.6, -0.6),
                    radius: 1.4,
                    colors: [palette.brand.withValues(alpha: 0.05), Colors.transparent],
                  ),
                ),
                child: Column(
                  children: [
                    ChatHeader(
                      isMobile: isMobile,
                      onOpenDrawer: () => _scaffoldKey.currentState?.openDrawer(),
                    ),
                    Expanded(
                      child: messagesAsync.when(
                        data: (messages) {
                          if (messages.isEmpty) {
                            return EmptyStateChat(
                              onSuggestionSelected: (prompt) => _messageController.text = prompt,
                            );
                          }

                          // A full page came back, so there may be older messages this query
                          // didn't fetch (see ChatVisibleMessageLimit's paging, added to bound
                          // how many encrypted rows get decrypted on first load of a long
                          // conversation). Fewer than the limit means history is exhausted.
                          final hasMoreHistory = messages.length >= visibleLimit;
                          final itemCount = messages.length + (hasMoreHistory ? 1 : 0);

                          return ListView.builder(
                            controller: _scrollController,
                            padding: const EdgeInsets.symmetric(horizontal: AuraSpacing.xl, vertical: AuraSpacing.lg),
                            itemCount: itemCount,
                            itemBuilder: (context, index) {
                              if (hasMoreHistory && index == 0) {
                                return Padding(
                                  padding: const EdgeInsets.only(bottom: AuraSpacing.md),
                                  child: Center(
                                    child: TextButton(
                                      onPressed: () => ref
                                          .read(chatVisibleMessageLimitProvider(activeSessionId).notifier)
                                          .loadMore(),
                                      child: Text(context.l10n.chatLoadEarlier),
                                    ),
                                  ),
                                );
                              }
                              final msg = messages[hasMoreHistory ? index - 1 : index];
                              String? userPrompt;
                              if (msg.role != 'user') {
                                for (var i = (hasMoreHistory ? index - 1 : index) - 1; i >= 0; i--) {
                                  if (messages[i].role == 'user') {
                                    userPrompt = messages[i].content;
                                    break;
                                  }
                                }
                              }
                              return MessageBubble(
                                message: msg,
                                isUser: msg.role == 'user',
                                userPrompt: userPrompt,
                              );
                            },
                          );
                        },
                        loading: () => Center(child: CircularProgressIndicator(color: palette.brand)),
                        error: (err, _) => Center(
                          child: AuraFailureView(failure: AuraFailure.from(err)),
                        ),
                      ),
                    ),
                    const PendingActionsCard(),
                    ChatComposer(
                      controller: _messageController,
                      isMobile: isMobile,
                      onSend: _sendMessage,
                    ),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
