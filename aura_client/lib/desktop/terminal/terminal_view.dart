/// Live, interactive terminal view for Desktop IDE (D4).
///
/// Replaces `bottom_panel.dart`'s `_TerminalPlaceholder`. Visual style copied
/// from `SandboxTerminalView` (dark monospace block) -- that class itself is
/// a different, read-only widget in the chat feature, not reused directly.
library;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/desktop/state/state_notifiers.dart';

const _bg = Color(0xFF14161B);
const _fg = Color(0xFFD4D4D4);

class TerminalView extends ConsumerStatefulWidget {
  const TerminalView({super.key});

  @override
  ConsumerState<TerminalView> createState() => _TerminalViewState();
}

class _TerminalViewState extends ConsumerState<TerminalView> {
  final _focusNode = FocusNode();
  final _scrollController = ScrollController();
  String? _startedForWorkspaceId;
  double? _charWidth;
  double? _charHeight;
  int _lastCols = 0;
  int _lastRows = 0;
  DateTime? _lastResizeSent;

  @override
  void dispose() {
    _focusNode.dispose();
    _scrollController.dispose();
    super.dispose();
  }

  void _scrollToBottom() {
    if (!_scrollController.hasClients) return;
    _scrollController.jumpTo(_scrollController.position.maxScrollExtent);
  }

