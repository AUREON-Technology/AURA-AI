import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:intl/intl.dart';
import '../data/chat_repository.dart';
import '../data/agent_config_repository.dart';
import '../data/memory_repository.dart';
import '../../tasks/data/task_repository.dart';
import '../../calendar/data/calendar_repository.dart';
import '../../finance/data/finance_repository.dart';
import '../../health/data/health_repository.dart';
import 'package:aura_client/core/theme/theme.dart';
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:drift/drift.dart' show OrderingTerm, OrderingMode;
import '../../sync/sync_engine.dart';
import 'package:aura_client/core/ai/ai_router/aura_ai_router.dart';

class ChatPage extends ConsumerStatefulWidget {
  const ChatPage({super.key});

  @override
  ConsumerState<ChatPage> createState() => _ChatPageState();
}

class _ChatPageState extends ConsumerState<ChatPage> {
  final _messageController = TextEditingController();
  final _scrollController = ScrollController();
  bool _useRag = true;

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
        );

    // Short delay to let list update then scroll
    Future.delayed(const Duration(milliseconds: 100), _scrollToBottom);
  }

  final _scaffoldKey = GlobalKey<ScaffoldState>();

  Widget _buildSidebar(BuildContext context, String activeSessionId, AsyncValue<List<String>> sessionsAsync) {
    final isMobile = MediaQuery.of(context).size.width < 768;

    return Container(
      width: 260,
      color: const Color(0xFF0C0917),
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 24),
      child: ListView(
        padding: EdgeInsets.zero,
        children: [
          // New Chat Button
          ElevatedButton.icon(
            onPressed: () {
              final newId = "session_${DateTime.now().millisecondsSinceEpoch}";
              ref.read(activeConversationIdProvider.notifier).setSession(newId);
              if (isMobile) {
                Navigator.pop(context); // Close drawer
              }
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
          sessionsAsync.when(
            data: (sessions) {
              final allSessions = sessions.contains('default_session')
                  ? sessions
                  : ['default_session', ...sessions];

              return ListView.builder(
                shrinkWrap: true,
                physics: const NeverScrollableScrollPhysics(),
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
                      selectedTileColor: AuraTheme.accentIndigo.withAlpha(38),
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
                        if (isMobile) {
                          Navigator.pop(context); // Close drawer
                        }
                      },
                    ),
                  );
                },
              );
            },
            loading: () => const Center(
              child: Padding(
                padding: EdgeInsets.all(8.0),
                child: CircularProgressIndicator(strokeWidth: 2),
              ),
            ),
            error: (err, _) => Padding(
              padding: const EdgeInsets.all(8.0),
              child: Text('Error: $err', style: const TextStyle(color: Colors.red)),
            ),
          ),
          
          // Mobile specific settings in drawer to avoid header overflow
          if (isMobile) ...[
            const Divider(color: Color(0x1F94A3B8), height: 32),
            const Text(
              'AGENT CONFIGURATIONS',
              style: TextStyle(
                color: AuraTheme.textSecondary,
                fontSize: 11,
                fontWeight: FontWeight.bold,
                letterSpacing: 1.2,
              ),
            ),
            const SizedBox(height: 12),
            // Agent Selector Dropdown
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                const Text('Active Agent:', style: TextStyle(fontSize: 12, color: Colors.white70)),
                DropdownButton<String>(
                  value: ref.watch(activeAgentIdProvider),
                  dropdownColor: const Color(0xFF0F0C1B),
                  style: const TextStyle(color: Colors.white, fontSize: 13),
                  underline: const SizedBox(),
                  items: const [
                    DropdownMenuItem(value: 'orchestrator', child: Text('Orchestrator')),
                    DropdownMenuItem(value: 'task_planner', child: Text('Task Planner')),
                    DropdownMenuItem(value: 'finance_advisor', child: Text('Finance Advisor')),
                    DropdownMenuItem(value: 'health_companion', child: Text('Health Companion')),
                    DropdownMenuItem(value: 'academic_coach', child: Text('Academic Coach')),
                  ],
                  onChanged: (val) {
                    if (val != null) {
                      ref.read(activeAgentIdProvider.notifier).setAgent(val);
                    }
                  },
                ),
              ],
            ),
            const SizedBox(height: 8),
            // RAG Toggler
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                const Text('Use Knowledge (RAG):', style: TextStyle(fontSize: 12, color: Colors.white70)),
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
            const SizedBox(height: 8),
            // Agent Prompt Settings button
            ElevatedButton.icon(
              onPressed: () {
                Navigator.pop(context); // Close drawer
                _showAgentSettingsDialog();
              },
              icon: const Icon(Icons.settings_suggest, size: 16),
              label: const Text('Prompt Settings', style: TextStyle(fontSize: 12)),
              style: ElevatedButton.styleFrom(
                backgroundColor: const Color(0x0DFFFFFF),
                padding: const EdgeInsets.symmetric(vertical: 8),
              ),
            ),
          ],
          
          const Divider(color: Color(0x1F94A3B8), height: 32),
          const Text(
            'AI ROUTING CONFIG',
            style: TextStyle(
              color: AuraTheme.textSecondary,
              fontSize: 11,
              fontWeight: FontWeight.bold,
              letterSpacing: 1.2,
            ),
          ),
          const SizedBox(height: 12),
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              const Text('AI engine:', style: TextStyle(fontSize: 12, color: Colors.white70)),
              // A read-out, not a control. This was a dropdown selecting between a hosted API and
              // three on-device models; all four are gone, and a picker with one option is a worse
              // answer to "which engine am I using" than simply saying.
              Text(
                ref.watch(aiRouterStatusProvider).isOffline ? 'AURA (unreachable)' : 'AURA server',
                style: const TextStyle(fontSize: 12, color: Colors.white),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildSyncIndicator(bool isMobile) {
    final syncState = ref.watch(syncEngineProvider);
    
    Color indicatorColor;
    String statusText;
    IconData syncIcon;
    Color iconColor;
    bool isSyncing = syncState == SyncState.syncing;
    
    switch (syncState) {
      case SyncState.synced:
        indicatorColor = AuraTheme.accentTeal;
        statusText = 'Synced';
        syncIcon = Icons.sync;
        iconColor = AuraTheme.accentTeal;
        break;
      case SyncState.syncing:
        indicatorColor = AuraTheme.accentAmber;
        statusText = 'Syncing';
        syncIcon = Icons.sync;
        iconColor = AuraTheme.accentAmber;
        break;
      case SyncState.offline:
        indicatorColor = Colors.grey;
        statusText = 'Offline';
        syncIcon = Icons.sync_disabled;
        iconColor = Colors.grey;
        break;
      case SyncState.error:
        indicatorColor = Colors.redAccent;
        statusText = 'Error';
        syncIcon = Icons.sync_problem;
        iconColor = Colors.redAccent;
        break;
    }
    
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: BoxDecoration(
        color: Colors.white.withAlpha(8),
        border: Border.all(color: Colors.white.withAlpha(20)),
        borderRadius: BorderRadius.circular(16),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            width: 8,
            height: 8,
            decoration: BoxDecoration(
              color: indicatorColor,
              shape: BoxShape.circle,
              boxShadow: [
                BoxShadow(
                  color: indicatorColor.withAlpha(128),
                  blurRadius: 6,
                  spreadRadius: 2,
                ),
              ],
            ),
          ),
          if (!isMobile) ...[
            const SizedBox(width: 8),
            Text(
              statusText,
              style: TextStyle(
                fontSize: 11,
                color: Colors.white.withAlpha(178),
                fontWeight: FontWeight.w500,
              ),
            ),
          ],
          const SizedBox(width: 4),
          isSyncing
              ? const SizedBox(
                  width: 12,
                  height: 12,
                  child: CircularProgressIndicator(
                    strokeWidth: 1.5,
                    valueColor: AlwaysStoppedAnimation<Color>(AuraTheme.accentAmber),
                  ),
                )
              : IconButton(
                  padding: EdgeInsets.zero,
                  constraints: const BoxConstraints(),
                  icon: Icon(syncIcon, color: iconColor, size: 12),
                  tooltip: 'Sync Now',
                  onPressed: syncState == SyncState.offline
                      ? null
                      : () => ref.read(syncEngineProvider.notifier).synchronize(),
                ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final activeSessionId = ref.watch(activeConversationIdProvider);
    final messagesAsync = ref.watch(chatMessagesStreamProvider(activeSessionId));
    final sessionsAsync = ref.watch(conversationsStreamProvider);
    final isMobile = MediaQuery.of(context).size.width < 768;

    return Scaffold(
      key: _scaffoldKey,
      backgroundColor: AuraTheme.background,
      drawer: isMobile
          ? Drawer(
              child: _buildSidebar(context, activeSessionId, sessionsAsync),
            )
          : null,
      body: Row(
        children: [
          // Sidebar: Conversations List (only visible directly on desktop)
          if (!isMobile) _buildSidebar(context, activeSessionId, sessionsAsync),

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
                        if (isMobile) ...[
                          IconButton(
                            icon: const Icon(Icons.menu, color: Colors.white),
                            onPressed: () {
                              _scaffoldKey.currentState?.openDrawer();
                            },
                          ),
                          const SizedBox(width: 8),
                        ],
                        Container(
                          width: 32,
                          height: 32,
                          decoration: BoxDecoration(
                            borderRadius: BorderRadius.circular(8),
                            boxShadow: [
                              BoxShadow(
                                color: AuraTheme.accentIndigo.withValues(alpha: 0.3),
                                blurRadius: 8,
                                offset: const Offset(0, 2),
                              ),
                            ],
                          ),
                          child: ClipRRect(
                            borderRadius: BorderRadius.circular(8),
                            child: Image.asset(
                              'assets/app_icon.png',
                              fit: BoxFit.contain,
                            ),
                          ),
                        ),
                        const SizedBox(width: 12),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              const Text(
                                'Aura Assistant',
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
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
                                    decoration: BoxDecoration(
                                      color: ref.watch(aiRouterStatusProvider).isOffline 
                                          ? AuraTheme.accentAmber 
                                          : AuraTheme.accentTeal,
                                      shape: BoxShape.circle,
                                    ),
                                  ),
                                  const SizedBox(width: 6),
                                  Expanded(
                                    child: Text(
                                      isMobile 
                                          ? 'Active • ${ref.watch(aiRouterStatusProvider).activeModel}' 
                                          : 'Hybrid OS Active • ${ref.watch(aiRouterStatusProvider).activeModel} (${ref.watch(aiRouterStatusProvider).status}) • Latency: ${ref.watch(aiRouterStatusProvider).latencyMs.toStringAsFixed(0)}ms',
                                      maxLines: 1,
                                      overflow: TextOverflow.ellipsis,
                                      style: const TextStyle(
                                        fontSize: 11,
                                        color: AuraTheme.textSecondary,
                                      ),
                                    ),
                                  ),
                                ],
                              ),
                            ],
                          ),
                        ),
                        // Memory Bank Button
                        IconButton(
                          icon: const Icon(Icons.psychology_outlined, color: AuraTheme.accentAmber, size: 20),
                          tooltip: 'Memory Bank',
                          onPressed: _showMemoryBankDialog,
                        ),
                        
                        // Desktop Only Settings Buttons
                        if (!isMobile) ...[
                          const SizedBox(width: 8),
                          // Agent Selector Dropdown
                          const Text(
                            'Agent: ',
                            style: TextStyle(fontSize: 12, color: AuraTheme.textSecondary),
                          ),
                          const SizedBox(width: 4),
                          DropdownButton<String>(
                            value: ref.watch(activeAgentIdProvider),
                            dropdownColor: const Color(0xFF0F0C1B),
                            style: const TextStyle(color: Colors.white, fontSize: 13),
                            underline: const SizedBox(),
                            items: const [
                              DropdownMenuItem(value: 'orchestrator', child: Text('Auto-Orchestrator')),
                              DropdownMenuItem(value: 'task_planner', child: Text('Task Planner')),
                              DropdownMenuItem(value: 'finance_advisor', child: Text('Finance Advisor')),
                              DropdownMenuItem(value: 'health_companion', child: Text('Health Companion')),
                              DropdownMenuItem(value: 'academic_coach', child: Text('Academic Coach')),
                            ],
                            onChanged: (val) {
                              if (val != null) {
                                ref.read(activeAgentIdProvider.notifier).setAgent(val);
                              }
                            },
                          ),
                          const SizedBox(width: 8),
                          // Agent Config Button
                          IconButton(
                            icon: const Icon(Icons.settings_suggest, color: AuraTheme.accentIndigo, size: 20),
                            tooltip: 'Agent Prompt Settings',
                            onPressed: _showAgentSettingsDialog,
                          ),
                          const SizedBox(width: 12),
                          // RAG Toggler
                          Row(
                            children: [
                              const Text(
                                'RAG',
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
                        const SizedBox(width: 8),
                        // Sync Status Indicator & Manual Sync Button
                        _buildSyncIndicator(isMobile),
                      ],
                    ),
                  ),

                  // Chat Message Feed
                  Expanded(
                    child: messagesAsync.when(
                      data: (messages) {
                        if (messages.isEmpty) {
                          return EmptyStateChat(
                            onSuggestionSelected: (prompt) {
                              _messageController.text = prompt;
                            },
                          );
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

                  // Pending Actions Confirmation Panel
                  _buildPendingActionsCard(),

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
                            decoration: InputDecoration(
                              hintText: isMobile ? 'Ask Aura...' : 'Ask Aura anything... (e.g. Explain how to set up Riverpod)',
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

  Widget _buildPendingActionsCard() {
    final actions = ref.watch(pendingActionsProvider);
    if (actions.isEmpty) return const SizedBox();

    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 24, vertical: 8),
      color: AuraTheme.cardBg,
      child: Padding(
        padding: const EdgeInsets.all(16.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Row(
              children: [
                Icon(Icons.auto_awesome, color: AuraTheme.accentTeal, size: 20),
                SizedBox(width: 8),
                Text(
                  "AURA Proposes Actions",
                  style: TextStyle(fontWeight: FontWeight.bold, fontSize: 14, color: Colors.white),
                ),
              ],
            ),
            const SizedBox(height: 12),
            ...actions.map((act) {
              final type = act['type'] ?? '';
              final params = act['params'] ?? {};
              String label = "";
              IconData icon = Icons.check_circle_outline;
              Color color = AuraTheme.accentIndigo;

              if (type == 'add_task') {
                label = "Create Task: ${params['title']} (${params['priority']} priority, ${params['category']})";
                icon = Icons.playlist_add;
                color = AuraTheme.accentTeal;
              } else if (type == 'add_event') {
                label = "Schedule Event: ${params['title']} at ${params['startTime']}";
                icon = Icons.event;
                color = AuraTheme.accentIndigo;
              } else if (type == 'add_expense') {
                label = "Log Expense: \$${params['amount']} for ${params['category']}";
                icon = Icons.remove_circle_outline;
                color = Colors.redAccent;
              } else if (type == 'add_income') {
                label = "Log Income: \$${params['amount']} for ${params['category']}";
                icon = Icons.add_circle_outline;
                color = AuraTheme.accentTeal;
              } else if (type == 'log_health') {
                final metrics = [];
                if (params['waterIntakeMl'] != null) metrics.add("${params['waterIntakeMl']}ml water");
                if (params['sleepMinutes'] != null) metrics.add("${params['sleepMinutes']}m sleep");
                if (params['caloriesConsumed'] != null) metrics.add("${params['caloriesConsumed']}kcal");
                if (params['exerciseMinutes'] != null) metrics.add("${params['exerciseMinutes']}m exercise");
                if (params['weight'] != null) metrics.add("${params['weight']}kg");
                label = "Log Health: ${metrics.join(', ')}";
                icon = Icons.favorite_outline;
                color = Colors.pinkAccent;
              } else {
                label = "Unknown Action: $type";
              }

              return Padding(
                padding: const EdgeInsets.only(bottom: 8.0),
                child: Row(
                  children: [
                    Icon(icon, size: 16, color: color),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(label, style: const TextStyle(fontSize: 13, color: AuraTheme.textSecondary)),
                    ),
                  ],
                ),
              );
            }),
            const Divider(color: Color(0x1F94A3B8), height: 20),
            Row(
              mainAxisAlignment: MainAxisAlignment.end,
              children: [
                TextButton(
                  onPressed: () {
                    ref.read(pendingActionsProvider.notifier).clear();
                  },
                  child: const Text("Reject", style: TextStyle(color: AuraTheme.textSecondary)),
                ),
                const SizedBox(width: 8),
                ElevatedButton(
                  onPressed: () async {
                    final messenger = ScaffoldMessenger.of(context);
                    for (final act in actions) {
                      final type = act['type'] ?? '';
                      final params = act['params'] ?? {};

                      try {
                        if (type == 'add_task') {
                          DateTime? due;
                          if (params['dueDate'] != null) due = DateTime.tryParse(params['dueDate']);
                          await ref.read(taskRepositoryProvider).addTask(
                            title: params['title'] ?? 'Task',
                            priority: params['priority'] ?? 'medium',
                            category: params['category'] ?? 'personal',
                            dueDate: due,
                          );
                        } else if (type == 'add_event') {
                          final start = DateTime.tryParse(params['startTime']) ?? DateTime.now();
                          final end = params['endTime'] != null ? DateTime.tryParse(params['endTime']) : start.add(const Duration(hours: 1));
                          await ref.read(calendarRepositoryProvider).addEvent(
                            title: params['title'] ?? 'Event',
                            description: params['description'],
                            startTime: start,
                            endTime: end ?? start.add(const Duration(hours: 1)),
                          );
                        } else if (type == 'add_expense') {
                          await ref.read(financeRepositoryProvider).addTransaction(
                            type: 'expense',
                            amount: (params['amount'] as num).toDouble(),
                            category: params['category'] ?? 'Other',
                            description: params['description'],
                            date: DateTime.now(),
                          );
                        } else if (type == 'add_income') {
                          await ref.read(financeRepositoryProvider).addTransaction(
                            type: 'income',
                            amount: (params['amount'] as num).toDouble(),
                            category: params['category'] ?? 'Other',
                            description: params['description'],
                            date: DateTime.now(),
                          );
                        } else if (type == 'log_health') {
                          final now = DateTime.now();
                          final repo = ref.read(healthRepositoryProvider);
                          if (params['waterIntakeMl'] != null) {
                            await repo.logWater(now, params['waterIntakeMl'] as int);
                          }
                          if (params['sleepMinutes'] != null) {
                            await repo.logSleep(now, params['sleepMinutes'] as int);
                          }
                          if (params['caloriesConsumed'] != null) {
                            await repo.logCalories(now, params['caloriesConsumed'] as int);
                          }
                          if (params['exerciseMinutes'] != null) {
                            await repo.logExercise(now, params['exerciseMinutes'] as int);
                          }
                          if (params['weight'] != null) {
                            await repo.logWeight(now, (params['weight'] as num).toDouble());
                          }
                        }
                      } catch (e) {
                        debugPrint("Action execution failed: $e");
                      }
                    }

                    // Clear actions and show confirmation
                    ref.read(pendingActionsProvider.notifier).clear();
                    messenger.showSnackBar(
                      const SnackBar(content: Text("Proposed actions successfully executed!"), backgroundColor: AuraTheme.accentTeal),
                    );
                  },
                  style: ElevatedButton.styleFrom(
                    backgroundColor: AuraTheme.accentTeal,
                    padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
                  ),
                  child: const Text("Approve Actions", style: TextStyle(fontSize: 13)),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  void _showMemoryBankDialog() {
    final factController = TextEditingController();

    showDialog(
      context: context,
      builder: (context) {
        return Consumer(
          builder: (context, ref, child) {
            final memoriesAsync = ref.watch(localMemoriesStreamProvider);

            return Dialog(
              backgroundColor: const Color(0xFF0F0C1B),
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(16),
                side: const BorderSide(color: Color(0x1F94A3B8)),
              ),
              child: Container(
                width: 500,
                height: 500,
                padding: const EdgeInsets.all(24),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        const Row(
                          children: [
                            Icon(Icons.psychology, color: AuraTheme.accentAmber),
                            SizedBox(width: 8),
                            Text(
                              "User Memory Bank",
                              style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold, color: Colors.white),
                            ),
                          ],
                        ),
                        IconButton(
                          icon: const Icon(Icons.close, color: Colors.white30),
                          onPressed: () => Navigator.pop(context),
                        ),
                      ],
                    ),
                    const SizedBox(height: 4),
                    const Text(
                      "These are facts and preferences AURA has learned about you. They are injected as system prompts to personalize reasoning.",
                      style: TextStyle(color: AuraTheme.textSecondary, fontSize: 12),
                    ),
                    const Divider(color: Color(0x1F94A3B8), height: 24),
                    
                    // Add Fact input
                    Row(
                      children: [
                        Expanded(
                          child: TextField(
                            controller: factController,
                            decoration: const InputDecoration(
                              hintText: "Add a custom fact manually...",
                              contentPadding: EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                            ),
                            style: const TextStyle(color: Colors.white, fontSize: 13),
                          ),
                        ),
                        const SizedBox(width: 8),
                        ElevatedButton(
                          onPressed: () async {
                            final fact = factController.text.trim();
                            if (fact.isNotEmpty) {
                              await ref.read(memoryRepositoryProvider).saveMemory(fact);
                              factController.clear();
                            }
                          },
                          style: ElevatedButton.styleFrom(
                            backgroundColor: AuraTheme.accentIndigo,
                            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                          ),
                          child: const Text("Add", style: TextStyle(fontSize: 13)),
                        ),
                      ],
                    ),
                    const SizedBox(height: 16),

                    // Memories List
                    Expanded(
                      child: memoriesAsync.when(
                        data: (memories) {
                          if (memories.isEmpty) {
                            return const Center(
                              child: Text(
                                "No memories stored yet. AURA extracts them automatically from conversations, or you can add one above.",
                                textAlign: TextAlign.center,
                                style: TextStyle(color: AuraTheme.textSecondary, fontSize: 12),
                              ),
                            );
                          }
                          return ListView.builder(
                            itemCount: memories.length,
                            itemBuilder: (context, index) {
                              final mem = memories[index];
                              return Card(
                                margin: const EdgeInsets.only(bottom: 8),
                                color: const Color(0x0CFFFFFF),
                                child: Padding(
                                  padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                                  child: Row(
                                    children: [
                                      Expanded(
                                        child: Text(
                                          mem.fact,
                                          style: const TextStyle(fontSize: 13, color: Colors.white),
                                        ),
                                      ),
                                      IconButton(
                                        icon: const Icon(Icons.delete, size: 16, color: Colors.redAccent),
                                        onPressed: () {
                                          ref.read(memoryRepositoryProvider).deleteMemory(mem.id);
                                        },
                                      ),
                                    ],
                                  ),
                                ),
                              );
                            },
                          );
                        },
                        loading: () => const Center(child: CircularProgressIndicator()),
                        error: (e, _) => Center(child: Text("Error: $e", style: const TextStyle(color: Colors.redAccent))),
                      ),
                    ),
                  ],
                ),
              ),
            );
          },
        );
      },
    );
  }

  void _showAgentSettingsDialog() {
    showDialog(
      context: context,
      builder: (context) {
        return Consumer(
          builder: (context, ref, child) {
            final instructionsAsync = ref.watch(allAgentInstructionsProvider);

            return Dialog(
              backgroundColor: const Color(0xFF0F0C1B),
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(16),
                side: const BorderSide(color: Color(0x1F94A3B8)),
              ),
              child: Container(
                width: 600,
                height: 550,
                padding: const EdgeInsets.all(24),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        const Row(
                          children: [
                            Icon(Icons.settings_suggest, color: AuraTheme.accentIndigo),
                            SizedBox(width: 8),
                            Text(
                              "Agent System Prompt Configurations",
                              style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold, color: Colors.white),
                            ),
                          ],
                        ),
                        IconButton(
                          icon: const Icon(Icons.close, color: Colors.white30),
                          onPressed: () => Navigator.pop(context),
                        ),
                      ],
                    ),
                    const SizedBox(height: 4),
                    const Text(
                      "Customize the core system prompt guidelines for each of AURA's cognitive agent personas.",
                      style: TextStyle(color: AuraTheme.textSecondary, fontSize: 12),
                    ),
                    const Divider(color: Color(0x1F94A3B8), height: 24),
                    
                    Expanded(
                      child: instructionsAsync.when(
                        data: (instructionsMap) {
                          final agents = [
                            {"id": "orchestrator", "name": "Auto-Orchestrator / Classifier"},
                            {"id": "task_planner", "name": "Task Planner"},
                            {"id": "finance_advisor", "name": "Finance Advisor"},
                            {"id": "health_companion", "name": "Health Companion"},
                            {"id": "academic_coach", "name": "Academic Coach"},
                          ];

                          return ListView.builder(
                            itemCount: agents.length,
                            itemBuilder: (context, index) {
                              final agent = agents[index];
                              final agentId = agent['id']!;
                              final name = agent['name']!;

                              final defaultPrompts = {
                                "orchestrator": "You are AURA, a premium Personal AI Operating System...",
                                "task_planner": "You are AURA's Task Planner. Help prioritize tasks...",
                                "finance_advisor": "You are AURA's Finance Advisor. Analyze expenses...",
                                "health_companion": "You are AURA's Health Companion. Track health...",
                                "academic_coach": "You are AURA's Academic Coach. Assist with engineering spec development..."
                              };

                              final initialText = instructionsMap[agentId] ?? defaultPrompts[agentId] ?? "";
                              final controller = TextEditingController(text: initialText);

                              return Card(
                                margin: const EdgeInsets.only(bottom: 16),
                                color: const Color(0x0CFFFFFF),
                                child: Padding(
                                  padding: const EdgeInsets.all(16.0),
                                  child: Column(
                                    crossAxisAlignment: CrossAxisAlignment.stretch,
                                    children: [
                                      Text(
                                        name,
                                        style: const TextStyle(fontWeight: FontWeight.bold, color: Colors.white, fontSize: 14),
                                      ),
                                      const SizedBox(height: 8),
                                      TextField(
                                        controller: controller,
                                        maxLines: 3,
                                        style: const TextStyle(color: Colors.white, fontSize: 12),
                                        decoration: const InputDecoration(
                                          border: OutlineInputBorder(),
                                          contentPadding: EdgeInsets.all(8),
                                        ),
                                      ),
                                      const SizedBox(height: 8),
                                      Row(
                                        mainAxisAlignment: MainAxisAlignment.end,
                                        children: [
                                          TextButton(
                                            onPressed: () async {
                                              final messenger = ScaffoldMessenger.of(context);
                                              await ref.read(agentConfigRepositoryProvider).deleteInstruction(agentId);
                                              ref.invalidate(allAgentInstructionsProvider);
                                              messenger.showSnackBar(
                                                SnackBar(content: Text("Reset $name to default prompt.")),
                                              );
                                            },
                                            child: const Text("Reset Default", style: TextStyle(color: AuraTheme.accentAmber, fontSize: 12)),
                                          ),
                                          const SizedBox(width: 8),
                                          ElevatedButton(
                                            onPressed: () async {
                                              final text = controller.text.trim();
                                              if (text.isNotEmpty) {
                                                final messenger = ScaffoldMessenger.of(context);
                                                await ref.read(agentConfigRepositoryProvider).saveInstruction(agentId, text);
                                                ref.invalidate(allAgentInstructionsProvider);
                                                messenger.showSnackBar(
                                                  SnackBar(content: Text("Saved custom instructions for $name.")),
                                                );
                                              }
                                            },
                                            style: ElevatedButton.styleFrom(
                                              backgroundColor: AuraTheme.accentTeal,
                                              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                                            ),
                                            child: const Text("Save Custom", style: TextStyle(fontSize: 12)),
                                          ),
                                        ],
                                      ),
                                    ],
                                  ),
                                ),
                              );
                            },
                          );
                        },
                        loading: () => const Center(child: CircularProgressIndicator()),
                        error: (e, _) => Center(child: Text("Error loading configurations: $e")),
                      ),
                    ),
                  ],
                ),
              ),
            );
          },
        );
      },
    );
  }
}

