import 'package:flutter/material.dart';
import '../aura_palette.dart';

enum AuraSnackbarKind { success, error, info }

class AuraSnackbar {
  AuraSnackbar._();

  /// Shows a snackbar and returns its controller, whose `closed` future says
  /// why it went away -- [SnackBarClosedReason.action] when the user tapped
  /// [actionLabel]. That is what an undoable action keys off (audit F-34).
  static ScaffoldFeatureController<SnackBar, SnackBarClosedReason> show(
    BuildContext context, {
    required String message,
    AuraSnackbarKind kind = AuraSnackbarKind.info,
    String? actionLabel,
    VoidCallback? onAction,
    Duration? duration,
  }) =>
      showOn(
        ScaffoldMessenger.of(context),
        palette: AuraPalette.of(context),
        message: message,
        kind: kind,
        actionLabel: actionLabel,
        onAction: onAction,
        duration: duration,
      );

  /// [show] on a messenger and palette read earlier, for a message that must survive the
  /// widget that triggered it -- one shown as the router leaves the page (account deletion
  /// ends on the login screen). The app-level messenger outlives every route.
  static ScaffoldFeatureController<SnackBar, SnackBarClosedReason> showOn(
    ScaffoldMessengerState messenger, {
    required AuraPalette palette,
    required String message,
    AuraSnackbarKind kind = AuraSnackbarKind.info,
    String? actionLabel,
    VoidCallback? onAction,
    Duration? duration,
  }) {
    final color = switch (kind) {
      AuraSnackbarKind.success => palette.success,
      AuraSnackbarKind.error => palette.danger,
      AuraSnackbarKind.info => palette.brand,
    };
    return messenger.showSnackBar(
      SnackBar(
        content: Text(message),
        backgroundColor: color,
        behavior: SnackBarBehavior.floating,
        duration: duration ?? const Duration(milliseconds: 4000),
        // SnackBar persists by default once it has an action. An undo window
        // has to close on its own, or the delete it defers never runs.
        persist: false,
        action: actionLabel == null
            ? null
            : SnackBarAction(
                label: actionLabel,
                textColor: palette.onBrand,
                onPressed: onAction ?? () {},
              ),
      ),
    );
  }
}
