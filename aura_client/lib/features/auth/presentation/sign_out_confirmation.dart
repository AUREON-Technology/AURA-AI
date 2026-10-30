import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/design_system/design_system.dart';

import 'auth_provider.dart';

/// Asks before signing out, then signs out. Returns whether it did.
///
/// Sign-out is not a light action here: it wipes this device's AURA data
/// (`AuthNotifier.signOut`), and anything the final push cannot upload is
/// gone. It used to be one tap on an icon with no confirmation (audit F-34).
Future<bool> confirmAndSignOut(BuildContext context, WidgetRef ref) async {
  final confirmed = await AuraDialog.show<bool>(
    context: context,
    title: 'Sign out?',
    confirmLabel: 'Sign out',
    cancelLabel: 'Cancel',
    confirmDestructive: true,
    // showDialog pushes onto the root navigator; the caller's context may sit
    // under a ShellRoute's nested one, whose pop would close the page instead.
    onConfirm: () => Navigator.of(context, rootNavigator: true).pop(true),
    child: Text(
      'AURA uploads unsynced changes first when it can reach the server, then '
      'clears its data from this device. Changes it cannot upload are lost.',
      style: Theme.of(context).textTheme.bodyMedium,
    ),
  );
  if (confirmed != true) return false;
  await ref.read(authProvider.notifier).signOut();
  return true;
}
