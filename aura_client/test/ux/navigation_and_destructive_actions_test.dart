/// Audit F-34: navigation dead ends and one-tap destructive actions.
///
/// - With the Desktop IDE on, `/today` always bounced to `/ide` (ADR-015 says
///   "no forced redirect").
/// - Settings sub-pages were opened with `go`: no back arrow, and Android
///   back left the app.
/// - Deleting a task or a conversation was one tap with no undo; signing out
///   was one tap with no confirmation.
library;

import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/routing/router.dart';
import 'package:aura_client/core/security/encryption_service.dart';
import 'package:aura_client/core/sync/remote_deletes.dart';
import 'package:aura_client/core/undo/undoable_deletes.dart';
import 'package:aura_client/features/auth/presentation/auth_provider.dart';
import 'package:aura_client/features/auth/presentation/sign_out_confirmation.dart';
import 'package:aura_client/features/chat/data/chat_repository.dart';
import 'package:aura_client/features/settings/presentation/settings_page.dart';
import 'package:aura_client/features/tasks/data/task_repository.dart';
import 'package:aura_client/features/tasks/presentation/widgets/task_item_card.dart';
import 'package:drift/native.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';

class _SignedInAuth extends AuthNotifier {
  int signOuts = 0;

  @override
  Future<UserProfile?> build() async =>
      UserProfile(id: 'user-1', email: 'a@example.com', displayName: 'A');

  @override
  Future<void> signOut() async {
    signOuts++;
    state = const AsyncValue.data(null);
  }
}

/// Records remote deletes instead of calling the server.
class _RecordingRemoteDeletes extends RemoteDeletes {
  _RecordingRemoteDeletes(super.db, super.ref);

  final requested = <String>[];

