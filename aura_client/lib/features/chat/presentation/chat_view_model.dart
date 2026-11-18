import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/ai/vision/chat_images.dart';
import 'package:aura_client/l10n/l10n.dart';

import '../../calendar/data/calendar_repository.dart';
import '../../finance/data/finance_repository.dart';
import '../../health/data/health_repository.dart';
import '../../sync/sync_engine.dart';
import '../../tasks/data/task_repository.dart';
import '../data/chat_repository.dart';
import '../data/turn_meta.dart';
import 'pending_actions_executor.dart';

/// What an image-only message asks, in the language on screen when it is sent.
String get kDefaultImageQuestion => currentL10n.chatDefaultImageQuestion;

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

  /// Text, or at least one attached image, and nothing already in flight.
  bool canSend(String text, {required bool isStreaming, bool hasImages = false}) {
    if (composerLocked(isStreaming: isStreaming)) return false;
    return text.trim().isNotEmpty || hasImages;
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

  /// Deleting the active session switches to a fresh session id rather than
  /// back to `default_session`:
  /// if the server has not confirmed clearing the deleted session's
  /// transcript yet (offline), reusing an id could still carry its history
  /// into the next turn (F-07).
  Future<void> deleteSession(String sessionId) async {
    if (ref.read(activeConversationIdProvider) == sessionId) startNewSession();
    await ref.read(chatRepositoryProvider).clearConversation(sessionId);
  }

  Future<void> synchronize() {
    return ref.read(syncEngineProvider.notifier).synchronize();
  }

  /// Auto-sends the Home capture-bar query once. Returns false if already consumed.
  /// [workspaceRoot] as for [sendMessage].
  Future<bool> consumeInitialQuery(String query, {String? workspaceRoot, bool desktopTaskIsolation = false}) async {
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
            workspaceRoot: workspaceRoot,
            desktopTaskIsolation: desktopTaskIsolation,
          );
      return true;
    } finally {
      state = state.copyWith(sendInFlight: false);
    }
  }

  /// Send-in-flight lock: ignores empty text and overlapping streams. Attached images
  /// (`pendingChatImagesProvider`) go with this message and are cleared once it is sent; with
  /// images and no text, the question defaults to [kDefaultImageQuestion].
  ///
  /// [workspaceRoot] is the folder on the user's computer the turn works in: the Desktop IDE's
  /// active workspace root (FSA-006), or the folder a phone or web Engineer chat picked in its
  /// desktop bar (WP8). The backend forwards it to the paired daemon as the `cwd` of every
  /// command. Null from any surface without one.
  ///
  /// [desktopTaskIsolation] asks the daemon to do the turn's work in the conversation's own git
  /// worktree (WP4, D-09) -- every Engineer turn on the user's computer does.
  Future<bool> sendMessage(String text, {String? workspaceRoot, bool desktopTaskIsolation = false}) async {
    final isStreaming = ref.read(streamingMessageIdProvider) != null;
    final images = ref.read(pendingChatImagesProvider);
    if (!state.canSend(text, isStreaming: isStreaming, hasImages: images.isNotEmpty)) {
      return false;
    }

    state = state.copyWith(sendInFlight: true);
    try {
      final sessionId = ref.read(activeConversationIdProvider);
      final trimmed = text.trim();
      ref.read(pendingChatImagesProvider.notifier).clear();
      await ref.read(chatRepositoryProvider).sendChatMessage(
            conversationId: sessionId,
            userContent: trimmed.isEmpty ? kDefaultImageQuestion : trimmed,
            useRag: state.useRag,
            useMemory: state.useMemory,
            useTools: state.useTools,
            images: images,
            workspaceRoot: workspaceRoot,
            desktopTaskIsolation: desktopTaskIsolation,
          );
      return true;
    } finally {
      state = state.copyWith(sendInFlight: false);
    }
  }

  /// Approves [calls] -- held calls of one turn, as one confirmation card shows them -- by their
  /// server-issued approval ids, never by tool name or by "the latest message" (F-04). Returns
  /// false, changing nothing, while another reply is still streaming.
  Future<bool> confirmTools(List<PendingToolCall> calls) {
    final sessionId = ref.read(activeConversationIdProvider);
    return ref.read(chatRepositoryProvider).confirmPendingCalls(
          conversationId: sessionId,
          calls: calls,
        );
  }

  /// Declines the given pending calls without sending anything to the backend -- their approval
  /// ids are never sent, so they never run, and the held turn expires server-side.
  void rejectTools(List<PendingToolCall> calls) {
    ref.read(chatRepositoryProvider).rejectPendingCalls(calls);
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