  void _maybeAutoStart(String workspaceId, String rootPath, bool trusted) {
    if (_startedForWorkspaceId == workspaceId) return;
    final terminal = ref.read(terminalNotifierProvider);
    if (terminal.sessions.any((s) => s.workspaceId == workspaceId)) {
      _startedForWorkspaceId = workspaceId;
      return;
    }
    if (!trusted) return;
    _startedForWorkspaceId = workspaceId;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      ref.read(terminalNotifierProvider.notifier).start(workspaceId: workspaceId, rootPath: rootPath);
    });
  }

  void _measureCharSize() {
    if (_charWidth != null) return;
    final painter = TextPainter(
      text: const TextSpan(text: 'M', style: TextStyle(fontFamily: 'Courier', fontSize: 12, height: 1.4)),
      textDirection: TextDirection.ltr,
    )..layout();
    _charWidth = painter.width;
    _charHeight = painter.height;
  }

  void _handleResize(BoxConstraints constraints, String sessionId) {
    _measureCharSize();
    if (_charWidth == null || _charHeight == null) return;
    final cols = (constraints.maxWidth / _charWidth!).floor().clamp(20, 500);
    final rows = (constraints.maxHeight / _charHeight!).floor().clamp(5, 200);
    if (cols == _lastCols && rows == _lastRows) return;
    _lastCols = cols;
    _lastRows = rows;
    final now = DateTime.now();
    if (_lastResizeSent != null && now.difference(_lastResizeSent!) < const Duration(milliseconds: 150)) {
      return;
    }
    _lastResizeSent = now;
    ref.read(terminalNotifierProvider.notifier).resize(sessionId, cols, rows);
  }

  KeyEventResult _handleKey(FocusNode node, KeyEvent event, String sessionId) {
    if (event is! KeyDownEvent && event is! KeyRepeatEvent) return KeyEventResult.ignored;
    final bytes = _translateKey(event);
    if (bytes == null) return KeyEventResult.ignored;
    ref.read(terminalNotifierProvider.notifier).sendInput(sessionId, bytes);
    return KeyEventResult.handled;
  }

  /// Translates a subset of keys to the VT byte sequences a shell expects --
  /// a `TextField` can't do this (line-buffered, no per-keystroke raw
  /// events). A full xterm keymap (Home/End/PageUp/F-keys/Alt combos) is an
  /// explicit v1 scope cut, not an oversight (see D4 plan section B4).
  Uint8List? _translateKey(KeyEvent event) {
    final key = event.logicalKey;
    if (HardwareKeyboard.instance.isControlPressed) {
      if (key == LogicalKeyboardKey.keyC) return Uint8List.fromList([0x03]);
      if (key == LogicalKeyboardKey.keyD) return Uint8List.fromList([0x04]);
    }
    if (key == LogicalKeyboardKey.enter || key == LogicalKeyboardKey.numpadEnter) {
      return Uint8List.fromList([0x0D]);
    }
    if (key == LogicalKeyboardKey.backspace) return Uint8List.fromList([0x7F]);
    if (key == LogicalKeyboardKey.tab) return Uint8List.fromList([0x09]);
    if (key == LogicalKeyboardKey.arrowUp) return Uint8List.fromList([0x1B, 0x5B, 0x41]);
    if (key == LogicalKeyboardKey.arrowDown) return Uint8List.fromList([0x1B, 0x5B, 0x42]);
    if (key == LogicalKeyboardKey.arrowRight) return Uint8List.fromList([0x1B, 0x5B, 0x43]);
    if (key == LogicalKeyboardKey.arrowLeft) return Uint8List.fromList([0x1B, 0x5B, 0x44]);
    final char = event.character;
    if (char != null && char.isNotEmpty) {
      return Uint8List.fromList(char.codeUnits);
    }
    return null;
  }

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final workspace = ref.watch(workspaceNotifierProvider).activeWorkspace;
    final hostdRoots = ref.watch(workspaceNotifierProvider).hostdRoots;

    if (workspace == null) {
      return _CenteredMessage(text: 'Open a workspace to use the terminal.', palette: palette);
    }

    final rootPath = workspace.rootPath;
    final matchingRoot = hostdRoots.where((r) => r.path == rootPath).firstOrNull;
    final trusted = matchingRoot?.trusted ?? false;

    _maybeAutoStart(workspace.id, rootPath, trusted);

    final terminal = ref.watch(terminalNotifierProvider);
    final session = terminal.sessions.where((s) => s.workspaceId == workspace.id).firstOrNull;

    ref.listen(terminalNotifierProvider, (previous, next) {
      final prevOutput = previous?.sessions.where((s) => s.id == session?.id).firstOrNull?.output;
      final nextOutput = next.sessions.where((s) => s.id == session?.id).firstOrNull?.output;
      if (prevOutput != nextOutput) {
        WidgetsBinding.instance.addPostFrameCallback((_) {
          if (mounted) _scrollToBottom();
        });
      }
    });

    if (session == null) {
      if (matchingRoot == null) {
        return _CenteredMessage(text: 'Grant this workspace to the host daemon to use the terminal.', palette: palette);
      }
      if (!trusted) {
        return _CenteredMessage(
          text: 'Workspace is not trusted. Trust it from the Host panel to use the terminal.',
          palette: palette,
        );
      }
      return _CenteredMessage(text: 'Starting terminal...', palette: palette);
    }

    return Container(
      color: _bg,
      child: LayoutBuilder(
        builder: (context, constraints) {
          _handleResize(constraints, session.id);
          return Focus(
            focusNode: _focusNode,
            autofocus: true,
            onKeyEvent: (node, event) => _handleKey(node, event, session.id),
            child: GestureDetector(
              onTap: () => _focusNode.requestFocus(),
              child: SingleChildScrollView(
                controller: _scrollController,
                padding: const EdgeInsets.all(AuraSpacing.sm),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    SelectableText(
                      session.output.isEmpty ? ' ' : session.output,
                      style: const TextStyle(fontFamily: 'Courier', fontSize: 12, color: _fg, height: 1.4),
                    ),
                    if (!session.isRunning)
                      Padding(
                        padding: const EdgeInsets.only(top: AuraSpacing.sm),
                        child: Row(
                          children: [
                            Text(
                              'Session ended${session.exitCode != null ? ' (exit code ${session.exitCode})' : ''}.',
                              style: TextStyle(color: palette.textDisabled, fontSize: 12),
                            ),
                            const SizedBox(width: AuraSpacing.sm),
                            TextButton(
                              onPressed: () => ref
                                  .read(terminalNotifierProvider.notifier)
                                  .restart(session.id, rootPath: rootPath),
                              child: const Text('Restart'),
                            ),
                          ],
                        ),
                      ),
                  ],
                ),
              ),
            ),
          );
        },
      ),
    );
  }
}

class _CenteredMessage extends StatelessWidget {
  const _CenteredMessage({required this.text, required this.palette});

  final String text;
  final AuraPalette palette;

  @override
  Widget build(BuildContext context) {
    return Container(
      color: _bg,
      child: Center(
        child: Padding(
          padding: const EdgeInsets.all(AuraSpacing.lg),
          child: Text(
            text,
            textAlign: TextAlign.center,
            style: const TextStyle(color: Color(0xFF808080), fontFamily: 'Courier', fontSize: 13),
          ),
        ),
      ),
    );
  }
}
