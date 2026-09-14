/// Editor abstraction for AURA Desktop IDE.
///
/// Defines the interface that any editor provider must implement.
/// This prevents lock-in to a specific editor package (flutter_code_editor, Monaco, etc.)
/// and allows swapping the underlying implementation without rewriting the IDE.
library;

import 'package:flutter/material.dart';

/// Represents a document in the editor.
abstract class EditorDocument {
  const EditorDocument();

  /// Unique identifier for this document.
  String get id;

  /// File system path (if backed by a file).
  String? get path;

  /// Current text content.
  String get text;

  /// Document version (for change tracking).
  int get version;

  /// Language mode identifier (e.g., 'dart', 'python', 'markdown').
  String get languageId;

  /// Whether the document has unsaved changes.
  bool get isDirty;

  /// Creates a copy with updated content.
  EditorDocument copyWith({String? text, int? version, bool? isDirty});
}

/// Represents a text selection in the editor.
@immutable
class EditorSelection {
  const EditorSelection({
    required this.start,
    required this.end,
  });

  /// Start offset (inclusive).
  final int start;

  /// End offset (exclusive).
  final int end;

  /// Whether the selection is empty (cursor only).
  bool get isEmpty => start == end;

  /// Whether the selection is reversed (start > end).
  bool get isReversed => start > end;

  /// Normalized selection (start <= end).
  EditorSelection get normalized => isReversed
      ? EditorSelection(start: end, end: start)
      : this;

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is EditorSelection &&
          runtimeType == other.runtimeType &&
          start == other.start &&
          end == other.end;

  @override
  int get hashCode => Object.hash(start, end);

  @override
  String toString() => 'EditorSelection(start: $start, end: $end)';
}

/// Represents a cursor position in the editor.
@immutable
class EditorCursor {
  const EditorCursor({
    required this.offset,
    this.affinity = TextAffinity.downstream,
  });

  /// Character offset from the start of the document.
  final int offset;

  /// Affinity for cursor placement at boundaries.
  final TextAffinity affinity;

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is EditorCursor &&
          runtimeType == other.runtimeType &&
          offset == other.offset &&
          affinity == other.affinity;

  @override
  int get hashCode => Object.hash(offset, affinity);

  @override
  String toString() => 'EditorCursor(offset: $offset, affinity: $affinity)';
}

/// Editor commands that can be executed.
abstract class EditorCommand {
  const EditorCommand();
}

/// Save the current document.
class SaveCommand extends EditorCommand {
  const SaveCommand();
}

/// Undo the last change.
class UndoCommand extends EditorCommand {
  const UndoCommand();
}

/// Redo the last undone change.
class RedoCommand extends EditorCommand {
  const RedoCommand();
}

/// Format the document.
class FormatCommand extends EditorCommand {
  const FormatCommand();
}

/// Go to definition of symbol at cursor.
class GoToDefinitionCommand extends EditorCommand {
  const GoToDefinitionCommand();
}

/// Find references to symbol at cursor.
class FindReferencesCommand extends EditorCommand {
  const FindReferencesCommand();
}

/// Rename symbol at cursor.
class RenameSymbolCommand extends EditorCommand {
  const RenameSymbolCommand(this.newName);
  final String newName;
}

/// Trigger code actions (quick fixes, refactors).
class CodeActionCommand extends EditorCommand {
  const CodeActionCommand();
}

/// D6 Part D: request one inline completion at the current cursor position.
///
/// Deliberately a separate command from [CodeActionCommand] -- a completion is keyed to a
/// cursor position and has no diagnostic behind it, whereas a code action is keyed to a
/// diagnostic/selection and offers a menu of fixes. Explicit-trigger only (see
/// `docs/architecture/015-desktop-ide.md`'s D6 plan, Part D decision 3): this command fires once
/// per keybinding press, never continuously as the user types.
class RequestInlineCompletionCommand extends EditorCommand {
  const RequestInlineCompletionCommand();
}

/// Language mode configuration.
@immutable
class LanguageMode {
  const LanguageMode({
    required this.id,
    required this.name,
    required this.extensions,
    this.mimeTypes = const [],
  });

  final String id;
  final String name;
  final List<String> extensions;
  final List<String> mimeTypes;
}

/// Abstract editor controller interface.
abstract class EditorController {
  /// The document being edited.
  EditorDocument get document;

  /// Current selection.
  EditorSelection get selection;

  /// Current cursor position.
  EditorCursor get cursor;

  /// Stream of document changes.
  Stream<EditorDocument> get onDocumentChange;

  /// Stream of selection changes.
  Stream<EditorSelection> get onSelectionChange;

  /// Executes an editor command.
  Future<void> executeCommand(EditorCommand command);

  /// Sets the document content.
  Future<void> setText(String text);

  /// Sets the selection.
  Future<void> setSelection(EditorSelection selection);

  /// Sets the cursor position.
  Future<void> setCursor(EditorCursor cursor);

  /// Disposes the controller.
  void dispose();
}

/// Provider for creating editor controllers.
abstract class EditorProvider {
  /// Creates a controller for the given document.
  EditorController createController(EditorDocument document);

  /// Returns the language mode for a file path.
  LanguageMode languageModeForPath(String path);

  /// Returns all supported language modes.
  List<LanguageMode> get supportedLanguages;
}

/// Simple in-memory document implementation.
class InMemoryEditorDocument extends EditorDocument {
  InMemoryEditorDocument({
    required this.id,
    this.path,
    required String initialText,
    this.languageId = 'plaintext',
    this.version = 0,
    this.isDirty = false,
  }) : _text = initialText;

  @override
  final String id;

  @override
  final String? path;

  @override
  final String languageId;

  @override
  final int version;

  @override
  final bool isDirty;

  final String _text;

  @override
  String get text => _text;

  @override
  InMemoryEditorDocument copyWith({
    String? text,
    int? version,
    bool? isDirty,
  }) {
    return InMemoryEditorDocument(
      id: id,
      path: path,
      initialText: text ?? _text,
      languageId: languageId,
      version: version ?? this.version + 1,
      isDirty: isDirty ?? this.isDirty,
    );
  }
}