/// flutter_code_editor provider implementation for Desktop IDE.
///
/// Initial editor provider using flutter_code_editor + highlight packages.
library;

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_code_editor/flutter_code_editor.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:highlight/highlight.dart' show Mode;
import 'package:highlight/languages/dart.dart' as lang_dart;
import 'package:highlight/languages/python.dart' as lang_python;
import 'package:highlight/languages/javascript.dart' as lang_javascript;
import 'package:highlight/languages/typescript.dart' as lang_typescript;
import 'package:highlight/languages/json.dart' as lang_json;
import 'package:highlight/languages/yaml.dart' as lang_yaml;
import 'package:highlight/languages/markdown.dart' as lang_markdown;
import 'package:highlight/languages/shell.dart' as lang_shell;
import 'package:highlight/languages/powershell.dart' as lang_powershell;
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/desktop/editor/editor_abstraction.dart';

/// Maps a language id (as used by [LanguageMode.id]) to its `highlight` [Mode].
/// `null` means render as plain text (no syntax highlighting).
final Map<String, Mode?> _highlightModes = {
  'dart': lang_dart.dart,
  'python': lang_python.python,
  'javascript': lang_javascript.javascript,
  'typescript': lang_typescript.typescript,
  'json': lang_json.json,
  'yaml': lang_yaml.yaml,
  'markdown': lang_markdown.markdown,
  'shell': lang_shell.shell,
  'powershell': lang_powershell.powershell,
  'plaintext': null,
};

final Map<String, LanguageMode> _languageModes = {
  'dart': const LanguageMode(
    id: 'dart',
    name: 'Dart',
    extensions: ['dart'],
    mimeTypes: ['application/dart'],
  ),
  'py': const LanguageMode(
    id: 'python',
    name: 'Python',
    extensions: ['py', 'pyw'],
    mimeTypes: ['text/x-python'],
  ),
  'js': const LanguageMode(
    id: 'javascript',
    name: 'JavaScript',
    extensions: ['js', 'jsx', 'mjs'],
    mimeTypes: ['text/javascript'],
  ),
  'ts': const LanguageMode(
    id: 'typescript',
    name: 'TypeScript',
    extensions: ['ts', 'tsx'],
    mimeTypes: ['text/typescript'],
  ),
  'json': const LanguageMode(
    id: 'json',
    name: 'JSON',
    extensions: ['json'],
    mimeTypes: ['application/json'],
  ),
  'yaml': const LanguageMode(
    id: 'yaml',
    name: 'YAML',
    extensions: ['yaml', 'yml'],
    mimeTypes: ['text/yaml'],
  ),
  'md': const LanguageMode(
    id: 'markdown',
    name: 'Markdown',
    extensions: ['md', 'markdown'],
    mimeTypes: ['text/markdown'],
  ),
  'sh': const LanguageMode(
    id: 'shell',
    name: 'Shell',
    extensions: ['sh', 'bash'],
    mimeTypes: ['application/x-sh'],
  ),
  'ps1': const LanguageMode(
    id: 'powershell',
    name: 'PowerShell',
    extensions: ['ps1'],
    mimeTypes: ['text/x-powershell'],
  ),
  'plaintext': const LanguageMode(
    id: 'plaintext',
    name: 'Plain Text',
    extensions: ['txt', 'log'],
    mimeTypes: ['text/plain'],
  ),
};

/// Provider for flutter_code_editor implementation.
class FlutterCodeEditorProvider implements EditorProvider {
  FlutterCodeEditorProvider();

  @override
  EditorController createController(EditorDocument document) {
    return _FlutterCodeEditorController(document);
  }

  @override
  LanguageMode languageModeForPath(String path) {
    final ext = path.contains('.') ? path.split('.').last.toLowerCase() : '';
    return _languageModes[ext] ?? _languageModes['plaintext']!;
  }

  @override
  List<LanguageMode> get supportedLanguages => _languageModes.values.toSet().toList();
}

class _FlutterCodeEditorController implements EditorController {
  _FlutterCodeEditorController(this.document) {
    _codeController = CodeController(
      text: document.text,
      language: _highlightModes[document.languageId],
    );
    _codeController.addListener(_handleControllerChanged);
  }

  @override
  final EditorDocument document;

  late final CodeController _codeController;
  EditorSelection _selection = const EditorSelection(start: 0, end: 0);
  EditorCursor _cursor = const EditorCursor(offset: 0);

  /// The underlying [CodeController], exposed so the widget layer can bind
  /// a [CodeField] to it without re-creating editor state.
  CodeController get codeController => _codeController;

