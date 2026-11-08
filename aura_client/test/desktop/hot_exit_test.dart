/// M8.8 "kill-app" half: unsaved editor buffers survive the app being killed.
///
/// Before this, an unsaved buffer lived only in `EditorNotifier`'s memory: killing the app, a
/// crash or a power cut lost it. Now a dirty buffer is backed up (encrypted) to
/// `DesktopUnsavedBuffers` shortly after the last edit and comes back, dirty, when the workspace
/// opens again.
///
/// "Killed" here means abandoned, not disposed: a disposed notifier is the graceful path, which
/// gets a chance to write. Each restart uses a fresh notifier and event bus on the same database,
/// as a relaunched app would.
library;

import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/security/encryption_service.dart';
import 'package:aura_client/desktop/events/ide_event.dart';
import 'package:aura_client/desktop/events/ide_event_bus.dart';
import 'package:aura_client/desktop/state/state_notifiers.dart';
import 'package:drift/native.dart';
import 'package:flutter_test/flutter_test.dart';

const _ws = 'ws-1';
const _tab = 'tab-1';
const _path = 'lib/main.dart';

void main() {
  late AppDatabase db;

  setUp(() async {
    await EncryptionService.init();
    db = AppDatabase.forTesting(NativeDatabase.memory());
  });

  tearDown(() => db.close());

  /// A notifier with the workspace open, as the app has it after launch.
  Future<EditorNotifier> launch({Duration backupDelay = const Duration(hours: 1)}) async {
    final bus = IdeEventBus();
    final notifier = EditorNotifier(db, bus, backupDelay: backupDelay);
    bus.publish(const WorkspaceOpened(_ws));
    await pumpEventQueue();
    return notifier;
  }

  /// Opens [_tab] showing [disk] as the file's content, then types [edited] into it.
  Future<void> edit(EditorNotifier notifier, {String disk = 'v1', String edited = 'v1 + unsaved'}) async {
    await notifier.openTab(tabId: _tab, path: _path, workspaceId: _ws);
    notifier.recordDiskBase(_tab, disk);
    notifier.setContent(_tab, disk);
    notifier.setContent(_tab, edited);
    await notifier.markDirty(_tab, true);
  }

  Future<List<DesktopUnsavedBuffer>> backups() => db.select(db.desktopUnsavedBuffers).get();

  test('an unsaved buffer survives the app being killed', () async {
    final before = await launch();
    await edit(before, edited: 'void main() { /* half-written */ }');
    await before.flushBackups();
    // Killed: `before` is abandoned, never disposed.

    final after = await launch();

    expect(after.contentFor(_tab), 'void main() { /* half-written */ }');
    expect(after.state.tabs.singleWhere((t) => t.id == _tab).isDirty, isTrue);
    expect(after.wasRestored(_tab), isTrue);
  });

  test('the backup is written on its own after the delay, with no flush', () async {
    final notifier = await launch(backupDelay: const Duration(milliseconds: 20));
    await edit(notifier);

    await Future<void>.delayed(const Duration(milliseconds: 150));

    expect(await backups(), hasLength(1));
  });

  test('the backup is encrypted at rest, never the plaintext', () async {
    final notifier = await launch();
    await edit(notifier, edited: 'AWS_SECRET_ACCESS_KEY=hunter2');
    await notifier.flushBackups();

    final row = (await backups()).single;
    expect(row.contentEnc, isNot(contains('hunter2')));
    expect(EncryptionService.isEnvelope(row.contentEnc), isTrue);
  });

  test('saving drops the backup, so nothing is restored next time', () async {
    final notifier = await launch();
    await edit(notifier);
    await notifier.flushBackups();

    await notifier.markDirty(_tab, false); // what a save does after writing the file
    await notifier.flushBackups();

    expect(await backups(), isEmpty);
    final after = await launch();
    expect(after.wasRestored(_tab), isFalse);
    expect(after.contentFor(_tab), isNull);
  });

  test('a save racing an in-flight backup write still ends with no backup', () async {
    final notifier = await launch();
    await edit(notifier);

    final inFlight = notifier.flushBackups(); // a write is now queued
    await notifier.markDirty(_tab, false); // the save's delete queues behind it
    await inFlight;
    await notifier.flushBackups();

    expect(await backups(), isEmpty, reason: 'the delete must not run before the write');
  });

  test('discarding the changes (closing without saving) drops the backup', () async {
    final notifier = await launch();
    await edit(notifier);
    await notifier.flushBackups();

    await notifier.closeTab(_tab, force: true);
    await notifier.flushBackups();

    expect(await backups(), isEmpty);
  });

  test('a clean tab is never backed up', () async {
    final notifier = await launch();
    await notifier.openTab(tabId: _tab, path: _path, workspaceId: _ws);
    notifier.setContent(_tab, 'as on disk'); // the initial read, not an edit

    await notifier.flushBackups();

    expect(await backups(), isEmpty);
  });

  test('a restore notices when the file changed on disk since the backup', () async {
    final before = await launch();
    await edit(before, disk: 'v1');
    await before.flushBackups();

    final after = await launch();

    expect(after.restoredOverChangedFile(_tab, 'v1'), isFalse, reason: 'disk unchanged');
    expect(after.restoredOverChangedFile(_tab, 'v2 from a git pull'), isTrue);
  });

  test('a backup whose tab is missing from the saved session reopens the tab', () async {
    await db.into(db.desktopUnsavedBuffers).insert(DesktopUnsavedBuffersCompanion.insert(
          workspaceId: _ws,
          tabId: 'orphan',
          path: 'notes/todo.md',
          contentEnc: EncryptionService.encryptText('- [ ] unsaved'),
          updatedAt: DateTime.now(),
        ));

    final notifier = await launch();

    final tab = notifier.state.tabs.singleWhere((t) => t.id == 'orphan');
    expect(tab.path, 'notes/todo.md');
    expect(tab.isDirty, isTrue);
    expect(notifier.contentFor('orphan'), '- [ ] unsaved');
  });

  test('a backup that can no longer be decrypted is dropped, not shown as ciphertext', () async {
    await db.into(db.desktopUnsavedBuffers).insert(DesktopUnsavedBuffersCompanion.insert(
          workspaceId: _ws,
          tabId: _tab,
          path: _path,
          contentEnc: 'gcm:AAAAAAAAAAAAAAAA:not-real-ciphertext',
          updatedAt: DateTime.now(),
        ));

    final notifier = await launch();

    expect(notifier.contentFor(_tab), isNull);
    expect(notifier.state.tabs.where((t) => t.id == _tab), isEmpty);
    expect(await backups(), isEmpty);
  });

  test('backups belong to their workspace', () async {
    final notifier = await launch();
    await edit(notifier);
    await notifier.flushBackups();

    final bus = IdeEventBus();
    final other = EditorNotifier(db, bus);
    bus.publish(const WorkspaceOpened('ws-2'));
    await pumpEventQueue();

    expect(other.wasRestored(_tab), isFalse);
    expect(other.state.tabs, isEmpty);
    expect(
      await (db.select(db.desktopUnsavedBuffers)..where((t) => t.workspaceId.equals(_ws))).get(),
      hasLength(1),
    );
  });
}
