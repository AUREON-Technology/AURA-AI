/// Desktop IDE audit (DI-01..DI-08): the ways the editor could lose or silently rewrite a user's
/// file. Each group pins one of them against the real notifier / file service, not a mock.
library;

import 'dart:convert';
import 'dart:io';

import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/security/encryption_service.dart';
import 'package:aura_client/desktop/editor/providers/flutter_code_editor_provider.dart';
import 'package:aura_client/desktop/events/ide_event.dart';
import 'package:aura_client/desktop/events/ide_event_bus.dart';
import 'package:aura_client/desktop/shell/desktop_actions.dart';
import 'package:aura_client/desktop/state/state_notifiers.dart';
import 'package:aura_client/desktop/workspace/workspace_file_service.dart';
import 'package:drift/drift.dart' show Value;
import 'package:drift/native.dart';
import 'package:flutter/material.dart';
import 'package:flutter_code_editor/flutter_code_editor.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:highlight/languages/dart.dart' as lang_dart;
import 'package:path/path.dart' as p;

const _ws = 'ws-1';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('editor provider (DI-01, DI-05)', () {
    test('a folded block is still in the text that gets saved', () {
      const source = 'void main() {\n  print(1);\n  print(2);\n}\n';
      final controller = CodeController(text: source, language: lang_dart.dart);
      addTearDown(controller.dispose);

      controller.foldAt(0);

      // The field shows less than the document; saving what the field shows would drop it.
      expect(controller.text.length, lessThan(source.length), reason: 'the block is folded');
      expect(documentTextOf(controller), source);
    });

    test('tab characters survive loading; the package default would turn them into spaces', () {
      const source = 'all:\n\tgo build\n';

      final preserved = CodeController(text: source, modifiers: preservingTabModifiers);
      final packageDefault = CodeController(text: source);
      addTearDown(preserved.dispose);
      addTearDown(packageDefault.dispose);

      expect(preserved.fullText, source);
      expect(packageDefault.fullText, isNot(source), reason: 'proves the default really rewrites tabs');
    });
  });

  group('WorkspaceFileService (DI-02, DI-03)', () {
    late Directory dir;
    late String root;
    late WorkspaceFileService service;

    setUp(() {
      dir = Directory.systemTemp.createTempSync('aura_client_data_integrity_');
      root = dir.resolveSymbolicLinksSync();
      service = WorkspaceFileService(root);
    });

    tearDown(() => dir.deleteSync(recursive: true));

    test('renaming onto an existing file is refused and neither file changes', () async {
      File(p.join(root, 'a.txt')).writeAsStringSync('A');
      File(p.join(root, 'b.txt')).writeAsStringSync('B');

      await expectLater(
        service.rename(p.join(root, 'a.txt'), p.join(root, 'b.txt'), isDirectory: false),
        throwsA(isA<FileSystemException>()),
      );

      expect(File(p.join(root, 'a.txt')).readAsStringSync(), 'A');
      expect(File(p.join(root, 'b.txt')).readAsStringSync(), 'B');
    });

    test('renaming onto an existing folder is refused too', () async {
      Directory(p.join(root, 'one')).createSync();
      Directory(p.join(root, 'two')).createSync();
      File(p.join(root, 'two', 'keep.txt')).writeAsStringSync('keep');

      await expectLater(
        service.rename(p.join(root, 'one'), p.join(root, 'two'), isDirectory: true),
        throwsA(isA<FileSystemException>()),
      );

      expect(File(p.join(root, 'two', 'keep.txt')).readAsStringSync(), 'keep');
    });

    test('renaming to a free name still works', () async {
      File(p.join(root, 'a.txt')).writeAsStringSync('A');

      await service.rename(p.join(root, 'a.txt'), p.join(root, 'c.txt'), isDirectory: false);

      expect(File(p.join(root, 'c.txt')).readAsStringSync(), 'A');
      expect(File(p.join(root, 'a.txt')).existsSync(), isFalse);
    });

    test('saving into a folder that no longer exists fails instead of recreating it', () async {
      await expectLater(service.writeFile('gone/a.txt', 'x'), throwsA(isA<FileSystemException>()));

      expect(Directory(p.join(root, 'gone')).existsSync(), isFalse);
    });
  });

  group('EditorNotifier', () {
    late AppDatabase db;
    late IdeEventBus bus;
    late String root;

    setUp(() async {
      await EncryptionService.init();
      db = AppDatabase.forTesting(NativeDatabase.memory());
      bus = IdeEventBus();
      root = p.join(Directory.systemTemp.path, 'aura_ws_root');
    });

    tearDown(() async {
      await bus.close();
      await db.close();
    });

    Future<EditorNotifier> launch({String workspaceId = _ws}) async {
      final notifier = EditorNotifier(
        db,
        bus,
        backupDelay: const Duration(hours: 1),
        rootPathFor: (_) => root,
      );
      bus.publish(WorkspaceOpened(workspaceId));
      await pumpEventQueue();
      return notifier;
    }

    /// Opens [path] with [disk] as its content, as the editor view does after reading the file.
    Future<String> open(EditorNotifier notifier, String path, {String disk = 'v1'}) async {
      await notifier.openTab(tabId: path, path: path, workspaceId: _ws);
      final id = notifier.state.activeTabId!;
      notifier.recordDiskBase(id, disk);
      notifier.setContent(id, disk);
      return id;
    }

    group('one tab per file (DI-06)', () {
      test('the Explorer\'s absolute path and Quick Open\'s relative one are the same tab', () async {
        final notifier = await launch();

        await notifier.openTab(
          tabId: p.join(root, 'lib', 'a.dart'),
          path: p.join(root, 'lib', 'a.dart'),
          workspaceId: _ws,
        );
        await notifier.openTab(tabId: 'lib/a.dart', path: 'lib/a.dart', workspaceId: _ws);

        expect(notifier.state.tabs, hasLength(1));
        expect(notifier.state.tabs.single.path, 'lib/a.dart');
        expect(notifier.state.tabs.single.title, 'a.dart');
      });

      test('a Windows-style path with backslashes is the same tab as the slash form', () async {
        final notifier = await launch();

        await notifier.openTab(tabId: 'lib/a.dart', path: 'lib/a.dart', workspaceId: _ws);
        await notifier.openTab(tabId: r'lib\a.dart', path: r'lib\a.dart', workspaceId: _ws);

        // On POSIX a backslash is a legal filename character, so this equivalence is Windows-only.
        expect(notifier.state.tabs, hasLength(Platform.isWindows ? 1 : 2));
      });

      test('isSameTabPath sees through absolute vs relative spelling', () async {
        final notifier = await launch();
        await notifier.openTab(tabId: 'lib/a.dart', path: 'lib/a.dart', workspaceId: _ws);

        expect(notifier.isSameTabPath(p.join(root, 'lib', 'a.dart'), 'lib/a.dart'), isTrue);
        expect(notifier.isSameTabPath('lib/b.dart', 'lib/a.dart'), isFalse);
      });
    });

    group('rename and delete (DI-02)', () {
      test('renaming a folder retargets the tabs under it, with their buffers', () async {
        final notifier = await launch();
        final a = await open(notifier, 'lib/a.dart', disk: 'A');
        final b = await open(notifier, 'lib/sub/b.dart', disk: 'B');
        notifier.setContent(b, 'B edited');
        await notifier.markDirty(b, true);

        bus.publish(FileRenamed(p.join(root, 'lib'), p.join(root, 'src'), _ws));
        await pumpEventQueue();

        final tabs = notifier.state.tabs;
        expect(tabs.map((t) => t.path), ['src/a.dart', 'src/sub/b.dart']);
        expect(tabs.map((t) => t.title), ['a.dart', 'b.dart']);
        final newA = tabs.first.id;
        final newB = tabs.last.id;
        expect(notifier.contentFor(newA), 'A');
        expect(notifier.contentFor(newB), 'B edited');
        expect(notifier.contentFor(a), isNull, reason: 'the old id no longer holds a buffer');
        expect(notifier.contentFor(b), isNull);
        expect(tabs.last.isDirty, isTrue, reason: 'unsaved edits stay unsaved');
        expect(notifier.state.activeTab?.id, newB, reason: 'the active tab follows the file');
      });

      test('a rename in another workspace is ignored', () async {
        final notifier = await launch();
        await open(notifier, 'lib/a.dart');

        bus.publish(FileRenamed(p.join(root, 'lib'), p.join(root, 'src'), 'some-other-workspace'));
        await pumpEventQueue();

        expect(notifier.state.tabs.single.path, 'lib/a.dart');
      });

      test('deleting a file closes its clean tab but keeps a dirty one', () async {
        final notifier = await launch();
        await open(notifier, 'lib/clean.dart');
        final dirty = await open(notifier, 'lib/dirty.dart');
        notifier.setContent(dirty, 'unsaved work');
        await notifier.markDirty(dirty, true);

        bus.publish(FileDeleted(p.join(root, 'lib'), _ws));
        await pumpEventQueue();

        expect(notifier.state.tabs.map((t) => t.path), ['lib/dirty.dart']);
        expect(notifier.contentFor(dirty), 'unsaved work', reason: 'the only copy is kept');
      });
    });

    test('switching workspace drops the previous workspace\'s buffers (DI-07)', () async {
      final notifier = await launch();
      final id = await open(notifier, 'README.md', disk: "workspace one's readme");

      bus.publish(const WorkspaceOpened('ws-2'));
      await pumpEventQueue();

      expect(notifier.state.workspaceId, 'ws-2');
      expect(notifier.state.tabs, isEmpty);
      expect(notifier.contentFor(id), isNull,
          reason: 'a README.md opened in ws-2 must be read from ws-2, not served from ws-1');
    });

    group('saving (DI-08, ED-09)', () {
      test('what was typed while a save was in flight stays unsaved', () async {
        final notifier = await launch();
        final id = await open(notifier, 'a.txt');
        notifier.setContent(id, 'v2');
        await notifier.markDirty(id, true);

        // The save wrote "v2"; meanwhile the user typed on to "v3".
        notifier.setContent(id, 'v3');
        await notifier.markSaved(id, 'v2');

        expect(notifier.state.tabs.single.isDirty, isTrue);
        expect(notifier.hasDiskChanged(id, 'v2'), isFalse, reason: 'v2 is what is on disk now');

        await notifier.markSaved(id, 'v3');
        expect(notifier.state.tabs.single.isDirty, isFalse);
      });

      test('a file changed on disk is detected against the text the edits were made on', () async {
        final notifier = await launch();
        final id = await open(notifier, 'a.txt', disk: 'as opened');

        expect(notifier.hasDiskChanged(id, 'as opened'), isFalse);
        expect(notifier.hasDiskChanged(id, 'rewritten by a formatter'), isTrue);
      });

      test('reloading from disk replaces the buffer, clears dirty and rebuilds the view', () async {
        final notifier = await launch();
        final id = await open(notifier, 'a.txt', disk: 'old');
        notifier.setContent(id, 'my edits');
        await notifier.markDirty(id, true);
        final revisionBefore = notifier.state.tabs.single.revision;

        notifier.reloadFromDisk(id, 'new on disk');

        expect(notifier.contentFor(id), 'new on disk');
        expect(notifier.state.tabs.single.isDirty, isFalse);
        expect(notifier.state.tabs.single.revision, revisionBefore + 1);
        expect(notifier.hasDiskChanged(id, 'new on disk'), isFalse);
      });
    });

    group('sessions saved before ids were canonical', () {
      test('absolute and duplicate tabs come back as one canonical tab each', () async {
        final abs = p.join(root, 'lib', 'a.dart');
        await db.into(db.desktopEditorState).insert(
              DesktopEditorStateCompanion.insert(
                workspaceId: _ws,
                openTabsJson: jsonEncode([
                  {'id': abs, 'path': abs, 'title': abs},
                  {'id': 'lib/a.dart', 'path': 'lib/a.dart', 'title': 'a.dart'},
                  {'id': 'lib/b.dart', 'path': 'lib/b.dart', 'title': 'b.dart'},
                ]),
                activeTabPath: Value(abs),
              ),
            );

        final notifier = await launch();

        expect(notifier.state.tabs.map((t) => t.path), ['lib/a.dart', 'lib/b.dart']);
        expect(notifier.state.tabs.first.title, 'a.dart');
        expect(notifier.state.activeTab?.path, 'lib/a.dart');
      });
    });
  });

  group('saveEditorTab (DI-08, ED-09)', () {
    late AppDatabase db;
    late IdeEventBus bus;
    late Directory dir;
    late String root;

    setUp(() async {
      await EncryptionService.init();
      db = AppDatabase.forTesting(NativeDatabase.memory());
      bus = IdeEventBus();
      dir = Directory.systemTemp.createTempSync('aura_client_save_');
      root = dir.resolveSymbolicLinksSync();
    });

    tearDown(() async {
      await bus.close();
      await db.close();
      dir.deleteSync(recursive: true);
    });

    /// Runs [body] with a real WidgetRef, a workspace rooted at [root], and file I/O outside the
    /// test's fake-async zone.
    Future<void> withRef(
      WidgetTester tester,
      Future<void> Function(WidgetRef ref, EditorNotifier editor) body,
    ) async {
      late WidgetRef ref;
      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            databaseProvider.overrideWithValue(db),
            ideEventBusProvider.overrideWithValue(bus),
            workspaceNotifierProvider.overrideWith((ref) => WorkspaceNotifier(db, bus)),
            editorNotifierProvider.overrideWith(
              (ref) => EditorNotifier(db, bus, rootPathFor: (_) => root),
            ),
          ],
          child: MaterialApp(
            home: Consumer(builder: (context, r, _) {
              ref = r;
              return const SizedBox.shrink();
            }),
          ),
        ),
      );
      await tester.runAsync(() async {
        await ref.read(workspaceNotifierProvider.notifier).createWorkspace(
              id: _ws,
              rootPath: root,
              displayName: 'ws',
            );
        await pumpEventQueue();
        final editor = ref.read(editorNotifierProvider.notifier);
        await body(ref, editor);
        // A tab left dirty has a backup pending; write it here, in real time. Left for the
        // notifier's dispose at teardown, its database write would be queued in the test's
        // fake-async zone, where it never completes.
        await editor.flushBackups();
      });
    }

    Future<String> openDirty(EditorNotifier editor, String path, {required String disk, required String edited}) async {
      await editor.openTab(tabId: path, path: path, workspaceId: _ws);
      final id = editor.state.activeTabId!;
      editor.recordDiskBase(id, disk);
      editor.setContent(id, edited);
      await editor.markDirty(id, true);
      return id;
    }

    testWidgets('a file changed on disk is not overwritten without being asked', (tester) async {
      await withRef(tester, (ref, editor) async {
        File(p.join(root, 'a.txt')).writeAsStringSync('as opened');
        final id = await openDirty(editor, 'a.txt', disk: 'as opened', edited: 'my edit');
        File(p.join(root, 'a.txt')).writeAsStringSync('rewritten by someone else');

        // No BuildContext: it cannot ask, so it must not overwrite.
        final saved = await saveEditorTab(ref, editor.state.tabs.single);

        expect(saved, isFalse);
        expect(File(p.join(root, 'a.txt')).readAsStringSync(), 'rewritten by someone else');
        expect(editor.state.tabs.single.isDirty, isTrue);
        expect(editor.contentFor(id), 'my edit', reason: 'the user\'s edits are untouched');
      });
    });

    testWidgets('an unchanged file saves, and the tab goes clean', (tester) async {
      await withRef(tester, (ref, editor) async {
        File(p.join(root, 'a.txt')).writeAsStringSync('as opened');
        await openDirty(editor, 'a.txt', disk: 'as opened', edited: 'my edit');

        final saved = await saveEditorTab(ref, editor.state.tabs.single);

        expect(saved, isTrue);
        expect(File(p.join(root, 'a.txt')).readAsStringSync(), 'my edit');
        expect(editor.state.tabs.single.isDirty, isFalse);
      });
    });

    testWidgets('a failed save leaves the tab dirty and recreates no folder', (tester) async {
      await withRef(tester, (ref, editor) async {
        // The tab's folder was removed outside the editor.
        await openDirty(editor, 'gone/a.txt', disk: 'x', edited: 'unsaved');

        final saved = await saveEditorTab(ref, editor.state.tabs.single);

        expect(saved, isFalse);
        expect(editor.state.tabs.single.isDirty, isTrue);
        expect(Directory(p.join(root, 'gone')).existsSync(), isFalse);
      });
    });

    testWidgets('a clean tab whose file changed on disk is reloaded when it is shown again', (tester) async {
      await withRef(tester, (ref, editor) async {
        File(p.join(root, 'a.txt')).writeAsStringSync('v1');
        await editor.openTab(tabId: 'a.txt', path: 'a.txt', workspaceId: _ws);
        final id = editor.state.activeTabId!;
        editor.recordDiskBase(id, 'v1');
        editor.setContent(id, 'v1');

        File(p.join(root, 'a.txt')).writeAsStringSync('v2 from git checkout');
        await editor.refreshIfChangedOnDisk(id);

        expect(editor.contentFor(id), 'v2 from git checkout');
        expect(editor.state.tabs.single.isDirty, isFalse);
      });
    });

    testWidgets('a dirty tab is never reloaded over its edits', (tester) async {
      await withRef(tester, (ref, editor) async {
        File(p.join(root, 'a.txt')).writeAsStringSync('v1');
        final id = await openDirty(editor, 'a.txt', disk: 'v1', edited: 'my edit');

        File(p.join(root, 'a.txt')).writeAsStringSync('v2');
        await editor.refreshIfChangedOnDisk(id);

        expect(editor.contentFor(id), 'my edit');
        expect(editor.state.tabs.single.isDirty, isTrue);
      });
    });
  });
}