  @override
  EditorSelection get selection => _selection;

  @override
  EditorCursor get cursor => _cursor;

  @override
  Stream<EditorDocument> get onDocumentChange => _documentController.stream;

  @override
  Stream<EditorSelection> get onSelectionChange => _selectionController.stream;

  final StreamController<EditorDocument> _documentController = StreamController<EditorDocument>.broadcast();
  final StreamController<EditorSelection> _selectionController = StreamController<EditorSelection>.broadcast();

  void _handleControllerChanged() {
    final selection = _codeController.selection;
    if (selection.start < 0 || selection.end < 0) return;
    _selection = EditorSelection(start: selection.start, end: selection.end);
    _cursor = EditorCursor(offset: selection.end);
    _selectionController.add(_selection);
  }

  @override
  Future<void> executeCommand(EditorCommand command) async {
    switch (command) {
      case SaveCommand():
        // Handled by UI
        break;
      case UndoCommand():
        _codeController.undo();
        break;
      case RedoCommand():
        _codeController.redo();
        break;
      case FormatCommand():
        // TODO: Implement formatting
        break;
      case GoToDefinitionCommand():
        // TODO: Implement go to definition
        break;
      case FindReferencesCommand():
        // TODO: Implement find references
        break;
      case RenameSymbolCommand():
        // TODO: Implement rename
        break;
      case CodeActionCommand():
        // TODO: Implement code actions
        break;
    }
  }

  @override
  Future<void> setText(String text) async {
    _codeController.text = text;
    _documentController.add(document.copyWith(text: text));
  }

  @override
  Future<void> setSelection(EditorSelection selection) async {
    _selection = selection;
    _codeController.selection = TextSelection(
      baseOffset: selection.start,
      extentOffset: selection.end,
    );
    _selectionController.add(selection);
  }

  @override
  Future<void> setCursor(EditorCursor cursor) async {
    _cursor = cursor;
    _codeController.selection = TextSelection.collapsed(offset: cursor.offset);
    _selection = EditorSelection(start: cursor.offset, end: cursor.offset);
    _selectionController.add(_selection);
  }

  @override
  void dispose() {
    _codeController.removeListener(_handleControllerChanged);
    _codeController.dispose();
    _documentController.close();
    _selectionController.close();
  }
}

class FlutterCodeEditorWidget extends StatefulWidget {
  const FlutterCodeEditorWidget({
    super.key,
    required this.controller,
    required this.onTextChange,
    required this.onSelectionChange,
    required this.onCursorChange,
  });

  final EditorController controller;
  final void Function(String) onTextChange;
  final void Function(EditorSelection) onSelectionChange;
  final void Function(EditorCursor) onCursorChange;

  @override
  State<FlutterCodeEditorWidget> createState() => _FlutterCodeEditorWidgetState();
}

class _FlutterCodeEditorWidgetState extends State<FlutterCodeEditorWidget> {
  late CodeController _codeController;
  late CodeTheme _theme;

  @override
  void initState() {
    super.initState();
    final controller = widget.controller;
    if (controller is! _FlutterCodeEditorController) {
      throw ArgumentError(
        'FlutterCodeEditorWidget requires a controller created by FlutterCodeEditorProvider',
      );
    }
    _codeController = controller.codeController;
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _theme = _createTheme();
  }

  CodeTheme _createTheme() {
    final palette = AuraPalette.of(context);
    return CodeTheme(
      styles: {
        'root': TextStyle(
          fontFamily: 'Courier',
          fontSize: 13,
          color: palette.textPrimary,
        ),
        'keyword': const TextStyle(color: Color(0xFFC678DD), fontWeight: FontWeight.bold),
        'string': const TextStyle(color: Color(0xFF98C379)),
        'comment': const TextStyle(color: Color(0xFF5C6370), fontStyle: FontStyle.italic),
        'number': const TextStyle(color: Color(0xFFD19A66)),
        'function': const TextStyle(color: Color(0xFF61AFEF)),
        'class': const TextStyle(color: Color(0xFFE5C07B), fontWeight: FontWeight.bold),
        'punctuation': TextStyle(color: palette.textSecondary),
        'operator': const TextStyle(color: Color(0xFFC678DD)),
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    return CodeField(
      controller: _codeController,
      onChanged: widget.onTextChange,
      textStyle: const TextStyle(fontFamily: 'Courier', fontSize: 13),
    );
  }
}

/// Riverpod provider for the editor provider.
final editorProviderProvider = Provider<EditorProvider>((ref) {
  return FlutterCodeEditorProvider();
});
