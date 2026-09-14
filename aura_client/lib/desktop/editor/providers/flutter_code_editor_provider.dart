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
import 'package:aura_client/core/ai/inline_completion_client.dart';
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

/// Signature for requesting one inline completion -- injected rather than read from a Riverpod
/// `Ref` directly, since [EditorController]/[EditorProvider] are meant to be usable without
/// Riverpod (see `editor_abstraction.dart`'s own doc comment on swappable editor backends).
/// `editorProviderProvider` below is what actually wires this to [InlineCompletionClient].
typedef InlineCompletionRequester = Future<InlineCompletionResult?> Function({
  required String prefix,
  required String suffix,
  String? language,
});

/// Provider for flutter_code_editor implementation.
class FlutterCodeEditorProvider implements EditorProvider {
  FlutterCodeEditorProvider({this.requestInlineCompletion});

  /// `null` (the default) makes [RequestInlineCompletionCommand] a no-op -- useful for tests or
  /// any host that constructs this provider without a backend to call.
  final InlineCompletionRequester? requestInlineCompletion;

  @override
  EditorController createController(EditorDocument document) {
    return _FlutterCodeEditorController(document, requestInlineCompletion: requestInlineCompletion);
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
  _FlutterCodeEditorController(this.document, {this.requestInlineCompletion}) {
    _codeController = CodeController(
      text: document.text,
      language: _highlightModes[document.languageId],
    );
    _codeController.addListener(_handleControllerChanged);
  }

  @override
  final EditorDocument document;

  final InlineCompletionRequester? requestInlineCompletion;

  /// Backend caps (`CodeCompletionRequest` in `aura_core/api/v3.py`) are 8000/4000 chars --
  /// slicing to the same bound here means a completion request never 422s on size and the
  /// prompt sent to the model matches what a person would actually see around the cursor.
  static const _maxPrefixChars = 8000;
  static const _maxSuffixChars = 4000;

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
        // CodeField binds Ctrl+Z/Ctrl+Shift+Z to Flutter's own
        // UndoTextIntent/RedoTextIntent internally (via its undo/redo
        // history), so there's nothing for this command to drive directly.
        break;
      case RedoCommand():
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
      case RequestInlineCompletionCommand():
        await _requestInlineCompletion();
        break;
    }
  }

  /// D6 Part D. Slices the document at the cursor, asks the backend for one completion, and
  /// inserts it as a *selected* text span (rather than a floating ghost-text overlay --
  /// `flutter_code_editor`'s `CodeController` has no such rendering primitive, and building one
  /// is out of scope here): the user sees exactly what would be inserted, highlighted, and can
  /// either move on (implicit accept) or immediately overwrite it by typing (reject).
  Future<void> _requestInlineCompletion() async {
    final requester = requestInlineCompletion;
    if (requester == null) return;

    final requestSelection = _codeController.selection;
    if (!requestSelection.isValid || !requestSelection.isCollapsed) return;
    final cursorOffset = requestSelection.baseOffset;
    final requestText = _codeController.text;

    final prefix = requestText.substring(
      (cursorOffset - _maxPrefixChars).clamp(0, cursorOffset),
      cursorOffset,
    );
    final suffix = requestText.substring(
      cursorOffset,
      (cursorOffset + _maxSuffixChars).clamp(cursorOffset, requestText.length),
    );

    InlineCompletionResult? result;
    try {
      result = await requester(prefix: prefix, suffix: suffix, language: document.languageId);
    } catch (_) {
      return;
    }
    if (result == null || result.text.isEmpty) return;

    // Stale-response guard: if the cursor moved or the document changed since the request was
    // sent, `cursorOffset` no longer names the place the user was looking at -- drop the
    // response rather than inserting somewhere the user didn't ask for (D6 plan, Part D).
    final currentSelection = _codeController.selection;
    if (!currentSelection.isValid ||
        !currentSelection.isCollapsed ||
        currentSelection.baseOffset != cursorOffset ||
        _codeController.text != requestText) {
      return;
    }

    final newText = requestText.replaceRange(cursorOffset, cursorOffset, result.text);
    _codeController.value = _codeController.value.copyWith(
      text: newText,
      selection: TextSelection(baseOffset: cursorOffset, extentOffset: cursorOffset + result.text.length),
      composing: TextRange.empty,
    );
    _documentController.add(document.copyWith(text: newText));
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

  CodeThemeData _createThemeData() {
    final palette = AuraPalette.of(context);
    return CodeThemeData(
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
    // CodeTheme is an InheritedWidget — CodeField reads its styles via
    // CodeTheme.of(context) rather than a constructor parameter.
    return CodeTheme(
      data: _createThemeData(),
      child: CodeField(
        controller: _codeController,
        onChanged: widget.onTextChange,
        textStyle: const TextStyle(fontFamily: 'Courier', fontSize: 13),
        expands: true,
      ),
    );
  }
}

/// Riverpod provider for the editor provider.
final editorProviderProvider = Provider<EditorProvider>((ref) {
  return FlutterCodeEditorProvider(
    requestInlineCompletion: ({required prefix, required suffix, language}) {
      return ref.read(inlineCompletionClientProvider).complete(
            prefix: prefix,
            suffix: suffix,
            language: language,
          );
    },
  );
});
