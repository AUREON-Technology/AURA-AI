// C1 / F20: approving the suggested actions runs them once, however fast the button is tapped.
//
// The card's Approve used to have no in-flight guard and the view model cleared the actions only
// when they had all run, so a double tap created every task, event and expense twice.

import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/security/encryption_service.dart';
import 'package:aura_client/features/chat/data/chat_repository.dart';
import 'package:aura_client/features/chat/presentation/chat_view_model.dart';
import 'package:aura_client/features/chat/presentation/widgets/pending_actions_card.dart';
import 'package:aura_client/l10n/generated/app_localizations.dart';
import 'package:drift/native.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

const _actions = [
  {
    'type': 'add_task',
    'params': {'title': 'Buy milk', 'priority': 'medium', 'category': 'personal'},
  },
];

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late AppDatabase db;

  setUp(() async {
    await EncryptionService.init();
    db = AppDatabase.forTesting(NativeDatabase.memory());
  });

  tearDown(() async => db.close());

  ProviderContainer scope() {
    final container = ProviderContainer(overrides: [databaseProvider.overrideWith((ref) => db)]);
    addTearDown(container.dispose);
    container.read(pendingActionsProvider.notifier).setActions(List.of(_actions));
    return container;
  }

  test('two approvals in a row run the actions once', () async {
    final c = scope();
    final vm = c.read(chatViewModelProvider.notifier);

    final outcomes = await Future.wait([vm.approvePendingActions(), vm.approvePendingActions()]);

    expect(await db.select(db.tasks).get(), hasLength(1));
    expect(outcomes.whereType<Object>(), hasLength(1), reason: 'the second tap is ignored');
    expect(c.read(pendingActionsProvider), isEmpty);
    expect(c.read(chatViewModelProvider).approvingActions, isFalse);
  });

  test('while they run, the view model says so, and the actions are already claimed', () async {
    final c = scope();
    final vm = c.read(chatViewModelProvider.notifier);

    final running = vm.approvePendingActions();
    expect(c.read(chatViewModelProvider).approvingActions, isTrue);
    await running;
    expect(c.read(chatViewModelProvider).approvingActions, isFalse);
  });

  testWidgets('a double tap on Approve creates one task', (tester) async {
    final c = scope();
    await tester.pumpWidget(UncontrolledProviderScope(
      container: c,
      child: MaterialApp(
        // No Aura theme: its Google Fonts would be fetched over the network inside runAsync.
        localizationsDelegates: AppLocalizations.localizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        home: const Scaffold(body: PendingActionsCard()),
      ),
    ));
    await tester.pump();

    final approve = find.text('Approve Actions');
    expect(approve, findsOneWidget);
    await tester.tap(approve);
    await tester.tap(approve, warnIfMissed: false);
    await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 200)));
    await tester.pump();

    expect(await tester.runAsync(() => db.select(db.tasks).get()), hasLength(1));
  });
}
