import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';

import '../aura_palette.dart';
import 'aura_empty_state.dart';

/// The content width from which the web build shows a list and its detail side by side. Below
/// it the screen is the mobile one, unchanged. It is measured on the space the screen actually
/// has (after the navigation rail), not on the window.
const double kMasterDetailMinWidth = 840;

/// The widest a reading column gets on the web (chat messages, a settings page opened on its own).
const double kReadableMaxWidth = 840;

/// Whether a screen that is [availableWidth] wide should split into master and detail panes.
/// Only the web build splits (docs/plans/client-web-app_plan.md): the mobile app, including on a
/// tablet, keeps its single column. [isWeb] is a parameter so a test can ask the web's answer.
bool masterDetailSplits(double availableWidth, {bool isWeb = kIsWeb}) =>
    isWeb && availableWidth >= kMasterDetailMinWidth;

/// Tells a list inside a master pane that it is one, and which of its rows is open: the row opens
/// its page by replacing the detail pane (`go`) instead of stacking another screen (`push`), and
/// is drawn selected.
class AuraPaneScope extends InheritedWidget {
  const AuraPaneScope({super.key, required this.selectedPath, required super.child});

  /// The location shown in the detail pane.
  final String selectedPath;

  static AuraPaneScope? maybeOf(BuildContext context) =>
      context.dependOnInheritedWidgetOfExactType<AuraPaneScope>();

  /// Whether [path] (or a page under it) is the open one.
  bool isSelected(String path) => selectedPath == path || selectedPath.startsWith('$path/');

  @override
  bool updateShouldNotify(AuraPaneScope oldWidget) => selectedPath != oldWidget.selectedPath;
}

/// A list on one side and the open item on the other. The master keeps a fixed width; the detail
/// takes the rest. A [Row] mirrors in right-to-left languages, so the list sits on the right in
/// Arabic with no extra code.
class AuraMasterDetail extends StatelessWidget {
  const AuraMasterDetail({
    super.key,
    required this.master,
    required this.detail,
    this.masterWidth = 360,
  });

  final Widget master;
  final Widget detail;
  final double masterWidth;

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    return Row(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        SizedBox(width: masterWidth, child: master),
        VerticalDivider(width: 1, thickness: 1, color: palette.border),
        Expanded(child: detail),
      ],
    );
  }
}

/// What the detail pane says before anything is open.
class AuraDetailPlaceholder extends StatelessWidget {
  const AuraDetailPlaceholder({super.key, required this.icon, required this.title, this.message});

  final IconData icon;
  final String title;
  final String? message;

  @override
  Widget build(BuildContext context) => Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 420),
          child: AuraEmptyState(icon: icon, title: title, message: message),
        ),
      );
}

/// Keeps a column of text readable on a big monitor: centered, at most [maxWidth] wide. Applied
/// on the web only by default; narrower windows (and every native build) are untouched.
class AuraReadableWidth extends StatelessWidget {
  const AuraReadableWidth({
    super.key,
    required this.child,
    this.maxWidth = kReadableMaxWidth,
    this.enabled = kIsWeb,
  });

  final Widget child;
  final double maxWidth;
  final bool enabled;

  @override
  Widget build(BuildContext context) {
    if (!enabled) return child;
    return Align(
      alignment: Alignment.topCenter,
      child: ConstrainedBox(constraints: BoxConstraints(maxWidth: maxWidth), child: child),
    );
  }
}
