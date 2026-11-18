import 'dart:async';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../core/sync/sync_client.dart';
import '../../core/sync/sync_result.dart';
import '../../features/auth/presentation/auth_provider.dart';

enum SyncState {
  synced,
  syncing,
  offline,
  error,
}

/// V2: thin state wrapper around [SyncClient] (Doc/V2_BLUEPRINT.md §5/§7).
/// V1's `SyncEngine` was 577 lines of direct Firestore reads/writes with
/// five duplicated per-table LWW comparators; that entire mechanism (and
/// the Firebase Auth session that existed only to satisfy Firestore's
/// security rules) is retired in favor of the backend's Postgres-backed
/// cursor sync — the server does the diffing now, this class just tracks
/// UI state and the periodic trigger. Public shape (`syncEngineProvider`,
/// `SyncState`, `.synchronize()`) is unchanged so `chat_page.dart` didn't
/// need to change.
///
/// The engine follows the session (F1): signed out it is idle, with no timer; signing in rebuilds
/// it, which syncs at once and then every minute; signing out rebuilds it again, cancelling the
/// timer. `signedInSessionProvider` keeps it listened at app scope, so this runs whatever screen
/// is open -- the desktop shell included, which never showed the chat header that used to be the
/// only thing building it.
class SyncEngine extends Notifier<SyncState> {
  Timer? _syncTimer;

  @override
  SyncState build() {
    final signedIn = ref.watch(authProvider.select((auth) => auth.value != null));
    if (!signedIn) return SyncState.offline;

    _syncTimer = Timer.periodic(const Duration(seconds: 60), (_) {
      synchronize();
    });
    ref.onDispose(() {
      _syncTimer?.cancel();
    });

    Future.microtask(() {
      if (ref.mounted) synchronize();
    });
    return SyncState.offline;
  }

  Future<void> synchronize() async {
    if (state == SyncState.syncing) return;

    final authState = ref.read(authProvider);
    if (authState.value == null) {
      state = SyncState.offline;
      return;
    }

    state = SyncState.syncing;
    try {
      final result = await ref.read(syncClientProvider).synchronize();
      if (result.ok) {
        state = SyncState.synced;
        return;
      }
      switch (result.failure) {
        case SyncFailureKind.unauthorized:
        case SyncFailureKind.unknown:
          state = SyncState.error;
        case SyncFailureKind.unavailable:
        case SyncFailureKind.network:
        case null:
          state = SyncState.offline;
      }
    } catch (_) {
      state = SyncState.error;
    }
  }
}

final syncEngineProvider = NotifierProvider<SyncEngine, SyncState>(() {
  return SyncEngine();
});
