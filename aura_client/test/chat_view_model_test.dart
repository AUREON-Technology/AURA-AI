import 'package:aura_client/features/chat/data/chat_repository.dart';
import 'package:aura_client/features/chat/presentation/chat_view_model.dart';
import 'package:aura_client/features/chat/presentation/pending_actions_executor.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('DesktopTaskIsolation (D8 per-task worktrees)', () {
    test('a session is unisolated until explicitly marked', () {
      final container = ProviderContainer();
      addTearDown(container.dispose);
      final notifier = container.read(desktopTaskIsolationProvider.notifier);
      expect(notifier.isIsolated('session-1'), isFalse);
      notifier.markIsolated('session-1');
      expect(notifier.isIsolated('session-1'), isTrue);
      expect(notifier.isIsolated('session-2'), isFalse);
    });
  });

  group('ChatViewModel.startNewIsolatedTask', () {
    test('starts a fresh session and marks it isolated, leaving other sessions alone', () {
      final container = ProviderContainer();
      addTearDown(container.dispose);

      final before = container.read(activeConversationIdProvider);
      container.read(chatViewModelProvider.notifier).startNewIsolatedTask();
      final after = container.read(activeConversationIdProvider);

      expect(after, isNot(before));
      final isolation = container.read(desktopTaskIsolationProvider.notifier);
      expect(isolation.isIsolated(after), isTrue);
      expect(isolation.isIsolated(before), isFalse);
    });

    test('startNewSession (the ordinary "New Chat" action) never marks isolation', () {
      final container = ProviderContainer();
      addTearDown(container.dispose);

      container.read(chatViewModelProvider.notifier).startNewSession();
      final sessionId = container.read(activeConversationIdProvider);
      expect(
        container.read(desktopTaskIsolationProvider.notifier).isIsolated(sessionId),
        isFalse,
      );
    });
  });

  group('ChatUiState', () {
    test('composer is locked while send is in flight or a stream is active', () {
      const idle = ChatUiState();
      expect(idle.composerLocked(isStreaming: false), isFalse);
      expect(idle.canSend('hello', isStreaming: false), isTrue);

      expect(idle.canSend('  ', isStreaming: false), isFalse);
      expect(idle.canSend('hello', isStreaming: true), isFalse);

      const sending = ChatUiState(sendInFlight: true);
      expect(sending.composerLocked(isStreaming: false), isTrue);
      expect(sending.canSend('hello', isStreaming: false), isFalse);
    });

    test('initial query is consumed only once', () {
      const idle = ChatUiState();
      expect(idle.canConsumeInitialQuery('buy milk'), isTrue);
      expect(idle.canConsumeInitialQuery('  '), isFalse);
      expect(idle.canConsumeInitialQuery(null), isFalse);

      const consumed = ChatUiState(consumedInitialQuery: true);
      expect(consumed.canConsumeInitialQuery('buy milk'), isFalse);
    });
  });

  group('pendingActionLabel', () {
    test('formats known action types', () {
      expect(pendingActionLabel('add_task', {'title': 'Milk'}), 'Create Task: Milk');
      expect(pendingActionLabel('add_event', {'title': 'Standup'}), 'Schedule Event: Standup');
      expect(pendingActionLabel('add_expense', {'amount': 12}), 'Log Expense: \$12');
      expect(pendingActionLabel('unknown', {}), 'Unknown Action: unknown');
    });
  });

  group('PendingActionsExecutor', () {
    test('executes add_task and reports unknown types as failures', () async {
      String? createdTitle;
      final executor = PendingActionsExecutor(
        addTask: ({
          required title,
          description,
          required priority,
          dueDate,
          required category,
        }) async {
          createdTitle = title;
        },
        addEvent: ({required title, description, required startTime, required endTime}) async {},
        addTransaction: ({
          required type,
          required amount,
          required category,
          description,
          required date,
        }) async {},
        logWater: (date, value) async {},
        logSleep: (date, value) async {},
        logCalories: (date, value) async {},
        logExercise: (date, value) async {},
        logWeight: (date, value) async {},
      );

      final outcome = await executor.execute([
        {
          'type': 'add_task',
          'params': {'title': 'Buy milk', 'priority': 'high', 'category': 'personal'},
        },
        {'type': 'not_a_tool', 'params': {}},
      ]);

      expect(createdTitle, 'Buy milk');
      expect(outcome.succeeded, 1);
      expect(outcome.failedLabels, ['Unknown Action: not_a_tool']);
      expect(outcome.allSucceeded, isFalse);
      expect(outcome.allFailed, isFalse);
    });
  });
}
