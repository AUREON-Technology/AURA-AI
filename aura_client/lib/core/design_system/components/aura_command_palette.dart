import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../aura_palette.dart';
import '../tokens.dart';
import '../typography.dart';
import 'aura_empty_state.dart';
import 'aura_text_field.dart';
import 'package:aura_client/l10n/l10n.dart';

/// One entry in [AuraCommandPalette].
///
/// A command with a `null` [onInvoke] is **shown and explained**, not hidden:
/// "Go to File" disappearing when no workspace is open teaches the user it
/// does not exist, whereas seeing it greyed with "No workspace is open" teaches
/// them what to do first. Keyboard navigation skips them, so Enter is never a
/// dead interaction.
@immutable
class AuraCommandItem {
  const AuraCommandItem({
    required this.id,
    required this.title,
    required this.group,
    required this.icon,
    this.onInvoke,
    this.disabledReason,
    this.trailing,
  });

  final String id;
  final String title;

  /// Heading this command is filed under — "File", "View", "Git", "AURA".
  final String group;
  final IconData icon;

  /// `null` renders the command disabled, with [disabledReason] shown in place
  /// of the shortcut.
  final VoidCallback? onInvoke;
  final String? disabledReason;

  /// Keyboard shortcut hint, e.g. `Ctrl+P`. A palette is where a user learns
  /// the shortcut for the thing they just did the slow way.
  final String? trailing;

  bool get isEnabled => onInvoke != null;

  /// Everything a query is matched against. A getter rather than a cached
  /// field so the class stays const-constructible; the palette holds tens of
  /// commands, not thousands.
  String get _haystack => '$group $title'.toLowerCase();
}

/// Matches [item] against [query]: every whitespace-separated token must
/// appear somewhere in the command's group or title.
///
/// Deliberately not fuzzy. Subsequence matching makes "tsb" find "Toggle
/// Sidebar", and also makes half the palette match almost anything — in a list
/// the user is scanning under time pressure, predictable beats clever.
bool commandMatches(AuraCommandItem item, String query) {
  final tokens = query.toLowerCase().split(RegExp(r'\s+')).where((t) => t.isNotEmpty);
  return tokens.every(item._haystack.contains);
}

/// Filters [items] by [query], preserving the order they were registered in —
/// which is the order their groups appear, so a palette opened with an empty
/// query reads as a menu rather than as a ranked list.
List<AuraCommandItem> filterCommands(List<AuraCommandItem> items, String query) {
  if (query.trim().isEmpty) return items;
  return items.where((item) => commandMatches(item, query)).toList();
}

/// Index of the next selectable command from [from] moving by [delta],
/// skipping disabled entries, stopping at either end rather than wrapping.
///
/// Returns [from] when there is nothing further to land on, so holding an
/// arrow key at the end of the list is a no-op instead of a wrap that loses
/// the user's place.
int nextSelectableIndex(List<AuraCommandItem> items, int from, int delta) {
  var index = from + delta;
  while (index >= 0 && index < items.length) {
    if (items[index].isEnabled) return index;
    index += delta;
  }
  return from;
}

/// First selectable index in [items], or `-1` when none is.
int firstSelectableIndex(List<AuraCommandItem> items) {
  for (var i = 0; i < items.length; i++) {
    if (items[i].isEnabled) return i;
  }
  return -1;
}

Future<void> showAuraCommandPalette(
  BuildContext context, {
  required List<AuraCommandItem> commands,
  String? hintText,
}) {
  return showDialog<void>(
    context: context,
    // Escape pops through the barrier's own DismissIntent; no binding needed.
    barrierDismissible: true,
    barrierLabel: context.l10n.commandPaletteLabel,
    builder: (_) => AuraCommandPalette(commands: commands, hintText: hintText),
  );
}

class AuraCommandPalette extends StatefulWidget {
  const AuraCommandPalette({super.key, required this.commands, this.hintText});

  final List<AuraCommandItem> commands;

  /// Placeholder of the search field; defaults to the localized `commandPaletteHint`.
  final String? hintText;

  @override
  State<AuraCommandPalette> createState() => _AuraCommandPaletteState();
}

class _AuraCommandPaletteState extends State<AuraCommandPalette> {
  final _controller = TextEditingController();
  final _scrollController = ScrollController();
  late List<AuraCommandItem> _results = widget.commands;
  late int _selected = firstSelectableIndex(_results);

  @override
  void dispose() {
    _controller.dispose();
    _scrollController.dispose();
    super.dispose();
  }

  void _onQueryChanged(String query) {
    setState(() {
      _results = filterCommands(widget.commands, query);
      _selected = firstSelectableIndex(_results);
    });
  }

  void _move(int delta) {
    if (_results.isEmpty) return;
    setState(() {
      _selected = _selected < 0
          ? firstSelectableIndex(_results)
          : nextSelectableIndex(_results, _selected, delta);
    });
  }

