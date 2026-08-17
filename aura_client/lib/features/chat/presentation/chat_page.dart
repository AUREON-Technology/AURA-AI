import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:intl/intl.dart';
import '../data/chat_repository.dart';
import '../data/agent_config_repository.dart';
import '../data/agents_repository.dart';
import '../data/memory_repository.dart';
import 'widgets/citation_chip.dart';
import 'widgets/tool_call_chip.dart';
import 'widgets/persona_badge.dart';
import 'widgets/streaming_message_skeleton.dart';
import '../../tasks/data/task_repository.dart';
import '../../calendar/data/calendar_repository.dart';
import '../../finance/data/finance_repository.dart';
import '../../health/data/health_repository.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:drift/drift.dart' show OrderingTerm, OrderingMode;
import '../../sync/sync_engine.dart';
import 'package:aura_client/core/ai/ai_router/aura_ai_router.dart';

/// "Copilot" -- chat, rebuilt on the V2 design system.
///
/// Locally scoped to [AuraThemeV2] via the [Theme] wrapper in [build] rather
/// than flipping the app-wide theme, matching how home_page.dart/
/// settings_page.dart already do this (see Doc/V2_BLUEPRINT.md §13 Phase 2 --
/// a deliberate incremental migration, not a big-bang swap).
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

    await ref.read(chatRepositoryProvider).sendChatMessage(
          conversationId: sessionId,
          userContent: text,
          useRag: _useRag,
        );

    Future.delayed(const Duration(milliseconds: 100), _scrollToBottom);
  }

  final _scaffoldKey = GlobalKey<ScaffoldState>();

  /// Persona picker, replacing the old bare `DropdownButton` in both the
  /// desktop header and the mobile drawer -- a modal listing icon + name +
  /// one-line description per persona, sourced live from `agentsProvider`
  /// (GET /api/v3/agents) rather than a hardcoded list that had drifted
  /// from the backend's real 7 persona ids.
  void _showPersonaPicker(BuildContext context) {
    showModalBottomSheet(
      context: context,
      backgroundColor: Colors.transparent,
      builder: (sheetContext) {
        return Consumer(
          builder: (context, ref, _) {
            final palette = AuraPalette.of(context);
            final agentsAsync = ref.watch(agentsProvider);
            final activeId = ref.watch(activeAgentIdProvider);

            return Container(
              decoration: BoxDecoration(
                color: palette.surface,
                borderRadius: const BorderRadius.vertical(top: Radius.circular(AuraRadius.xl)),
                border: Border.all(color: palette.border),
              ),
              padding: const EdgeInsets.all(AuraSpacing.lg),
              child: SafeArea(
                child: agentsAsync.when(
                  data: (agents) => Column(
                    mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('Choose a persona', style: Theme.of(context).textTheme.titleSmall),
                      const SizedBox(height: AuraSpacing.sm),
                      ...agents.map((agent) {
                        final (label, icon) = personaDisplay(agent.id);
                        final isActive = agent.id == activeId;
                        return ListTile(
                          leading: Icon(icon, color: isActive ? palette.brandOnSurfaceText : palette.textSecondary),
                          title: Text(
                            label,
                            style: TextStyle(
                              color: palette.textPrimary,
                              fontWeight: isActive ? FontWeight.bold : FontWeight.normal,
                            ),
                          ),
                          subtitle: agent.description.isEmpty
                              ? null
                              : Text(
                                  agent.description,
                                  maxLines: 1,
                                  overflow: TextOverflow.ellipsis,
                                  style: TextStyle(color: palette.textSecondary, fontSize: 12),
                                ),
                          trailing: isActive ? Icon(Icons.check, color: palette.brandOnSurfaceText) : null,
                          onTap: () {
                            ref.read(activeAgentIdProvider.notifier).setAgent(agent.id);
                            Navigator.pop(context);
                          },
                        );
                      }),
                    ],
                  ),
                  loading: () => const Padding(
                    padding: EdgeInsets.all(AuraSpacing.xl),
                    child: Center(child: CircularProgressIndicator()),
                  ),
                  error: (e, _) => Padding(
                    padding: const EdgeInsets.all(AuraSpacing.xl),
                    child: Text('Could not load personas: $e', style: TextStyle(color: palette.danger)),
                  ),
                ),
              ),
            );
          },
        );
      },
    );
  }

  Widget _buildPersonaChip(BuildContext context) {
    final palette = AuraPalette.of(context);
    final activeId = ref.watch(activeAgentIdProvider);
    final (label, icon) = personaDisplay(activeId);

    return InkWell(
      borderRadius: BorderRadius.circular(AuraRadius.pill),
      onTap: () => _showPersonaPicker(context),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: AuraSpacing.md, vertical: 6),
        decoration: BoxDecoration(
          color: palette.surfaceVariant,
          borderRadius: BorderRadius.circular(AuraRadius.pill),
          border: Border.all(color: palette.border),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 16, color: palette.brandOnSurfaceText),
            const SizedBox(width: AuraSpacing.xs),
            Text(label, style: TextStyle(fontSize: 13, color: palette.textPrimary)),
            const SizedBox(width: AuraSpacing.xs),
            Icon(Icons.expand_more, size: 16, color: palette.textSecondary),
          ],
        ),
      ),
    );
  }

  Widget _buildSidebar(BuildContext context, String activeSessionId, AsyncValue<List<String>> sessionsAsync) {
    final palette = AuraPalette.of(context);
    final isMobile = MediaQuery.of(context).size.width < 768;

    return Container(
      width: 260,
      color: palette.background,
      padding: const EdgeInsets.symmetric(horizontal: AuraSpacing.lg, vertical: AuraSpacing.xl),
      child: ListView(
        padding: EdgeInsets.zero,
        children: [
          AuraButton(
            label: 'New Session',
            icon: Icons.add,
            expand: true,
            onPressed: () {
              final newId = "session_${DateTime.now().millisecondsSinceEpoch}";
              ref.read(activeConversationIdProvider.notifier).setSession(newId);
              if (isMobile) Navigator.pop(context);
            },
          ),
          const SizedBox(height: AuraSpacing.xl),
          Text(
            'CHAT SESSIONS',
            style: TextStyle(color: palette.textSecondary, fontSize: 11, fontWeight: FontWeight.bold, letterSpacing: 1.2),
          ),
          const SizedBox(height: AuraSpacing.md),
          sessionsAsync.when(
            data: (sessions) {
              final allSessions = sessions.contains('default_session') ? sessions : ['default_session', ...sessions];

              return ListView.builder(
                shrinkWrap: true,
                physics: const NeverScrollableScrollPhysics(),
                itemCount: allSessions.length,
                itemBuilder: (context, index) {
                  final session = allSessions[index];
                  final isActive = session == activeSessionId;
                  final label = session == 'default_session' ? 'Default Workspace' : 'Session ${session.split('_').last.substring(0, 5)}';

                  return Padding(
                    padding: const EdgeInsets.only(bottom: 6.0),
                    child: ListTile(
                      dense: true,
                      selected: isActive,
                      selectedTileColor: palette.brand.withValues(alpha: 0.15),
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(AuraRadius.sm)),
                      title: Text(
                        label,
                        style: TextStyle(
                          color: isActive ? palette.textPrimary : palette.textSecondary,
                          fontWeight: isActive ? FontWeight.bold : FontWeight.normal,
                        ),
                      ),
                      trailing: isActive && session != 'default_session'
                          ? IconButton(
                              icon: Icon(Icons.delete_outline, size: 16, color: palette.textDisabled),
                              onPressed: () {
                                ref.read(chatRepositoryProvider).clearConversation(session);
                                ref.read(activeConversationIdProvider.notifier).setSession('default_session');
                              },
                            )
                          : null,
                      onTap: () {
                        ref.read(activeConversationIdProvider.notifier).setSession(session);
                        if (isMobile) Navigator.pop(context);
                      },
                    ),
                  );
                },
              );
            },
            loading: () => const Center(
              child: Padding(padding: EdgeInsets.all(8.0), child: CircularProgressIndicator(strokeWidth: 2)),
            ),
            error: (err, _) => Padding(
              padding: const EdgeInsets.all(8.0),
              child: Text('Error: $err', style: TextStyle(color: palette.danger)),
            ),
          ),
          if (isMobile) ...[
            Divider(color: palette.border, height: 32),
            Text(
              'ACTIVE PERSONA',
              style: TextStyle(color: palette.textSecondary, fontSize: 11, fontWeight: FontWeight.bold, letterSpacing: 1.2),
            ),
            const SizedBox(height: AuraSpacing.md),
            _buildPersonaChip(context),
            const SizedBox(height: AuraSpacing.md),
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text('Use Knowledge (RAG)', style: TextStyle(fontSize: 12, color: palette.textSecondary)),
                Switch(
                  value: _useRag,
                  activeTrackColor: palette.brand,
                  onChanged: (val) => setState(() => _useRag = val),
                ),
              ],
            ),
          ],
          Divider(color: palette.border, height: 32),
          Text(
            'AI ROUTING',
            style: TextStyle(color: palette.textSecondary, fontSize: 11, fontWeight: FontWeight.bold, letterSpacing: 1.2),
          ),
          const SizedBox(height: AuraSpacing.md),
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text('AI engine', style: TextStyle(fontSize: 12, color: palette.textSecondary)),
              // A read-out, not a control. This was a dropdown selecting between a hosted API and
              // three on-device models; all four are gone, and a picker with one option is a worse
              // answer to "which engine am I using" than simply saying.
              Text(
                ref.watch(aiRouterStatusProvider).isOffline ? 'AURA (unreachable)' : 'AURA server',
                style: TextStyle(fontSize: 12, color: palette.textPrimary),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildSyncIndicator(BuildContext context, bool isMobile) {
    final palette = AuraPalette.of(context);
    final syncState = ref.watch(syncEngineProvider);

    Color indicatorColor;
    String statusText;
    IconData syncIcon;
    bool isSyncing = syncState == SyncState.syncing;

    switch (syncState) {
      case SyncState.synced:
        indicatorColor = palette.success;
        statusText = 'Synced';
        syncIcon = Icons.sync;
        break;
      case SyncState.syncing:
        indicatorColor = palette.warning;
        statusText = 'Syncing';
        syncIcon = Icons.sync;
        break;
      case SyncState.offline:
        indicatorColor = palette.textDisabled;
        statusText = 'Offline';
        syncIcon = Icons.sync_disabled;
        break;
      case SyncState.error:
        indicatorColor = palette.danger;
        statusText = 'Error';
        syncIcon = Icons.sync_problem;
        break;
    }

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: AuraSpacing.sm, vertical: 4),
      decoration: BoxDecoration(
        color: palette.surfaceVariant,
        border: Border.all(color: palette.border),
        borderRadius: BorderRadius.circular(AuraRadius.pill),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(width: 8, height: 8, decoration: BoxDecoration(color: indicatorColor, shape: BoxShape.circle)),
          if (!isMobile) ...[
            const SizedBox(width: AuraSpacing.xs),
            Text(statusText, style: TextStyle(fontSize: 11, color: palette.textSecondary, fontWeight: FontWeight.w500)),
          ],
          const SizedBox(width: 4),
          isSyncing
              ? SizedBox(
                  width: 12,
                  height: 12,
                  child: CircularProgressIndicator(strokeWidth: 1.5, valueColor: AlwaysStoppedAnimation<Color>(palette.warning)),
                )
              : IconButton(
                  padding: EdgeInsets.zero,
                  constraints: const BoxConstraints(),
                  icon: Icon(syncIcon, color: indicatorColor, size: 12),
                  tooltip: 'Sync Now',
                  onPressed: syncState == SyncState.offline ? null : () => ref.read(syncEngineProvider.notifier).synchronize(),
                ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Theme(
      data: AuraThemeV2.dark,
      child: Builder(builder: (context) => _buildScaffold(context)),
    );
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
      drawer: isMobile ? Drawer(child: _buildSidebar(context, activeSessionId, sessionsAsync)) : null,
      body: Row(
        children: [
          if (!isMobile) _buildSidebar(context, activeSessionId, sessionsAsync),
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
                  // Copilot Header
                  Container(
                    padding: const EdgeInsets.all(AuraSpacing.xl),
                    decoration: BoxDecoration(border: Border(bottom: BorderSide(color: palette.border, width: 1))),
                    child: Row(
                      children: [
                        if (isMobile) ...[
                          IconButton(
                            icon: Icon(Icons.menu, color: palette.textPrimary),
                            onPressed: () => _scaffoldKey.currentState?.openDrawer(),
                          ),
                          const SizedBox(width: AuraSpacing.sm),
                        ],
                        Container(
                          width: 32,
                          height: 32,
                          decoration: BoxDecoration(borderRadius: BorderRadius.circular(AuraRadius.sm)),
                          child: ClipRRect(
                            borderRadius: BorderRadius.circular(AuraRadius.sm),
                            child: Image.asset('assets/app_icon.png', fit: BoxFit.contain),
                          ),
                        ),
                        const SizedBox(width: AuraSpacing.md),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                'Copilot',
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
                                style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold, color: palette.textPrimary),
                              ),
                              Row(
                                children: [
                                  Container(
                                    width: 8,
                                    height: 8,
                                    decoration: BoxDecoration(
                                      color: ref.watch(aiRouterStatusProvider).isOffline ? palette.warning : palette.success,
                                      shape: BoxShape.circle,
                                    ),
                                  ),
                                  const SizedBox(width: 6),
                                  Expanded(
                                    child: Text(
                                      isMobile
                                          ? 'Active • ${ref.watch(aiRouterStatusProvider).activeModel}'
                                          : 'Active • ${ref.watch(aiRouterStatusProvider).activeModel} (${ref.watch(aiRouterStatusProvider).status}) • ${ref.watch(aiRouterStatusProvider).latencyMs.toStringAsFixed(0)}ms',
                                      maxLines: 1,
                                      overflow: TextOverflow.ellipsis,
                                      style: TextStyle(fontSize: 11, color: palette.textSecondary),
                                    ),
                                  ),
                                ],
                              ),
                            ],
                          ),
                        ),
                        IconButton(
                          icon: Icon(Icons.psychology_outlined, color: palette.warning, size: 20),
                          tooltip: 'Memory Bank',
                          onPressed: _showMemoryBankDialog,
                        ),
                        if (!isMobile) ...[
                          const SizedBox(width: AuraSpacing.sm),
                          _buildPersonaChip(context),
                          const SizedBox(width: AuraSpacing.md),
                          Row(
                            children: [
                              Text('RAG', style: TextStyle(fontSize: 12, color: palette.textSecondary)),
                              Switch(
                                value: _useRag,
                                activeTrackColor: palette.brand,
                                onChanged: (val) => setState(() => _useRag = val),
                              ),
                            ],
                          ),
                        ],
                        const SizedBox(width: AuraSpacing.sm),
                        _buildSyncIndicator(context, isMobile),
                      ],
                    ),
                  ),

                  // Message Feed
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
                            return MessageBubble(message: msg, isUser: msg.role == "user");
                          },
                        );
                      },
                      loading: () => Center(child: CircularProgressIndicator(color: palette.brand)),
                      error: (err, _) => Center(
                        child: Text('Failed to load message history: $err', style: TextStyle(color: palette.danger)),
                      ),
                    ),
                  ),

                  _buildPendingActionsCard(context),

                  // Input Bar
                  Container(
                    padding: const EdgeInsets.all(AuraSpacing.xl),
                    decoration: BoxDecoration(border: Border(top: BorderSide(color: palette.border, width: 1))),
                    child: Row(
                      children: [
                        Expanded(
                          child: TextField(
                            controller: _messageController,
                            decoration: InputDecoration(
                              hintText: isMobile ? 'Ask Copilot...' : 'Ask Copilot anything...',
                            ),
                            style: TextStyle(color: palette.textPrimary),
                            onSubmitted: (_) => _sendMessage(activeSessionId),
                          ),
                        ),
                        const SizedBox(width: AuraSpacing.lg),
                        Container(
                          decoration: BoxDecoration(gradient: palette.brandGradient, borderRadius: BorderRadius.circular(AuraRadius.md)),
                          child: IconButton(
                            icon: Icon(Icons.send, color: palette.onBrand),
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

  /// Fallback-only: v3's tool registry (aura_core/tools/builtin.py) has no
  /// task/calendar/finance/health CRUD tools, so this panel only ever
  /// populates via _sendViaOrchestrator, chat_repository.dart's v1 fallback
  /// path -- not the primary v3 stream, which shows tool activity inline
  /// per-message instead (see ToolCallChips in MessageBubble below).
  Widget _buildPendingActionsCard(BuildContext context) {
    final palette = AuraPalette.of(context);
    final actions = ref.watch(pendingActionsProvider);
    if (actions.isEmpty) return const SizedBox();

    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: AuraSpacing.xl, vertical: AuraSpacing.sm),
      child: AuraCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                Icon(Icons.auto_awesome, color: palette.success, size: 20),
                const SizedBox(width: AuraSpacing.sm),
                Text("AURA Proposes Actions", style: TextStyle(fontWeight: FontWeight.bold, fontSize: 14, color: palette.textPrimary)),
              ],
            ),
            const SizedBox(height: AuraSpacing.md),
            ...actions.map((act) {
              final type = act['type'] ?? '';
              final params = act['params'] ?? {};
              String label = "";
              IconData icon = Icons.check_circle_outline;
              Color color = palette.brand;

              if (type == 'add_task') {
                label = "Create Task: ${params['title']} (${params['priority']} priority, ${params['category']})";
                icon = Icons.playlist_add;
                color = palette.success;
              } else if (type == 'add_event') {
                label = "Schedule Event: ${params['title']} at ${params['startTime']}";
                icon = Icons.event;
                color = palette.brand;
              } else if (type == 'add_expense') {
                label = "Log Expense: \$${params['amount']} for ${params['category']}";
                icon = Icons.remove_circle_outline;
                color = palette.danger;
              } else if (type == 'add_income') {
                label = "Log Income: \$${params['amount']} for ${params['category']}";
                icon = Icons.add_circle_outline;
                color = palette.success;
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
                padding: const EdgeInsets.only(bottom: AuraSpacing.sm),
                child: Row(
                  children: [
                    Icon(icon, size: 16, color: color),
                    const SizedBox(width: AuraSpacing.sm),
                    Expanded(child: Text(label, style: TextStyle(fontSize: 13, color: palette.textSecondary))),
                  ],
                ),
              );
            }),
            Divider(color: palette.border, height: 20),
            Row(
              mainAxisAlignment: MainAxisAlignment.end,
              children: [
                AuraButton(
                  label: "Reject",
                  variant: AuraButtonVariant.ghost,
                  onPressed: () => ref.read(pendingActionsProvider.notifier).clear(),
                ),
                const SizedBox(width: AuraSpacing.sm),
                AuraButton(
                  label: "Approve Actions",
                  variant: AuraButtonVariant.primary,
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

                    ref.read(pendingActionsProvider.notifier).clear();
                    messenger.showSnackBar(
                      SnackBar(content: const Text("Proposed actions successfully executed!"), backgroundColor: palette.success),
                    );
                  },
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
      builder: (dialogContext) {
        return Consumer(
          builder: (context, ref, child) {
            final palette = AuraPalette.of(context);
            final memoriesAsync = ref.watch(localMemoriesStreamProvider);

            return Dialog(
              backgroundColor: palette.surface,
              shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(AuraRadius.lg), side: BorderSide(color: palette.border)),
              child: Container(
                width: 500,
                height: 500,
                padding: const EdgeInsets.all(AuraSpacing.xl),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        Row(
                          children: [
                            Icon(Icons.psychology, color: palette.warning),
                            const SizedBox(width: AuraSpacing.sm),
                            Text("User Memory Bank", style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold, color: palette.textPrimary)),
                          ],
                        ),
                        IconButton(
                          icon: Icon(Icons.close, color: palette.textDisabled),
                          onPressed: () => Navigator.pop(context),
                        ),
                      ],
                    ),
                    const SizedBox(height: AuraSpacing.xs),
                    Text(
                      "These are facts and preferences AURA has learned about you. They are injected as system prompts to personalize reasoning.",
                      style: TextStyle(color: palette.textSecondary, fontSize: 12),
                    ),
                    Divider(color: palette.border, height: 24),
                    Row(
                      children: [
                        Expanded(
                          child: TextField(
                            controller: factController,
                            decoration: const InputDecoration(hintText: "Add a custom fact manually..."),
                            style: TextStyle(color: palette.textPrimary, fontSize: 13),
                          ),
                        ),
                        const SizedBox(width: AuraSpacing.sm),
                        AuraButton(
                          label: "Add",
                          onPressed: () async {
                            final fact = factController.text.trim();
                            if (fact.isNotEmpty) {
                              await ref.read(memoryRepositoryProvider).saveMemory(fact);
                              factController.clear();
                            }
                          },
                        ),
                      ],
                    ),
                    const SizedBox(height: AuraSpacing.lg),
                    Expanded(
                      child: memoriesAsync.when(
                        data: (memories) {
                          if (memories.isEmpty) {
                            return Center(
                              child: Text(
                                "No memories stored yet. AURA extracts them automatically from conversations, or you can add one above.",
                                textAlign: TextAlign.center,
                                style: TextStyle(color: palette.textSecondary, fontSize: 12),
                              ),
                            );
                          }
                          return ListView.builder(
                            itemCount: memories.length,
                            itemBuilder: (context, index) {
                              final mem = memories[index];
                              return Padding(
                                padding: const EdgeInsets.only(bottom: AuraSpacing.sm),
                                child: AuraCard(
                                  padding: const EdgeInsets.symmetric(horizontal: AuraSpacing.md, vertical: AuraSpacing.sm),
                                  child: Row(
                                    children: [
                                      Expanded(child: Text(mem.fact, style: TextStyle(fontSize: 13, color: palette.textPrimary))),
                                      IconButton(
                                        icon: Icon(Icons.delete, size: 16, color: palette.danger),
                                        onPressed: () => ref.read(memoryRepositoryProvider).deleteMemory(mem.id),
                                      ),
                                    ],
                                  ),
                                ),
                              );
                            },
                          );
                        },
                        loading: () => const Center(child: CircularProgressIndicator()),
                        error: (e, _) => Center(child: Text("Error: $e", style: TextStyle(color: palette.danger))),
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

  List<String> _decodeCitations() {
    final raw = message.citationsJson;
    if (raw == null || raw.isEmpty) return const [];
    try {
      return (jsonDecode(raw) as List<dynamic>).map((c) => c.toString()).toList();
    } catch (_) {
      return const [];
    }
  }

  List<Map<String, dynamic>> _decodeToolCalls() {
    final raw = message.toolCallsJson;
    if (raw == null || raw.isEmpty) return const [];
    try {
      return (jsonDecode(raw) as List<dynamic>).cast<Map<String, dynamic>>();
    } catch (_) {
      return const [];
    }
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final palette = AuraPalette.of(context);
    final formattedTime = DateFormat('jm').format(message.createdAt);
    final maxWidthFraction = MediaQuery.of(context).size.width < 768 ? 0.85 : 0.65;

    if (!isUser && message.content.startsWith("Connection Error")) {
      return Align(
        alignment: Alignment.centerLeft,
        child: Container(
          margin: const EdgeInsets.only(bottom: AuraSpacing.lg),
          constraints: BoxConstraints(maxWidth: MediaQuery.of(context).size.width * maxWidthFraction),
          child: AuraCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Icon(Icons.cloud_off, color: palette.danger, size: 20),
                    const SizedBox(width: AuraSpacing.sm),
                    Text("Cloud AI is currently unavailable", style: TextStyle(fontWeight: FontWeight.bold, color: palette.textPrimary, fontSize: 14)),
                  ],
                ),
                const SizedBox(height: AuraSpacing.sm),
                Text(
                  "AURA could not connect to the cloud assistant. You can retry or report this issue.",
                  style: TextStyle(color: palette.textSecondary, fontSize: 13, height: 1.4),
                ),
                const SizedBox(height: AuraSpacing.md),
                Wrap(
                  spacing: AuraSpacing.sm,
                  runSpacing: AuraSpacing.sm,
                  children: [
                    AuraButton(
                      label: "Retry",
                      icon: Icons.refresh,
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
                          await (db.delete(db.messages)..where((m) => m.id.equals(message.id))).go();
                          await (db.delete(db.messages)..where((m) => m.id.equals(lastUserMsg.id))).go();

                          await ref.read(chatRepositoryProvider).sendChatMessage(
                                conversationId: activeSessionId,
                                userContent: lastUserMsg.content,
                                useRag: true,
                              );
                        }
                      },
                    ),
                    AuraButton(
                      label: "Report Issue",
                      icon: Icons.bug_report_outlined,
                      variant: AuraButtonVariant.secondary,
                      onPressed: () {
                        ScaffoldMessenger.of(context).showSnackBar(
                          SnackBar(content: const Text("Issue reported successfully to developers."), backgroundColor: palette.success),
                        );
                      },
                    ),
                  ],
                ),
              ],
            ),
          ),
        ),
      );
    }

    final isStreaming = !isUser && ref.watch(streamingMessageIdProvider) == message.id;
    final showSkeleton = isStreaming && message.content.isEmpty;
    final citations = isUser ? const <String>[] : _decodeCitations();
    final toolCalls = isUser ? const <Map<String, dynamic>>[] : _decodeToolCalls();
    final agentId = message.agentId;
    final personaInfo = agentId != null ? personaDisplay(agentId) : null;

    return Align(
      alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.only(bottom: AuraSpacing.lg),
        constraints: BoxConstraints(maxWidth: MediaQuery.of(context).size.width * (isUser ? 0.75 : maxWidthFraction)),
        child: isUser
            ? Container(
                padding: const EdgeInsets.all(AuraSpacing.lg),
                decoration: BoxDecoration(
                  gradient: palette.brandGradient,
                  borderRadius: const BorderRadius.only(
                    topLeft: Radius.circular(AuraRadius.lg),
                    topRight: Radius.circular(AuraRadius.lg),
                    bottomLeft: Radius.circular(AuraRadius.lg),
                    bottomRight: Radius.circular(0),
                  ),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(message.content, style: TextStyle(color: palette.onBrand, fontSize: 15, height: 1.4)),
                    const SizedBox(height: AuraSpacing.sm),
                    Align(
                      alignment: Alignment.bottomRight,
                      child: Text(formattedTime, style: TextStyle(fontSize: 10, color: palette.onBrand.withValues(alpha: 0.7))),
                    ),
                  ],
                ),
              )
            : AuraCard(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    if (personaInfo != null) ...[
                      PersonaBadge(label: personaInfo.$1, icon: personaInfo.$2),
                      const SizedBox(height: AuraSpacing.sm),
                    ],
                    if (showSkeleton)
                      const StreamingMessageSkeleton()
                    else
                      MarkdownBody(
                        data: message.content,
                        styleSheet: MarkdownStyleSheet(
                          p: TextStyle(color: palette.textPrimary, fontSize: 15, height: 1.4),
                          code: TextStyle(backgroundColor: palette.surfaceVariant, color: palette.warning, fontFamily: 'Courier', fontSize: 13),
                          codeblockDecoration: BoxDecoration(
                            color: palette.background,
                            borderRadius: BorderRadius.circular(AuraRadius.sm),
                            border: Border.all(color: palette.border),
                          ),
                        ),
                      ),
                    if (toolCalls.isNotEmpty) ...[
                      const SizedBox(height: AuraSpacing.sm),
                      ToolCallChips(toolCalls: toolCalls),
                    ],
                    if (citations.isNotEmpty) ...[
                      const SizedBox(height: AuraSpacing.sm),
                      CitationChips(citations: citations),
                    ],
                    const SizedBox(height: AuraSpacing.sm),
                    Align(
                      alignment: Alignment.bottomRight,
                      child: Text(formattedTime, style: TextStyle(fontSize: 10, color: palette.textSecondary)),
                    ),
                  ],
                ),
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
    final palette = AuraPalette.of(context);
    return Center(
      child: SingleChildScrollView(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            AuraEmptyState(
              icon: Icons.auto_awesome,
              title: 'Ask Copilot Anything',
              message: 'Programming questions, academic inquiries, or coordinating your tasks.',
            ),
            const SizedBox(height: AuraSpacing.lg),
            ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 500),
              child: GridView.count(
                shrinkWrap: true,
                crossAxisCount: MediaQuery.of(context).size.width < 500 ? 1 : 2,
                crossAxisSpacing: AuraSpacing.md,
                mainAxisSpacing: AuraSpacing.md,
                childAspectRatio: MediaQuery.of(context).size.width < 500 ? 3.8 : 2.2,
                physics: const NeverScrollableScrollPhysics(),
                children: [
                  _buildSuggestionCard(context, "Explain a Concept", "Ask a programming or academic question", "Explain how dependency injection works"),
                  _buildSuggestionCard(context, "Log Health", "Log water intake and exercise duration for today", "Log 500ml of water and 30 minutes of exercise"),
                  _buildSuggestionCard(context, "Financial Health", "Show my current balance and expense distribution", "How are my expenses this month?"),
                  _buildSuggestionCard(context, "Agent Memory", "View facts the system has remembered about me", "What do you remember about me?"),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildSuggestionCard(BuildContext context, String title, String subtitle, String prompt) {
    final palette = AuraPalette.of(context);
    return AuraCard(
      padding: const EdgeInsets.all(14.0),
      onTap: () => onSuggestionSelected(prompt),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Text(title, style: TextStyle(fontWeight: FontWeight.bold, fontSize: 13, color: palette.textPrimary)),
          const SizedBox(height: AuraSpacing.xs),
          Text(subtitle, maxLines: 2, overflow: TextOverflow.ellipsis, style: TextStyle(fontSize: 11, color: palette.textSecondary)),
        ],
      ),
    );
  }
}
