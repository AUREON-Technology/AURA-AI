import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:intl/intl.dart';
import '../data/chat_repository.dart';
import '../../../core/theme/theme.dart';
import '../../../core/database/database.dart';

class ChatPage extends ConsumerStatefulWidget {
  const ChatPage({super.key});

  @override
  ConsumerState<ChatPage> createState() => _ChatPageState();
}

class _ChatPageState extends ConsumerState<ChatPage> {
  final _messageController = TextEditingController();
  final _scrollController = ScrollController();
  bool _useRag = true;
  String _geminiApiKey = ""; // Local API key state for offline fallback

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

  Future<void> _sendMessage(String sessionId) async {
    final text = _messageController.text.trim();
    if (text.isEmpty) return;

    _messageController.clear();
    FocusScope.of(context).unfocus();

    // Call chat repository send message
    await ref.read(chatRepositoryProvider).sendChatMessage(
          conversationId: sessionId,
          userContent: text,
          useRag: _useRag,
          localApiKey: _geminiApiKey.isEmpty ? null : _geminiApiKey,
        );

    // Short delay to let list update then scroll
    Future.delayed(const Duration(milliseconds: 100), _scrollToBottom);
  }

  @override
  Widget build(BuildContext context) {
    final activeSessionId = ref.watch(activeConversationIdProvider);
    final messagesAsync = ref.watch(chatMessagesStreamProvider(activeSessionId));
    final sessionsAsync = ref.watch(conversationsStreamProvider);

    return Scaffold(
      backgroundColor: AuraTheme.background,
      body: Row(
        children: [
          // Sidebar: Conversations List
          Container(
            width: 260,
            color: const Color(0xFF0C0917),
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 24),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                // New Chat Button
                ElevatedButton.icon(
                  onPressed: () {
                    final newId = "session_${DateTime.now().millisecondsSinceEpoch}";
                    ref.read(activeConversationIdProvider.notifier).setSession(newId);
                  },
                  icon: const Icon(Icons.add, size: 18),
                  label: const Text('New Session'),
                  style: ElevatedButton.styleFrom(
                    padding: const EdgeInsets.symmetric(vertical: 12),
                  ),
                ),
                const SizedBox(height: 24),
                const Text(
                  'CHATS SESSIONS',
                  style: TextStyle(
                    color: AuraTheme.textSecondary,
                    fontSize: 11,
                    fontWeight: FontWeight.bold,
                    letterSpacing: 1.2,
                  ),
                ),
                const SizedBox(height: 12),

                // Sessions List
                Expanded(
                  child: sessionsAsync.when(
                    data: (sessions) {
                      final allSessions = sessions.contains('default_session')
                          ? sessions
                          : ['default_session', ...sessions];

                      return ListView.builder(
                        itemCount: allSessions.length,
                        itemBuilder: (context, index) {
                          final session = allSessions[index];
                          final isActive = session == activeSessionId;
                          final label = session == 'default_session'
                              ? 'Default Workspace'
                              : 'Session ${session.split('_').last.substring(0, 5)}';

                          return Padding(
                            padding: const EdgeInsets.only(bottom: 6.0),
                            child: ListTile(
                              dense: true,
                              selected: isActive,
                              selectedTileColor: AuraTheme.accentIndigo.withValues(alpha: 0.15),
                              shape: RoundedRectangleBorder(
                                borderRadius: BorderRadius.circular(10),
                              ),
                              title: Text(
                                label,
                                style: TextStyle(
                                  color: isActive ? Colors.white : AuraTheme.textSecondary,
                                  fontWeight: isActive ? FontWeight.bold : FontWeight.normal,
                                ),
                              ),
                              trailing: isActive && session != 'default_session'
                                  ? IconButton(
                                      icon: const Icon(Icons.delete_outline, size: 16, color: Colors.white30),
                                      onPressed: () {
                                        ref.read(chatRepositoryProvider).clearConversation(session);
                                        ref.read(activeConversationIdProvider.notifier).setSession('default_session');
                                      },
                                    )
                                  : null,
                              onTap: () {
                                ref.read(activeConversationIdProvider.notifier).setSession(session);
                              },
                            ),
                          );
                        },
                      );
                    },
                    loading: () => const Center(
                      child: CircularProgressIndicator(strokeWidth: 2),
                    ),
                    error: (err, _) => Text('Error: $err', style: const TextStyle(color: Colors.red)),
                  ),
                ),
                const Divider(color: Color(0x1F94A3B8), height: 32),
                
                // Fallback Key Inputs
                TextField(
                  onChanged: (val) => setState(() => _geminiApiKey = val),
                  obscureText: true,
                  decoration: const InputDecoration(
                    labelText: 'Local Gemini API Key',
                    hintText: 'Enter API Key (Optional)',
                    contentPadding: EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                  ),
                  style: const TextStyle(color: Colors.white, fontSize: 12),
                ),
              ],
            ),
          ),

          // Main Chat Area
          Expanded(
            child: Container(
              decoration: const BoxDecoration(
                gradient: RadialGradient(
                  center: Alignment(-0.6, -0.6),
                  radius: 1.4,
                  colors: [
                    Color(0x0C6366F1), // Translucent indigo glow
                    Colors.transparent,
                  ],
                ),
              ),
              child: Column(
                children: [
                  // Chat Page Header
                  Container(
                    padding: const EdgeInsets.all(20.0),
                    decoration: const BoxDecoration(
                      border: Border(
                        bottom: BorderSide(color: Color(0x1F94A3B8), width: 1),
                      ),
                    ),
                    child: Row(
                      children: [
                        const Icon(Icons.auto_awesome, color: AuraTheme.accentIndigo, size: 28),
                        const SizedBox(width: 12),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              const Text(
                                'Aura Assistant',
                                style: TextStyle(
                                  fontSize: 18,
                                  fontWeight: FontWeight.bold,
                                  color: Colors.white,
                                ),
                              ),
                              Row(
                                children: [
                                  Container(
                                    width: 8,
                                    height: 8,
                                    decoration: const BoxDecoration(
                                      color: AuraTheme.accentTeal,
                                      shape: BoxShape.circle,
                                    ),
                                  ),
                                  const SizedBox(width: 6),
                                  const Text(
                                    'Hybrid OS Active • Gemini 1.5 Flash',
                                    style: TextStyle(
                                      fontSize: 11,
                                      color: AuraTheme.textSecondary,
                                    ),
                                  ),
                                ],
                              ),
                            ],
                          ),
                        ),
                        // RAG Toggler
                        Row(
                          children: [
                            const Text(
                              'Retrieve RAG Context',
                              style: TextStyle(fontSize: 12, color: AuraTheme.textSecondary),
                            ),
                            Switch(
                              value: _useRag,
                              activeTrackColor: AuraTheme.accentIndigo,
                              onChanged: (val) {
                                setState(() {
                                  _useRag = val;
                                });
                              },
                            ),
                          ],
                        ),
                      ],
                    ),
                  ),

                  // Chat Message Feed
                  Expanded(
                    child: messagesAsync.when(
                      data: (messages) {
                        if (messages.isEmpty) {
                          return const EmptyStateChat();
                        }
                        // Scroll to bottom when list changes
                        WidgetsBinding.instance.addPostFrameCallback((_) => _scrollToBottom());

                        return ListView.builder(
                          controller: _scrollController,
                          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 20),
                          itemCount: messages.length,
                          itemBuilder: (context, index) {
                            final msg = messages[index];
                            final isUser = msg.role == "user";
                            return MessageBubble(message: msg, isUser: isUser);
                          },
                        );
                      },
                      loading: () => const Center(
                        child: CircularProgressIndicator(color: AuraTheme.accentIndigo),
                      ),
                      error: (err, _) => Center(
                        child: Text('Failed to load message history: $err',
                            style: const TextStyle(color: Colors.redAccent)),
                      ),
                    ),
                  ),

                  // Input Bar
                  Container(
                    padding: const EdgeInsets.all(24.0),
                    decoration: const BoxDecoration(
                      border: Border(
                        top: BorderSide(color: Color(0x1F94A3B8), width: 1),
                      ),
                    ),
                    child: Row(
                      children: [
                        Expanded(
                          child: TextField(
                            controller: _messageController,
                            decoration: const InputDecoration(
                              hintText: 'Ask Aura anything... (e.g. Explain how to set up Riverpod)',
                            ),
                            style: const TextStyle(color: Colors.white),
                            onSubmitted: (_) => _sendMessage(activeSessionId),
                          ),
                        ),
                        const SizedBox(width: 16),
                        Container(
                          decoration: BoxDecoration(
                            gradient: AuraTheme.primaryGradient,
                            borderRadius: BorderRadius.circular(12),
                          ),
                          child: IconButton(
                            icon: const Icon(Icons.send, color: Colors.white),
                            onPressed: () => _sendMessage(activeSessionId),
                          ),
                        ),
                      ],
                    ),
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

// ----------------------------------------------------
// Message Bubble Widget
// ----------------------------------------------------
class MessageBubble extends StatelessWidget {
  final Message message;
  final bool isUser;
  const MessageBubble({super.key, required this.message, required this.isUser});

  @override
  Widget build(BuildContext context) {
    final formattedTime = DateFormat('jm').format(message.createdAt);

    return Align(
      alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.only(bottom: 16),
        constraints: BoxConstraints(
          maxWidth: MediaQuery.of(context).size.width * 0.55,
        ),
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
          color: isUser ? null : AuraTheme.cardBg,
          gradient: isUser ? AuraTheme.primaryGradient : null,
          borderRadius: BorderRadius.only(
            topLeft: const Radius.circular(16),
            topRight: const Radius.circular(16),
            bottomLeft: isUser ? const Radius.circular(16) : const Radius.circular(0),
            bottomRight: isUser ? const Radius.circular(0) : const Radius.circular(16),
          ),
          border: isUser
              ? null
              : Border.all(color: const Color(0x1F94A3B8), width: 1),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (isUser)
              Text(
                message.content,
                style: const TextStyle(color: Colors.white, fontSize: 15, height: 1.4),
              )
            else
              MarkdownBody(
                data: message.content.isEmpty ? "..." : message.content,
                styleSheet: MarkdownStyleSheet(
                  p: const TextStyle(color: Colors.white, fontSize: 15, height: 1.4),
                  code: const TextStyle(
                    backgroundColor: Color(0xFF1E1B4B),
                    color: Color(0xFFF59E0B),
                    fontFamily: 'Courier',
                    fontSize: 13,
                  ),
                  codeblockDecoration: BoxDecoration(
                    color: const Color(0xFF0F0C1B),
                    borderRadius: BorderRadius.circular(8),
                    border: Border.all(color: const Color(0x1F94A3B8)),
                  ),
                ),
              ),
            const SizedBox(height: 8),
            Align(
              alignment: Alignment.bottomRight,
              child: Text(
                formattedTime,
                style: TextStyle(
                  fontSize: 10,
                  color: isUser ? Colors.white70 : AuraTheme.textSecondary,
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

// ----------------------------------------------------
// Empty State Widget
// ----------------------------------------------------
class EmptyStateChat extends StatelessWidget {
  const EmptyStateChat({super.key});

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Container(
            padding: const EdgeInsets.all(24),
            decoration: BoxDecoration(
              color: AuraTheme.accentIndigo.withValues(alpha: 0.1),
              shape: BoxShape.circle,
            ),
            child: const Icon(
              Icons.chat_bubble_outline,
              size: 64,
              color: AuraTheme.accentIndigo,
            ),
          ),
          const SizedBox(height: 24),
          const Text(
            'Ask Aura Anything',
            style: TextStyle(
              fontSize: 22,
              fontWeight: FontWeight.bold,
              color: Colors.white,
            ),
          ),
          const SizedBox(height: 8),
          const Text(
            'Ask programming questions, academic inquiries,\n or ask it to sync schedules and coordinate tasks.',
            textAlign: TextAlign.center,
            style: TextStyle(
              fontSize: 14,
              color: AuraTheme.textSecondary,
              height: 1.4,
            ),
          ),
        ],
      ),
    );
  }
}
