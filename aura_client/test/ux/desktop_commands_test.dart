/// UX_BASELINE_AUDIT.md F-07: the desktop command set behind the palette.
///
/// The invariant worth pinning is the one the audit's other findings keep
/// running into — a control that cannot act must say why, not sit there
/// looking available or quietly vanish.
library;

import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/design_system/components/aura_command_palette.dart';
import 'package:aura_client/core/security/encryption_service.dart';
import 'package:aura_client/desktop/events/ide_event_bus.dart';
import 'package:aura_client/desktop/shell/desktop_commands.dart';
import 'package:aura_client/desktop/state/state_notifiers.dart';
import 'package:drift/native.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late AppDatabase db;
  late IdeEventBus bus;

  setUp(() async {
    await EncryptionService.init();
    db = AppDatabase.forTesting(NativeDatabase.memory());
    bus = IdeEventBus();
  });

  tearDown(() async {
    await bus.close();
    await db.close();
  });

  /// Builds the command set through a real ProviderScope, since that is the
  /// only way to hold a WidgetRef.
  Future<(List<AuraCommandItem>, WidgetRef)> build(
    WidgetTester tester, {
    TargetPlatform platform = TargetPlatform.windows,
  }) async {
    late List<AuraCommandItem> commands;
    late WidgetRef capturedRef;
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          databaseProvider.overrideWithValue(db),
          ideEventBusProvider.overrideWithValue(bus),
          workspaceNotifierProvider.overrideWith((ref) => WorkspaceNotifier(db, bus)),
          layoutNotifierProvider.overrideWith((ref) => LayoutNotifier(db, bus)),
          editorNotifierProvider.overrideWith((ref) => EditorNotifier(db, bus)),
        ],
        child: MaterialApp(
          home: Consumer(
            builder: (context, ref, _) {
              capturedRef = ref;
              commands = buildDesktopCommands(ref, platform: platform);
              return const SizedBox.shrink();
            },
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    return (commands, capturedRef);
  }

  AuraCommandItem byId(List<AuraCommandItem> commands, String id) =>
      commands.firstWhere((c) => c.id == id);

  testWidgets('every disabled command explains itself', (tester) async {
    final (commands, _) = await build(tester);

    for (final command in commands) {
      if (!command.isEnabled) {
        expect(
          command.disabledReason,
          isNotNull,
          reason: '${command.id} is disabled with no reason given',
        );
        expect(command.disabledReason, isNotEmpty, reason: command.id);
      }
    }
  });

  testWidgets('command ids are unique', (tester) async {
    final (commands, _) = await build(tester);
    expect(commands.map((c) => c.id).toSet(), hasLength(commands.length));
  });

  testWidgets('with no workspace, workspace commands are listed but unavailable',
      (tester) async {
    final (commands, _) = await build(tester);

    // Listed, not hidden: a palette that omits these teaches the user they do
    // not exist, rather than what to do first.
    expect(byId(commands, 'file.goToFile').isEnabled, isFalse);
    expect(byId(commands, 'file.goToFile').disabledReason, 'No workspace is open');
    expect(byId(commands, 'git.refresh').isEnabled, isFalse);
  });

  testWidgets('opening a workspace enables them', (tester) async {
    final (_, ref) = await build(tester);
    await ref.read(workspaceNotifierProvider.notifier).createWorkspace(
          id: 'ws-1',
          rootPath: '/tmp/ws-1',
          displayName: 'ws-1',
        );
    await tester.pumpAndSettle();

    final (commands, _) = await build(tester);
    expect(byId(commands, 'file.goToFile').isEnabled, isTrue);
    expect(byId(commands, 'git.refresh').isEnabled, isTrue);
  });

  testWidgets('with no file open, file commands say so', (tester) async {
    final (commands, _) = await build(tester);

    for (final id in const ['file.save', 'file.closeTab', 'aura.inlineCompletion']) {
      expect(byId(commands, id).isEnabled, isFalse, reason: id);
      expect(byId(commands, id).disabledReason, 'No file is open', reason: id);
    }
  });

  testWidgets('layout commands are always available — they need nothing', (tester) async {
    final (commands, _) = await build(tester);

    for (final id in const [
      'view.explorer',
      'view.sourceControl',
      'view.controlCenter',
      'view.toggleSidebar',
      'view.togglePanel',
      'view.focusTerminal',
      'aura.toggleAgentPanel',
    ]) {
      expect(byId(commands, id).isEnabled, isTrue, reason: id);
    }
  });

  testWidgets('a toggle is titled for what it will do, not for what it is',
      (tester) async {
    final (before, ref) = await build(tester);
    // The sidebar starts visible, so the command offers to hide it.
    expect(byId(before, 'view.toggleSidebar').title, 'Hide Side Bar');

    ref.read(layoutNotifierProvider.notifier).togglePrimarySidebar();
    await tester.pumpAndSettle();

    final (after, _) = await build(tester);
    expect(byId(after, 'view.toggleSidebar').title, 'Show Side Bar');
  });

  testWidgets('shortcut hints name the platform modifier', (tester) async {
    final (windows, _) = await build(tester, platform: TargetPlatform.windows);
    expect(byId(windows, 'file.goToFile').trailing, 'Ctrl+P');

    final (mac, _) = await build(tester, platform: TargetPlatform.macOS);
    expect(byId(mac, 'file.goToFile').trailing, 'Cmd+P');
  });

  test('modifierLabel', () {
    expect(modifierLabel(TargetPlatform.macOS), 'Cmd');
    expect(modifierLabel(TargetPlatform.windows), 'Ctrl');
    expect(modifierLabel(TargetPlatform.linux), 'Ctrl');
  });
}