// ----------------------------------------------------
// Message Bubble Widget
// ----------------------------------------------------
class MessageBubble extends ConsumerWidget {
  final Message message;
  final bool isUser;
  const MessageBubble({super.key, required this.message, required this.isUser});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final formattedTime = DateFormat('jm').format(message.createdAt);

    if (!isUser && message.content.startsWith("Connection Error")) {
      return Align(
        alignment: Alignment.centerLeft,
        child: Container(
          margin: const EdgeInsets.only(bottom: 16),
          constraints: BoxConstraints(
            maxWidth: MediaQuery.of(context).size.width * (MediaQuery.of(context).size.width < 768 ? 0.85 : 0.65),
          ),
          padding: const EdgeInsets.all(16),
          decoration: BoxDecoration(
            color: const Color(0xFF2D1616),
            borderRadius: BorderRadius.circular(16),
            border: Border.all(color: Colors.redAccent.withValues(alpha: 0.5), width: 1),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Row(
                children: [
                  Icon(Icons.cloud_off, color: Colors.redAccent, size: 20),
                  SizedBox(width: 8),
                  Text(
                    "Cloud AI is currently unavailable",
                    style: TextStyle(fontWeight: FontWeight.bold, color: Colors.white, fontSize: 14),
                  ),
                ],
              ),
              const SizedBox(height: 8),
              const Text(
                "AURA could not connect to the cloud assistant. You can retry, switch to a local offline model, or report this issue.",
                style: TextStyle(color: AuraTheme.textSecondary, fontSize: 13, height: 1.4),
              ),
              const SizedBox(height: 12),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  ElevatedButton.icon(
                    onPressed: () async {
                      final db = ref.read(databaseProvider);
                      final activeSessionId = ref.read(activeConversationIdProvider);
                      final messages = await (db.select(db.messages)
                            ..where((m) => m.conversationId.equals(activeSessionId))
                            ..orderBy([(m) => OrderingTerm(expression: m.createdAt, mode: OrderingMode.asc)]))
                          .get();
                      
                      final userMessages = messages.where((m) => m.role == "user").toList();
                      if (userMessages.isNotEmpty) {
                        final lastUserMsg = userMessages.last;
                        // Delete the failed assistant message
                        await (db.delete(db.messages)..where((m) => m.id.equals(message.id))).go();
                        // Delete the last user message so sendChatMessage can recreate it cleanly
                        await (db.delete(db.messages)..where((m) => m.id.equals(lastUserMsg.id))).go();
                        
                        // Resend
                        await ref.read(chatRepositoryProvider).sendChatMessage(
                          conversationId: activeSessionId,
                          userContent: lastUserMsg.content,
                          useRag: true,
                        );
                      }
                    },
                    icon: const Icon(Icons.refresh, size: 14),
                    label: const Text("Retry"),
                    style: ElevatedButton.styleFrom(
                      backgroundColor: AuraTheme.accentIndigo,
                      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                    ),
                  ),
                  OutlinedButton.icon(
                    onPressed: () {
                      ScaffoldMessenger.of(context).showSnackBar(
                        const SnackBar(
                          content: Text("Issue reported successfully to developers."),
                          backgroundColor: AuraTheme.accentTeal,
                        ),
                      );
                    },
                    icon: const Icon(Icons.bug_report_outlined, size: 14, color: Colors.white70),
                    label: const Text("Report Issue", style: TextStyle(color: Colors.white70)),
                    style: OutlinedButton.styleFrom(
                      side: const BorderSide(color: Colors.white24),
                      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
      );
    }

    return Align(
      alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.only(bottom: 16),
        constraints: BoxConstraints(
          maxWidth: MediaQuery.of(context).size.width * (MediaQuery.of(context).size.width < 768 ? 0.75 : 0.55),
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
  final Function(String) onSuggestionSelected;
  const EmptyStateChat({super.key, required this.onSuggestionSelected});

  @override
  Widget build(BuildContext context) {
    return Center(
      child: SingleChildScrollView(
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
                Icons.auto_awesome,
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
              'Ask programming questions, academic inquiries,\nor ask it to sync schedules and coordinate tasks.',
              textAlign: TextAlign.center,
              style: TextStyle(
                fontSize: 14,
                color: AuraTheme.textSecondary,
                height: 1.4,
              ),
            ),
            const SizedBox(height: 32),
            ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 500),
              child: GridView.count(
                shrinkWrap: true,
                crossAxisCount: MediaQuery.of(context).size.width < 500 ? 1 : 2,
                crossAxisSpacing: 12,
                mainAxisSpacing: 12,
                childAspectRatio: MediaQuery.of(context).size.width < 500 ? 3.8 : 2.2,
                physics: const NeverScrollableScrollPhysics(),
                children: [
                  _buildSuggestionCard(
                    context,
                    "Sync Tasks",
                    "Synchronize my task database with Google Tasks",
                    "Sync my Google Tasks",
                  ),
                  _buildSuggestionCard(
                    context,
                    "Log Health",
                    "Log water intake and exercise duration for today",
                    "Log 500ml of water and 30 minutes of exercise",
                  ),
                  _buildSuggestionCard(
                    context,
                    "Financial Health",
                    "Show my current balance and expense distribution",
                    "How are my expenses this month?",
                  ),
                  _buildSuggestionCard(
                    context,
                    "Agent Memory",
                    "View facts the system has remembered about me",
                    "What do you remember about me?",
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildSuggestionCard(
    BuildContext context,
    String title,
    String subtitle,
    String prompt,
  ) {
    return Card(
      margin: EdgeInsets.zero,
      child: InkWell(
        onTap: () => onSuggestionSelected(prompt),
        borderRadius: BorderRadius.circular(16),
        child: Padding(
          padding: const EdgeInsets.all(14.0),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Text(
                title,
                style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 13, color: Colors.white),
              ),
              const SizedBox(height: 4),
              Text(
                subtitle,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(fontSize: 11, color: AuraTheme.textSecondary),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
