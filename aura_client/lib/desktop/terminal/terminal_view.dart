/// Live, interactive terminal view for Desktop IDE (D4, upgraded to a real VT100 emulator).
///
/// Renders through the `xterm` package's [xterm.Terminal]/[xterm.TerminalView] rather than
/// dumping raw PTY text into a `SelectableText` -- the previous approach showed escape sequences
/// (cursor moves, clears, colors, OSC title-sets) as literal characters instead of interpreting
/// them, since nothing parsed them. `TerminalNotifier` (`state_notifiers.dart`) owns one
/// [xterm.Terminal] per session (`terminalFor`); this widget only renders it and forwards
/// layout/keyboard/mouse events, which the package widget already implements in full -- this
/// also resolves the "full xterm keymap is an explicit v1 scope cut" limitation D4 had, since the
/// package's own input handler covers Home/End/PageUp/F-keys/Alt combos that the old hand-rolled
/// `_translateKey` never did.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:xterm/xterm.dart' as xterm;

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/desktop/state/state_notifiers.dart';
import 'package:aura_client/l10n/l10n.dart';

const _bg = Color(0xFF14161B);
const _fg = Color(0xFFD4D4D4);

/// Matches the rest of the Desktop IDE's dark chrome rather than the package's own
/// `TerminalThemes.defaultTheme` (a very similar but not identical near-black) -- the ANSI
/// 16-color entries are the package's own defaults, since Aura has no design-system opinion on
/// terminal syntax colors.
const _theme = xterm.TerminalTheme(
  cursor: Color(0xAAD4D4D4),
  selection: Color(0xAAD4D4D4),
  foreground: _fg,
  background: _bg,
  black: Color(0xFF000000),
  red: Color(0xFFCD3131),
  green: Color(0xFF0DBC79),
  yellow: Color(0xFFE5E510),
  blue: Color(0xFF2472C8),
  magenta: Color(0xFFBC3FBC),
  cyan: Color(0xFF11A8CD),
  white: Color(0xFFE5E5E5),
  brightBlack: Color(0xFF666666),
  brightRed: Color(0xFFF14C4C),
  brightGreen: Color(0xFF23D18B),
  brightYellow: Color(0xFFF5F543),
  brightBlue: Color(0xFF3B8EEA),
  brightMagenta: Color(0xFFD670D6),
  brightCyan: Color(0xFF29B8DB),
  brightWhite: Color(0xFFFFFFFF),
  searchHitBackground: Color(0xFFFFFF2B),
  searchHitBackgroundCurrent: Color(0xFF31FF26),
  searchHitForeground: Color(0xFF000000),
);

class TerminalView extends ConsumerStatefulWidget {
  const TerminalView({super.key});

  @override
  ConsumerState<TerminalView> createState() => _TerminalViewState();
}

class _TerminalViewState extends ConsumerState<TerminalView> {
  String? _startedForWorkspaceId;

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

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final workspace = ref.watch(workspaceNotifierProvider).activeWorkspace;
    final hostdRoots = ref.watch(workspaceNotifierProvider).hostdRoots;

    if (workspace == null) {
      return _CenteredMessage(text: context.l10n.terminalOpenWorkspace, palette: palette);
    }

    final rootPath = workspace.rootPath;
    final matchingRoot = hostdRoots.where((r) => r.path == rootPath).firstOrNull;
    final trusted = matchingRoot?.trusted ?? false;

    _maybeAutoStart(workspace.id, rootPath, trusted);

    final notifier = ref.read(terminalNotifierProvider.notifier);
    // Watched only for session bookkeeping (which session is active, whether it's still
    // running) -- the terminal's actual screen content is driven by xterm.Terminal's own
    // Observable/render-object plumbing below, not by rebuilding this widget per output byte.
    final terminalState = ref.watch(terminalNotifierProvider);
    final session = terminalState.sessions.where((s) => s.workspaceId == workspace.id).firstOrNull;

    if (session == null) {
      if (matchingRoot == null) {
        return _CenteredMessage(text: context.l10n.terminalGrantWorkspace, palette: palette);
      }
      if (!trusted) {
        return _CenteredMessage(
          text: context.l10n.terminalUntrusted,
          palette: palette,
        );
      }
      return _CenteredMessage(text: context.l10n.terminalStarting, palette: palette);
    }

    final terminal = notifier.terminalFor(session.id);
    if (terminal == null) {
      return _CenteredMessage(text: context.l10n.terminalStarting, palette: palette);
    }

    return Container(
      color: _bg,
      child: Column(
        children: [
          Expanded(
            // xterm.TerminalView emits a semantics node per visible cell/row, and repaints
            // (output bursts, resizes) faster than the Windows embedder's native ui::AXTree
            // bridge can reconcile within one update -- logged as "Nodes left pending by the
            // update". Those per-cell nodes carry no meaningful label for a screen reader
            // anyway, so excluding the whole grid from the semantics tree removes the churn
            // at its source instead of just silencing the symptom.
            child: ExcludeSemantics(
              child: xterm.TerminalView(
                terminal,
                theme: _theme,
                textStyle: const xterm.TerminalStyle(fontFamily: 'Courier', fontSize: 12, height: 1.4),
                autofocus: true,
                padding: const EdgeInsets.all(AuraSpacing.sm),
              ),
            ),
          ),
          if (!session.isRunning)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: AuraSpacing.sm, vertical: AuraSpacing.xs),
              child: Row(
                children: [
                  Text(
                    session.exitCode != null
                        ? context.l10n.terminalSessionEndedWithCode(session.exitCode!)
                        : context.l10n.terminalSessionEnded,
                    style: TextStyle(color: palette.textDisabled, fontSize: 12),
                  ),
                  const SizedBox(width: AuraSpacing.sm),
                  TextButton(
                    onPressed: () => notifier.restart(session.id, rootPath: rootPath),
                    child: Text(context.l10n.terminalRestart),
                  ),
                ],
              ),
            ),
        ],
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