  void _invokeSelected() {
    if (_selected < 0 || _selected >= _results.length) return;
    final item = _results[_selected];
    if (!item.isEnabled) return;
    // Pop first: a command that opens another dialog must not race this one
    // closing underneath it.
    Navigator.of(context).pop();
    item.onInvoke!.call();
  }

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);

    // Placed above the text field so arrow keys and Enter reach the list
    // instead of moving the caret: key events walk up from the focused node,
    // and Flutter's own text-editing bindings live far higher than this.
    return Shortcuts(
      shortcuts: const {
        SingleActivator(LogicalKeyboardKey.arrowDown): _MoveSelectionIntent(1),
        SingleActivator(LogicalKeyboardKey.arrowUp): _MoveSelectionIntent(-1),
        SingleActivator(LogicalKeyboardKey.enter): _InvokeSelectionIntent(),
        SingleActivator(LogicalKeyboardKey.numpadEnter): _InvokeSelectionIntent(),
      },
      child: Actions(
        actions: {
          _MoveSelectionIntent: CallbackAction<_MoveSelectionIntent>(
            onInvoke: (intent) => _move(intent.delta),
          ),
          _InvokeSelectionIntent: CallbackAction<_InvokeSelectionIntent>(
            onInvoke: (_) => _invokeSelected(),
          ),
        },
        child: Dialog(
          backgroundColor: palette.surface,
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(AuraRadius.dialog)),
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 620, maxHeight: 460),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Padding(
                  padding: const EdgeInsets.all(AuraSpacing.md),
                  child: AuraTextField(
                    controller: _controller,
                    autofocus: true,
                    hintText: widget.hintText ?? context.l10n.commandPaletteHint,
                    prefixIcon: Icons.search,
                    onChanged: _onQueryChanged,
                  ),
                ),
                Divider(height: 1, color: palette.border),
                Flexible(child: _results.isEmpty ? _noMatches(context) : _list(palette)),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _noMatches(BuildContext context) {
    return AuraEmptyState(
      icon: Icons.search_off_outlined,
      title: context.l10n.commandPaletteNoMatchesTitle,
      message: context.l10n.commandPaletteNoMatchesBody(_controller.text.trim()),
      actionLabel: context.l10n.commandPaletteClearSearch,
      onAction: () {
        _controller.clear();
        _onQueryChanged('');
      },
    );
  }

  Widget _list(AuraPalette palette) {
    return ListView.builder(
      controller: _scrollController,
      shrinkWrap: true,
      padding: const EdgeInsets.symmetric(vertical: AuraSpacing.xs),
      itemCount: _results.length,
      itemBuilder: (context, index) {
        final item = _results[index];
        final showGroupHeader = index == 0 || _results[index - 1].group != item.group;
        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            if (showGroupHeader)
              Padding(
                padding: const EdgeInsets.fromLTRB(
                  AuraSpacing.lg,
                  AuraSpacing.md,
                  AuraSpacing.lg,
                  AuraSpacing.xs,
                ),
                child: Text(
                  item.group.toUpperCase(),
                  style: Theme.of(context).textTheme.labelSmall?.copyWith(
                        color: palette.textSecondary,
                        letterSpacing: 0.8,
                      ),
                ),
              ),
            _CommandRow(
              item: item,
              selected: index == _selected,
              onTap: () {
                setState(() => _selected = index);
                _invokeSelected();
              },
            ),
          ],
        );
      },
    );
  }
}

class _CommandRow extends StatelessWidget {
  const _CommandRow({required this.item, required this.selected, required this.onTap});

  final AuraCommandItem item;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final enabled = item.isEnabled;
    final trailing = enabled ? item.trailing : item.disabledReason;

    return Semantics(
      button: true,
      enabled: enabled,
      selected: selected,
      label: enabled
          ? item.title
          : context.l10n.coreCommandItemUnavailable(item.title, item.disabledReason ?? ''),
      child: InkWell(
        onTap: enabled ? onTap : null,
        child: Container(
          // Selection is a filled row *and* the semantics flag above, never
          // colour alone.
          color: selected ? palette.brand.withValues(alpha: 0.14) : Colors.transparent,
          padding: const EdgeInsets.symmetric(
            horizontal: AuraSpacing.lg,
            vertical: AuraSpacing.md,
          ),
          child: Row(
            children: [
              Icon(
                item.icon,
                size: 18,
                color: enabled ? palette.textSecondary : palette.textDisabled,
              ),
              const SizedBox(width: AuraSpacing.md),
              Expanded(
                child: Text(
                  item.title,
                  overflow: TextOverflow.ellipsis,
                  style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                        color: enabled ? palette.textPrimary : palette.textDisabled,
                      ),
                ),
              ),
              if (trailing != null) ...[
                const SizedBox(width: AuraSpacing.md),
                Text(
                  trailing,
                  style: AuraTypography.code(
                    palette,
                    color: enabled ? palette.textSecondary : palette.textDisabled,
                    fontSize: 11,
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

class _MoveSelectionIntent extends Intent {
  const _MoveSelectionIntent(this.delta);
  final int delta;
}

class _InvokeSelectionIntent extends Intent {
  const _InvokeSelectionIntent();
}