  @override
  Future<bool> request(RemoteDeleteKind kind, String id) async {
    requested.add('${kind.table}:$id');
    return true;
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('Landing and reachable screens per build', () {
    Future<String?> redirect(String location, {required bool desktop}) {
      return resolveAppRedirect(
        isLoggedIn: true,
        onboarded: true,
        location: location,
        desktop: desktop,
      );
    }

    test('mobile: Today and Life Hub are hidden and land on Copilot', () async {
      expect(await redirect('/today', desktop: false), '/chat');
      expect(await redirect('/life', desktop: false), '/chat');
    });

    test('mobile: the IDE is not shown', () async {
      expect(await redirect('/ide', desktop: false), '/chat');
    });

    test('mobile: Copilot, Engineer, You and the other screens stay reachable', () async {
      for (final location in [
        '/chat',
        '/engineer-history',
        '/github-connect',
        '/engineer-daemon',
        '/you',
        '/knowledge',
      ]) {
        expect(await redirect(location, desktop: false), isNull, reason: location);
      }
    });

    test('desktop: the IDE, You and its own settings are reachable', () async {
      for (final location in ['/ide', '/ide/workspace', '/you', '/appearance']) {
        expect(await redirect(location, desktop: true), isNull, reason: location);
      }
    });

    test('desktop: every other screen goes to the IDE', () async {
      for (final location in ['/chat', '/engineer-history', '/life', '/knowledge']) {
        expect(await redirect(location, desktop: true), '/ide', reason: location);
      }
    });

    test('sign-in lands on the platform home', () async {
      expect(
        await resolveAppRedirect(
          isLoggedIn: true,
          onboarded: true,
          location: '/login',
          desktop: true,
        ),
        '/ide',
      );
      expect(
        await resolveAppRedirect(
          isLoggedIn: true,
          onboarded: true,
          location: '/login',
          desktop: false,
        ),
        '/chat',
      );
    });

    test('signed out still goes to login', () async {
      expect(
        await resolveAppRedirect(
          isLoggedIn: false,
          onboarded: true,
          location: '/chat',
          desktop: true,
        ),
        '/login',
      );
    });
  });

  testWidgets('a settings sub-page is pushed, so back returns to settings', (tester) async {
    tester.view.physicalSize = const Size(400, 1600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);

    final router = GoRouter(
      initialLocation: '/you',
      routes: [
        GoRoute(path: '/you', builder: (_, _) => const SettingsPage()),
        GoRoute(
          path: '/appearance',
          builder: (_, _) => Scaffold(
            appBar: AppBar(title: const Text('Appearance page')),
          ),
        ),
      ],
    );
    await tester.pumpWidget(
      ProviderScope(
        child: MaterialApp.router(theme: AuraThemeV2.light, routerConfig: router),
      ),
    );
    await tester.pumpAndSettle();

    await tester.tap(find.text('Appearance'));
    await tester.pumpAndSettle();
    expect(find.text('Appearance page'), findsOneWidget);
    expect(find.byType(BackButton), findsOneWidget, reason: 'a pushed page has a back arrow');

    await tester.tap(find.byType(BackButton));
    await tester.pumpAndSettle();
    expect(find.text('Appearance page'), findsNothing);
    expect(find.text('You'), findsWidgets);
  });

  group('sign-out asks first', () {
    Future<_SignedInAuth> pumpSignOutButton(WidgetTester tester) async {
      final auth = _SignedInAuth();
      await tester.pumpWidget(
        ProviderScope(
          overrides: [authProvider.overrideWith(() => auth)],
          child: MaterialApp(
            theme: AuraThemeV2.light,
            home: Consumer(
              builder: (context, ref, _) => Scaffold(
                body: TextButton(
                  onPressed: () => confirmAndSignOut(context, ref),
                  child: const Text('Sign Out'),
                ),
              ),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      return auth;
    }

    testWidgets('cancel keeps the user signed in', (tester) async {
      final auth = await pumpSignOutButton(tester);

      await tester.tap(find.text('Sign Out'));
      await tester.pumpAndSettle();
      expect(find.text('Sign out?'), findsOneWidget);
      expect(auth.signOuts, 0, reason: 'nothing happens before the user confirms');

      await tester.tap(find.text('Cancel'));
      await tester.pumpAndSettle();
      expect(find.text('Sign out?'), findsNothing);
      expect(auth.signOuts, 0);
    });

    testWidgets('confirming signs out', (tester) async {
      final auth = await pumpSignOutButton(tester);

      await tester.tap(find.text('Sign Out'));
      await tester.pumpAndSettle();
      await tester.tap(find.widgetWithText(AuraButton, 'Sign out'));
      await tester.pumpAndSettle();

      expect(auth.signOuts, 1);
      expect(find.text('Sign out?'), findsNothing);
    });
  });

  group('undoable deletes', () {
    late AppDatabase db;

    setUp(() async {
      await EncryptionService.init();
      db = AppDatabase.forTesting(NativeDatabase.memory());
    });

    tearDown(() => db.close());

    /// Unmounts the tree so the providers' Drift streams close, then lets
    /// Drift's zero-length close timer run before the test ends.
    Future<void> unmount(WidgetTester tester) async {
      await tester.pumpWidget(const SizedBox.shrink());
      await tester.pump(const Duration(milliseconds: 1));
      await tester.pump(const Duration(milliseconds: 1));
    }

    Future<void> insertTask(String id, String title) {
      return db.into(db.tasks).insert(TasksCompanion.insert(
            id: id,
            title: title,
            priority: 'low',
            isCompleted: false,
            category: 'personal',
            createdAt: DateTime.now(),
          ));
    }

    Future<List<OutboxEntry>> deletesQueued() async =>
        (await db.select(db.outboxEntries).get()).where((e) => e.operation == 'delete').toList();

    Future<void> pumpTaskList(WidgetTester tester) async {
      await tester.pumpWidget(
        ProviderScope(
          overrides: [databaseProvider.overrideWithValue(db)],
          child: MaterialApp(
            theme: AuraThemeV2.light,
            home: Scaffold(
              body: Consumer(
                builder: (context, ref, _) {
                  final tasks = ref.watch(tasksStreamProvider).value ?? const <Task>[];
                  return ListView(children: [for (final t in tasks) TaskItemCard(task: t)]);
                },
              ),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
    }

    testWidgets('undo restores a deleted task, and nothing reaches the outbox', (tester) async {
      await insertTask('t1', 'Water the plants');
      await pumpTaskList(tester);
      expect(find.text('Water the plants'), findsOneWidget);

      await tester.tap(find.byTooltip('Delete task'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 750));
      expect(find.text('Water the plants'), findsNothing, reason: 'hidden at once');
      expect(find.text('Task deleted'), findsOneWidget);

      await tester.tap(find.text('Undo'));
      await tester.pumpAndSettle();

      expect(find.text('Water the plants'), findsOneWidget);
      await unmount(tester);
      expect(await tester.runAsync(() => db.select(db.tasks).get()), hasLength(1));
      expect(await tester.runAsync(deletesQueued), isEmpty, reason: 'the server never hears of an undone delete');
    });

    testWidgets('without undo, the task is deleted once the snackbar closes', (tester) async {
      await insertTask('t1', 'Water the plants');
      await pumpTaskList(tester);

      await tester.tap(find.byTooltip('Delete task'));
      await tester.pump();
      expect(await db.select(db.tasks).get(), hasLength(1),
          reason: 'not deleted while it can still be undone');

      // The snackbar's 4s clock starts once it has finished sliding in.
      await tester.pump(const Duration(seconds: 1));
      await tester.pump(const Duration(seconds: 5));
      await tester.pumpAndSettle();
      expect(find.text('Task deleted'), findsNothing);
      // The commit's Drift writes complete on real time, not the test clock.
      await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 100)));
      await tester.pumpAndSettle();

      expect(await tester.runAsync(() => db.select(db.tasks).get()), isEmpty);
      final queued = (await tester.runAsync(deletesQueued))!;
      expect(queued.single.recordId, 't1');
      await tester.pumpAndSettle();
      expect(find.text('Water the plants'), findsNothing);
      await unmount(tester);
    });

    testWidgets('undo restores a deleted conversation', (tester) async {
      await db.into(db.messages).insert(MessagesCompanion.insert(
            id: 'm1',
            conversationId: 'session_123456',
            role: 'user',
            content: 'Hello',
            createdAt: DateTime.now(),
          ));
      _RecordingRemoteDeletes? remote;

      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            databaseProvider.overrideWithValue(db),
            remoteDeletesProvider.overrideWith((ref) => remote = _RecordingRemoteDeletes(db, ref)),
          ],
          child: MaterialApp(
            theme: AuraThemeV2.light,
            home: Scaffold(
              body: Consumer(
                builder: (context, ref, _) {
                  final sessions = ref.watch(conversationsStreamProvider).value ?? const <String>[];
                  return ListView(children: [
                    for (final s in sessions)
                      ListTile(
                        title: Text(s),
                        trailing: IconButton(
                          tooltip: 'Delete conversation',
                          icon: const Icon(Icons.delete_outline),
                          onPressed: () => deleteWithUndo(
                            context,
                            ref,
                            key: chatSessionUndoKey(s),
                            message: 'Conversation deleted',
                            commit: () => ref.read(chatRepositoryProvider).clearConversation(s),
                          ),
                        ),
                      ),
                  ]);
                },
              ),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.text('session_123456'), findsOneWidget);

      await tester.tap(find.byTooltip('Delete conversation'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 750));
      expect(find.text('session_123456'), findsNothing);

      await tester.tap(find.text('Undo'));
      await tester.pumpAndSettle();

      expect(find.text('session_123456'), findsOneWidget);
      expect(await db.select(db.messages).get(), hasLength(1));
      expect(remote?.requested ?? const <String>[], isEmpty,
          reason: 'the transcript delete was never sent');
      await unmount(tester);
    });
  });
}
