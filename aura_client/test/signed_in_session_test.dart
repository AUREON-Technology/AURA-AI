// F1: the 60 s sync only ran where a screen happened to watch the sync engine (the mobile chat
// header), so a desktop user -- who lands on /ide -- never synced in the background. It now runs
// at app scope for as long as someone is signed in, and stops at sign-out.
// F5: the saved team is checked against the server's list once after sign-in.
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/sync/sync_client.dart';
import 'package:aura_client/core/sync/sync_result.dart';
import 'package:aura_client/core/workspaces/active_workspace.dart';
import 'package:aura_client/features/auth/presentation/auth_provider.dart';
import 'package:aura_client/features/sync/signed_in_session.dart';
import 'package:aura_client/features/sync/sync_engine.dart';
import 'package:aura_client/features/workspaces/data/workspaces_client.dart';
import 'package:aura_client/main.dart';
import 'package:drift/native.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'workspaces/workspace_fakes.dart';

late AppDatabase _db;

class _CountingSync extends SyncClient {
  _CountingSync(Ref ref) : super(_db, ref);
  int runs = 0;

  @override
  Future<SyncResult> synchronize() async {
    runs++;
    return const SyncResult.success();
  }
}

class _SwitchableAuth extends AuthNotifier {
  _SwitchableAuth(this.initial);
  final UserProfile? initial;

  @override
  Future<UserProfile?> build() async => initial;

  void signInAs(String id) =>
      state = AsyncValue.data(UserProfile(id: id, email: '$id@example.com', displayName: id));

  void signOutNow() => state = const AsyncValue.data(null);
}

class _ListedWorkspaces extends WorkspacesClient {
  _ListedWorkspaces(super.ref, this.listed);
  final List<WorkspaceSummary> listed;
  int lists = 0;

  @override
  Future<List<WorkspaceSummary>> list() async {
    lists++;
    return listed;
  }
}

/// Riverpod applies a dependency change on a zero-length timer, which a bare `pump()` never
/// fires under the test's fake clock.
const _tick = Duration(milliseconds: 10);

UserProfile _ann() => UserProfile(id: 'ann', email: 'ann@example.com', displayName: 'Ann');

void main() {
  setUp(() {
    SharedPreferences.setMockInitialValues({});
    _db = AppDatabase.forTesting(NativeDatabase.memory());
  });
  tearDown(() => _db.close());

  testWidgets('a signed-in desktop app syncs every minute with no chat screen open, and stops at sign-out',
      (tester) async {
    late _CountingSync sync;
    final container = ProviderContainer(overrides: [
      authProvider.overrideWith(() => _SwitchableAuth(_ann())),
      syncClientProvider.overrideWith((ref) => sync = _CountingSync(ref)),
      workspacesClientProvider.overrideWith((ref) => _ListedWorkspaces(ref, const [personal])),
    ]);
    addTearDown(container.dispose);

    // What AuraApp does at app scope, whatever route the shell lands on.
    container.listen(signedInSessionProvider, (_, _) {});
    await tester.pump(_tick);
    await tester.pump(_tick);
    expect(sync.runs, 1, reason: 'a sync right after sign-in');

    await tester.pump(const Duration(seconds: 61));
    expect(sync.runs, 2, reason: 'and every minute after');

    (container.read(authProvider.notifier) as _SwitchableAuth).signOutNow();
    await tester.pump(_tick);
    final atSignOut = sync.runs;
    await tester.pump(const Duration(minutes: 5));
    expect(sync.runs, atSignOut, reason: 'nothing runs for a signed-out device');

    (container.read(authProvider.notifier) as _SwitchableAuth).signInAs('bob');
    await tester.pump(_tick);
    await tester.pump(_tick);
    expect(sync.runs, atSignOut + 1, reason: 'the next sign-in starts it again');

    (container.read(authProvider.notifier) as _SwitchableAuth).signOutNow();
    await tester.pump(_tick);
  });

  testWidgets('AuraApp keeps the session running on the desktop shell', (tester) async {
    debugDefaultTargetPlatformOverride = TargetPlatform.linux;
    late _CountingSync sync;
    await tester.pumpWidget(ProviderScope(
      overrides: [
        authProvider.overrideWith(() => _SwitchableAuth(null)),
        databaseProvider.overrideWithValue(_db),
        syncClientProvider.overrideWith((ref) => sync = _CountingSync(ref)),
        workspacesClientProvider.overrideWith((ref) => _ListedWorkspaces(ref, const [personal])),
      ],
      child: const AuraApp(),
    ));
    await tester.pump(_tick);
    final container = ProviderScope.containerOf(tester.element(find.byType(MaterialApp)));
    expect(container.exists(syncClientProvider), isFalse, reason: 'signed out: nothing syncs');

    (container.read(authProvider.notifier) as _SwitchableAuth).signInAs('ann');
    await tester.pump(_tick);
    await tester.pump(_tick);
    expect(sync.runs, 1);

    // Sign out again so no periodic timer outlives the test.
    (container.read(authProvider.notifier) as _SwitchableAuth).signOutNow();
    await tester.pump(_tick);
    // The desktop shell's own one-shot timers (daemon status polling) run out with the tree.
    await tester.pumpWidget(const SizedBox());
    await tester.pump(const Duration(seconds: 5));
    debugDefaultTargetPlatformOverride = null;
  });

  testWidgets('F5: a saved team the server no longer lists is dropped at sign-in', (tester) async {
    SharedPreferences.setMockInitialValues({});
    late _ListedWorkspaces workspaces;
    final container = ProviderContainer(overrides: [
      authProvider.overrideWith(() => _SwitchableAuth(null)),
      syncClientProvider.overrideWith((ref) => _CountingSync(ref)),
      workspacesClientProvider.overrideWith((ref) => workspaces = _ListedWorkspaces(ref, const [personal])),
    ]);
    addTearDown(container.dispose);
    await container.read(activeWorkspaceProvider.notifier).select(team());
    container.listen(signedInSessionProvider, (_, _) {});
    await tester.pump(_tick);

    (container.read(authProvider.notifier) as _SwitchableAuth).signInAs('ann');
    await tester.pump(_tick);
    await tester.pump(_tick);
    await tester.pump(_tick);

    expect(workspaces.lists, 1);
    expect(container.read(activeWorkspaceProvider), isNull);

    (container.read(authProvider.notifier) as _SwitchableAuth).signOutNow();
    await tester.pump(_tick);
  });

  test('the engine stays offline and starts no timer while signed out', () async {
    final container = ProviderContainer(overrides: [
      authProvider.overrideWith(() => _SwitchableAuth(null)),
    ]);
    addTearDown(container.dispose);
    container.listen(syncEngineProvider, (_, _) {});
    await container.read(authProvider.future);
    expect(container.read(syncEngineProvider), SyncState.offline);
    expect(container.exists(syncClientProvider), isFalse);
  });
}
