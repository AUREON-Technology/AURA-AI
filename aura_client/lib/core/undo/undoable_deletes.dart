/// One-tap deletes that can be taken back (audit F-34).
///
/// **Why the delete is deferred rather than undone.** A deleted task goes to
/// the server as an outbox `delete`, which `api/v2/sync.py` applies as a soft
/// delete (`deleted_at`). A later `upsert` of the same id updates the row but
/// does *not* clear `deleted_at` -- so "delete, then re-insert on Undo" would
/// leave the server holding a tombstone this device thinks is alive whenever a
/// sync ran in between, and the next pull would delete it here too. A chat
/// session's delete also reaches the server's transcript store through
/// `RemoteDeletes`, which has no inverse at all.
///
/// So nothing is deleted until the undo window has closed: the item is only
/// hidden (every list that shows these items filters on this provider), and
/// the real delete -- local row, outbox entry, remote delete -- runs once the
/// snackbar goes away for any reason other than its Undo action. If the app is
/// killed inside the window, the item simply comes back, which is the safe
/// direction to fail in.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../design_system/components/aura_snackbar.dart';

/// Keys of items whose delete is waiting out its undo window.
class UndoableDeletes extends Notifier<Set<String>> {
  final Map<String, Future<void> Function()> _commits = {};

  @override
  Set<String> build() => const {};

  /// Hides [key] now; [commit] performs the actual delete later.
  void hide(String key, Future<void> Function() commit) {
    _commits[key] = commit;
    state = {...state, key};
  }

  /// Takes the delete back. Returns false if it had already been committed.
  bool undo(String key) {
    if (_commits.remove(key) == null) return false;
    state = {...state}..remove(key);
    return true;
  }

  /// Runs the delete for [key] now, if it is still pending.
  Future<void> commit(String key) async {
    final commit = _commits.remove(key);
    if (commit == null) return;
    try {
      await commit();
    } finally {
      state = {...state}..remove(key);
    }
  }

  /// Drops everything still pending without running it -- account deletion calls this: the
  /// server is erasing the whole account, so a single pending delete has nothing left to do.
  void discardAll() {
    _commits.clear();
    state = const {};
  }

  /// Commits everything still pending -- sign-out calls this before its final
  /// push, so a delete made just before signing out still reaches the server.
  Future<void> commitAll() async {
    for (final key in _commits.keys.toList()) {
      await commit(key);
    }
  }
}

final undoableDeletesProvider =
    NotifierProvider<UndoableDeletes, Set<String>>(UndoableDeletes.new);

String taskUndoKey(String taskId) => 'task:$taskId';
String chatSessionUndoKey(String sessionId) => 'chat_session:$sessionId';

/// Hides the item under [key], shows [message] with an Undo action, and runs
/// [commit] once the snackbar closes without Undo having been tapped.
/// [onUndo] runs after an undo, for any state the delete itself changed (for
/// example, re-selecting a chat session that was active).
void deleteWithUndo(
  BuildContext context,
  WidgetRef ref, {
  required String key,
  required String message,
  required Future<void> Function() commit,
  VoidCallback? onUndo,
}) {
  final deletes = ref.read(undoableDeletesProvider.notifier);
  deletes.hide(key, commit);
  final controller = AuraSnackbar.show(
    context,
    message: message,
    actionLabel: 'Undo',
  );
  controller.closed.then((reason) {
    if (reason == SnackBarClosedReason.action) {
      if (deletes.undo(key)) onUndo?.call();
    } else {
      deletes.commit(key).catchError(
            (Object e) => debugPrint('[UndoableDeletes] delete of $key failed: $e'),
          );
    }
  });
}
