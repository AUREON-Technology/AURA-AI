/// Editor tab model for Desktop IDE.
///
/// Immutable tab state with all editor-relevant fields.
library;

import 'package:flutter/foundation.dart';

import 'package:aura_client/desktop/editor/editor_abstraction.dart';

/// Immutable editor tab.
@immutable
class EditorTab {
  const EditorTab({
    required this.id,
    required this.path,
    required this.title,
    this.isDirty = false,
    this.isPinned = false,
    this.viewState,
    this.cursorPosition,
    this.selection,
    required this.lastAccessed,
    this.languageId,
    this.revision = 0,
  });

  /// Bumped when the buffer is replaced from outside the editor (a reload from disk), so the
  /// editor view -- keyed on it -- is rebuilt around the new text instead of keeping the old.
  final int revision;

  /// Unique tab identifier.
  final String id;

  /// File system path (relative to workspace root).
  final String path;

  /// Display title (usually filename).
  final String title;

  /// Whether the tab has unsaved changes.
  final bool isDirty;

  /// Whether the tab is pinned (cannot be closed by "close all").
  final bool isPinned;

  /// Editor-specific view state (scroll position, folds, etc.).
  final Map<String, dynamic>? viewState;

  /// Current cursor position.
  final EditorCursor? cursorPosition;

  /// Current selection.
  final EditorSelection? selection;

  /// Last access timestamp (for MRU ordering).
  final DateTime lastAccessed;

  /// Language mode identifier.
  final String? languageId;

  EditorTab copyWith({
    String? id,
    String? path,
    String? title,
    bool? isDirty,
    bool? isPinned,
    Map<String, dynamic>? viewState,
    EditorCursor? cursorPosition,
    EditorSelection? selection,
    DateTime? lastAccessed,
    String? languageId,
    int? revision,
  }) {
    return EditorTab(
      id: id ?? this.id,
      path: path ?? this.path,
      title: title ?? this.title,
      isDirty: isDirty ?? this.isDirty,
      isPinned: isPinned ?? this.isPinned,
      viewState: viewState ?? this.viewState,
      cursorPosition: cursorPosition ?? this.cursorPosition,
      selection: selection ?? this.selection,
      lastAccessed: lastAccessed ?? this.lastAccessed,
      languageId: languageId ?? this.languageId,
      revision: revision ?? this.revision,
    );
  }

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is EditorTab &&
          runtimeType == other.runtimeType &&
          id == other.id &&
          path == other.path &&
          title == other.title &&
          isDirty == other.isDirty &&
          isPinned == other.isPinned &&
          lastAccessed == other.lastAccessed &&
          revision == other.revision;

  @override
  int get hashCode => Object.hash(id, path, title, isDirty, isPinned, lastAccessed, revision);

  @override
  String toString() => 'EditorTab(id: $id, path: $path, title: $title, isDirty: $isDirty, isPinned: $isPinned)';
}