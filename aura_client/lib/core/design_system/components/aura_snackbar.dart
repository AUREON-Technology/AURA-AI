import 'package:flutter/material.dart';
import '../aura_palette.dart';

enum AuraSnackbarKind { success, error, info }

class AuraSnackbar {
  AuraSnackbar._();

  static void show(
    BuildContext context, {
    required String message,
    AuraSnackbarKind kind = AuraSnackbarKind.info,
  }) {
    final palette = AuraPalette.of(context);
    final color = switch (kind) {
      AuraSnackbarKind.success => palette.success,
      AuraSnackbarKind.error => palette.danger,
      AuraSnackbarKind.info => palette.brand,
    };
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(message),
        backgroundColor: color,
        behavior: SnackBarBehavior.floating,
      ),
    );
  }
}
