/// Code editor view for Desktop IDE.
///
/// Uses the EditorProvider abstraction to support multiple editor implementations.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/desktop/editor/editor_abstraction.dart';
import 'package:aura_client/desktop/editor/editor_tab.dart';
import 'package:aura_client/desktop/editor/providers/flutter_code_editor_provider.dart';
import 'package:aura_client/desktop/state/state_notifiers.dart';
import 'package:aura_client/desktop/workspace/workspace_file_service.dart';

class CodeEditorView extends ConsumerWidget {
  const CodeEditorView({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final editorState = ref.watch(editorNotifierProvider);
    final activeTab = editorState.activeTab;

    if (activeTab == null) {
      return const _EmptyEditorPlaceholder();
    }

    // Keying by tab id ensures the editor state (and its async load) resets
    // when the active tab changes, rather than reusing stale State.
    return _EditorContent(key: ValueKey(activeTab.id), tab: activeTab);
  }
}

class _EmptyEditorPlaceholder extends StatelessWidget {
  const _EmptyEditorPlaceholder();

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    return Center(
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Icon(Icons.code_outlined, size: 64, color: palette.textDisabled),
          const SizedBox(height: AuraSpacing.md),
          Text(
            'No file open',
            style: TextStyle(color: palette.textSecondary, fontSize: 18),
          ),
          const SizedBox(height: AuraSpacing.xs),
          Text(
            'Open a file from the Explorer or press Ctrl+P',
            style: TextStyle(color: palette.textDisabled, fontSize: 12),
          ),
        ],
      ),
    );
  }
}

class _EditorContent extends ConsumerStatefulWidget {
  const _EditorContent({super.key, required this.tab});

  final EditorTab tab;

  @override
  ConsumerState<_EditorContent> createState() => _EditorContentState();
}

class _EditorContentState extends ConsumerState<_EditorContent> {
  EditorController? _controller;
  InMemoryEditorDocument? _document;
  String? _loadError;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _initializeEditor();
  }

  Future<void> _initializeEditor() async {
    final languageId = _getLanguageId(widget.tab.path);
    final cached = ref.read(editorNotifierProvider.notifier).contentFor(widget.tab.id);

    String initialText = cached ?? '';
    String? loadError;
    if (cached == null) {
      final root = ref.read(workspaceNotifierProvider).activeWorkspace?.rootPath;
      if (root != null) {
        try {
          initialText = await WorkspaceFileService(root).readFile(widget.tab.path);
          ref.read(editorNotifierProvider.notifier).setContent(widget.tab.id, initialText);
        } catch (e) {
          loadError = 'Could not read ${widget.tab.path}: $e';
        }
      } else {
        loadError = 'No active workspace';
      }
    }

    if (!mounted) return;
    setState(() {
      _document = InMemoryEditorDocument(
        id: widget.tab.id,
        path: widget.tab.path,
        initialText: initialText,
        languageId: languageId,
      );
      _controller = ref.read(editorProviderProvider).createController(_document!);
      _loadError = loadError;
      _loading = false;
    });
  }

  String _getLanguageId(String path) {
    final ext = path.contains('.') ? path.split('.').last.toLowerCase() : '';
    switch (ext) {
      case 'dart': return 'dart';
      case 'py': return 'python';
      case 'js': return 'javascript';
      case 'ts': return 'typescript';
      case 'json': return 'json';
      case 'yaml':
      case 'yml': return 'yaml';
      case 'md': return 'markdown';
      case 'sh': return 'shell';
      case 'ps1': return 'powershell';
      default: return 'plaintext';
    }
  }

  @override
  void dispose() {
    _controller?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (_loading || _controller == null || _document == null) {
      return const Center(child: CircularProgressIndicator());
    }

    return Container(
      color: AuraPalette.of(context).background,
      child: Column(
        children: [
          if (_loadError != null)
            Container(
              width: double.infinity,
              color: AuraPalette.of(context).danger.withValues(alpha: 0.1),
              padding: const EdgeInsets.symmetric(horizontal: AuraSpacing.md, vertical: AuraSpacing.xs),
              child: Text(
                _loadError!,
                style: TextStyle(color: AuraPalette.of(context).danger, fontSize: 12),
              ),
            ),
          Expanded(
            child: FlutterCodeEditorWidget(
              controller: _controller!,
              onTextChange: _handleTextChange,
              onSelectionChange: _handleSelectionChange,
              onCursorChange: _handleCursorChange,
            ),
          ),
        ],
      ),
    );
  }

  void _handleTextChange(String text) {
    ref.read(editorNotifierProvider.notifier).setContent(widget.tab.id, text);
    if (!widget.tab.isDirty) {
      ref.read(editorNotifierProvider.notifier).markDirty(widget.tab.id, true);
    }
  }

  void _handleSelectionChange(EditorSelection selection) {
    ref.read(editorNotifierProvider.notifier).updateSelection(widget.tab.id, selection);
  }

  void _handleCursorChange(EditorCursor cursor) {
    ref.read(editorNotifierProvider.notifier).updateCursor(widget.tab.id, cursor);
  }
}
