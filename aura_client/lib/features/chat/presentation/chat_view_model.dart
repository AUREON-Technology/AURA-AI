import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../calendar/data/calendar_repository.dart';
import '../../finance/data/finance_repository.dart';
import '../../health/data/health_repository.dart';
import '../../sync/sync_engine.dart';
import '../../tasks/data/task_repository.dart';
import '../data/chat_repository.dart';
import 'pending_actions_executor.dart';

@immutable
class ChatUiState {
  final bool useRag;
  final bool useMemory;
  final bool useTools;
  final bool sendInFlight;
  final bool consumedInitialQuery;

  const ChatUiState({
    this.useRag = true,
    this.useMemory = true,
    this.useTools = true,
    this.sendInFlight = false,
    this.consumedInitialQuery = false,
  });

  bool composerLocked({required bool isStreaming}) => sendInFlight || isStreaming;

  bool canSend(String text, {required bool isStreaming}) {
    if (composerLocked(isStreaming: isStreaming)) return false;
    return text.trim().isNotEmpty;
  }

  bool canConsumeInitialQuery(String? query) {
    if (consumedInitialQuery) return false;
    final trimmed = query?.trim();
    return trimmed != null && trimmed.isNotEmpty;
  }

  ChatUiState copyWith({
    bool? useRag,
    bool? useMemory,
    bool? useTools,
    bool? sendInFlight,
    bool? consumedInitialQuery,
  }) {
    return ChatUiState(
      useRag: useRag ?? this.useRag,
      useMemory: useMemory ?? this.useMemory,
      useTools: useTools ?? this.useTools,
      sendInFlight: sendInFlight ?? this.sendInFlight,
      consumedInitialQuery: consumedInitialQuery ?? this.consumedInitialQuery,
    );
  }
}

class ChatViewModel extends Notifier<ChatUiState> {
  @override
  ChatUiState build() => const ChatUiState();

  void setUseRag(bool value) {
    state = state.copyWith(useRag: value);
  }

  void setUseMemory(bool value) {
    state = state.copyWith(useMemory: value);
  }

  void setUseTools(bool value) {
    state = state.copyWith(useTools: value);
  }

  void startNewSession() {
    final newId = 'session_${DateTime.now().millisecondsSinceEpoch}';
    ref.read(activeConversationIdProvider.notifier).setSession(newId);
  }

  void selectSession(String sessionId) {
    ref.read(activeConversationIdProvider.notifier).setSession(sessionId);
  }

  void deleteSession(String sessionId) {
    ref.read(chatRepositoryProvider).clearConversation(sessionId);
    ref.read(activeConversationIdProvider.notifier).setSession('default_session');
  }

  Future<void> synchronize() {
    return ref.read(syncEngineProvider.notifier).synchronize();
  }

  /// Auto-sends the Home capture-bar query once. Returns false if already consumed.
  Future<bool> consumeInitialQuery(String query) async {
    if (!state.canConsumeInitialQuery(query)) return false;
    state = state.copyWith(consumedInitialQuery: true, sendInFlight: true);
    try {
      final sessionId = ref.read(activeConversationIdProvider);
      await ref.read(chatRepositoryProvider).sendChatMessage(
            conversationId: sessionId,
            userContent: query.trim(),
            useRag: state.useRag,
            useMemory: state.useMemory,
            useTools: state.useTools,
          );
      return true;
    } finally {
      state = state.copyWith(sendInFlight: false);
    }
  }

  /// Send-in-flight lock: ignores empty text and overlapping streams.
  Future<bool> sendMessage(String text) async {
    final isStreaming = ref.read(streamingMessageIdProvider) != null;
    if (!state.canSend(text, isStreaming: isStreaming)) return false;

    state = state.copyWith(sendInFlight: true);
    try {
      final sessionId = ref.read(activeConversationIdProvider);
      await ref.read(chatRepositoryProvider).sendChatMessage(
            conversationId: sessionId,
            userContent: text.trim(),
            useRag: state.useRag,
            useMemory: state.useMemory,
            useTools: state.useTools,
          );
      return true;
    } finally {
      state = state.copyWith(sendInFlight: false);
    }
  }

  Future<void> confirmTools() {
    final tools = ref.read(pendingConfirmationProvider);
    final sessionId = ref.read(activeConversationIdProvider);
    return ref.read(chatRepositoryProvider).confirmTaintedTools(
          conversationId: sessionId,
          confirmedTools: tools,
        );
  }

  void rejectPendingActions() {
    ref.read(pendingActionsProvider.notifier).clear();
  }

  Future<PendingActionsOutcome> approvePendingActions() {
    final actions = ref.read(pendingActionsProvider);
    final executor = PendingActionsExecutor(
      addTask: ref.read(taskRepositoryProvider).addTask,
      addEvent: ({
        required String title,
        String? description,
        required DateTime startTime,
        required DateTime endTime,
      }) {
        return ref.read(calendarRepositoryProvider).addEvent(
              title: title,
              description: description,
              startTime: startTime,
              endTime: endTime,
            );
      },
      addTransaction: ref.read(financeRepositoryProvider).addTransaction,
      logWater: ref.read(healthRepositoryProvider).logWater,
      logSleep: ref.read(healthRepositoryProvider).logSleep,
      logCalories: ref.read(healthRepositoryProvider).logCalories,
      logExercise: ref.read(healthRepositoryProvider).logExercise,
      logWeight: ref.read(healthRepositoryProvider).logWeight,
    );
    return executor.execute(actions).whenComplete(() {
      ref.read(pendingActionsProvider.notifier).clear();
    });
  }
}

final chatViewModelProvider = NotifierProvider<ChatViewModel, ChatUiState>(ChatViewModel.new);
