import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import 'package:aura_client/core/design_system/design_system.dart';

import '../data/chat_repository.dart';
import 'chat_view_model.dart';
import 'widgets/chat_composer.dart';
import 'widgets/chat_header.dart';
import 'widgets/chat_sidebar.dart';
import 'widgets/empty_state_chat.dart';
import 'widgets/message_bubble.dart';
import 'widgets/confirmation_sheet.dart';
import 'widgets/pending_actions_card.dart';

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

  /// Auto-sends the Home capture-bar query once, then clears GoRouter extra
  /// so a shell rebuild / hot reload cannot fire it again.
  void _consumeInitialQuery(String query) {
    if (!mounted) return;
    _messageController.clear();
    unawaited(ref.read(chatViewModelProvider.notifier).consumeInitialQuery(query));

    if (GoRouterState.of(context).extra is String) {
      context.go('/chat');
    }
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

  Future<void> _sendMessage() async {
    final text = _messageController.text;
    final chat = ref.read(chatViewModelProvider);
    final isStreaming = ref.read(streamingMessageIdProvider) != null;
    if (!chat.canSend(text, isStreaming: isStreaming)) return;

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
    final sessionsAsync = ref.watch(conversationsStreamProvider);
    final isMobile = MediaQuery.of(context).size.width < 768;

    return Scaffold(
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
                        WidgetsBinding.instance.addPostFrameCallback((_) => _scrollToBottom());

                        return ListView.builder(
                          controller: _scrollController,
                          padding: const EdgeInsets.symmetric(horizontal: AuraSpacing.xl, vertical: AuraSpacing.lg),
                          itemCount: messages.length,
                          itemBuilder: (context, index) {
                            final msg = messages[index];
                            return MessageBubble(message: msg, isUser: msg.role == 'user');
                          },
                        );
                      },
                      loading: () => Center(child: CircularProgressIndicator(color: palette.brand)),
                      error: (err, _) => Center(
                        child: Text('Failed to load message history: $err', style: TextStyle(color: palette.danger)),
                      ),
                    ),
                  ),
                  const ConfirmationCard(),
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
    );
  }
}
