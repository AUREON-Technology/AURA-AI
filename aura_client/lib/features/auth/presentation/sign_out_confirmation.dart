import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/design_system/design_system.dart';

import 'auth_provider.dart';
import 'package:aura_client/l10n/l10n.dart';

/// Asks before signing out, then signs out. Returns whether it did.
///
/// Sign-out is not a light action here: it wipes this device's AURA data
/// (`AuthNotifier.signOut`), and anything the final push cannot upload is
/// gone. It used to be one tap on an icon with no confirmation (audit F-34).
Future<bool> confirmAndSignOut(BuildContext context, WidgetRef ref) async {
  final confirmed = await AuraDialog.show<bool>(
    context: context,
    title: context.l10n.signOutConfirmTitle,
    confirmLabel: context.l10n.signOut,
    cancelLabel: context.l10n.cancel,
    confirmDestructive: true,
    // showDialog pushes onto the root navigator; the caller's context may sit
    // under a ShellRoute's nested one, whose pop would close the page instead.
    onConfirm: () => Navigator.of(context, rootNavigator: true).pop(true),
    child: Text(
      context.l10n.signOutConfirmBody,
      style: Theme.of(context).textTheme.bodyMedium,
    ),
  );
  if (confirmed != true) return false;
  await ref.read(authProvider.notifier).signOut();
  return true;
}
