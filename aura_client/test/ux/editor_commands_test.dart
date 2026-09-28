/// UX_BASELINE_AUDIT.md F-08: the flutter_code_editor controller accepted
/// five commands it does not implement and returned normally — an abstraction
/// advertising capabilities its only implementation does not provide.
///
/// No widget dispatches them today, so nothing was visibly broken. The risk is
/// the next change: wiring one to a context-menu item would ship a menu entry
/// that silently does nothing. These pin the loud failure that replaced the
/// silent one — and pin the three commands that no-op on purpose, so they are
/// not swept up with it.
library;

import 'package:flutter_test/flutter_test.dart';

import 'package:aura_client/desktop/editor/editor_abstraction.dart';
import 'package:aura_client/desktop/editor/providers/flutter_code_editor_provider.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late EditorController controller;

  setUp(() {
    controller = FlutterCodeEditorProvider().createController(
      InMemoryEditorDocument(id: 'doc-1', initialText: 'void main() {}\n', languageId: 'dart'),
    );
  });

  tearDown(() => controller.dispose());

  group('unimplemented commands fail loudly', () {
    for (final command in const <EditorCommand>[
      FormatCommand(),
      GoToDefinitionCommand(),
      FindReferencesCommand(),
      RenameSymbolCommand('newName'),
      CodeActionCommand(),
    ]) {
      test('${command.runtimeType} throws instead of silently doing nothing', () {
        expect(
          () => controller.executeCommand(command),
          throwsA(isA<UnimplementedError>()),
        );
      });
    }
  });

  group('deliberately-no-op commands still return normally', () {
    // Save is handled by the UI, and CodeField binds undo/redo to Flutter's
    // own UndoTextIntent/RedoTextIntent — these three do nothing here on
    // purpose, which is not the same as the five above.
    for (final command in const <EditorCommand>[
      SaveCommand(),
      UndoCommand(),
      RedoCommand(),
    ]) {
      test('${command.runtimeType} returns normally', () async {
        await expectLater(controller.executeCommand(command), completes);
      });
    }
  });
}
