import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/network/aura_failure.dart';
import 'package:aura_client/l10n/l10n.dart';

import 'auth_provider.dart';

/// "Delete account" in You → Account: asks, deletes the account on the server, and signs this
/// device out. Both app stores require an app that creates accounts to offer this in-app
/// (Apple 5.1.1(v), Google Play's account deletion policy); before it the only erasure was an
/// operator route.
class DeleteAccountTile extends ConsumerStatefulWidget {
  const DeleteAccountTile({super.key});

  @override
  ConsumerState<DeleteAccountTile> createState() => _DeleteAccountTileState();
}

class _DeleteAccountTileState extends ConsumerState<DeleteAccountTile> {
  var _deleting = false;

  Future<void> _confirmAndDelete() async {
    if (_deleting) return;
    final l10n = context.l10n;
    final confirmed = await AuraDialog.show<bool>(
      context: context,
      title: l10n.deleteAccountConfirmTitle,
      confirmLabel: l10n.deleteAccountConfirm,
      cancelLabel: l10n.cancel,
      confirmDestructive: true,
      // showDialog pushes onto the root navigator; this page sits under a ShellRoute.
      onConfirm: () => Navigator.of(context, rootNavigator: true).pop(true),
      child: Text(l10n.deleteAccountConfirmBody, style: Theme.of(context).textTheme.bodyMedium),
    );
    if (confirmed != true || !mounted) return;

    // Read now: a successful delete signs out, the router leaves this page, and the result has
    // to be reported on the login screen through the app-level messenger.
    final messenger = ScaffoldMessenger.of(context);
    final palette = AuraPalette.of(context);
    setState(() => _deleting = true);
    try {
      final result = await ref.read(authProvider.notifier).deleteAccount();
      AuraSnackbar.showOn(
        messenger,
        palette: palette,
        message: result.forgotten ? l10n.deleteAccountDone : l10n.deleteAccountPartial,
        kind: result.forgotten ? AuraSnackbarKind.success : AuraSnackbarKind.error,
        duration: const Duration(seconds: 8),
      );
    } catch (error) {
      // Nothing local changed: still signed in, free to try again.
      AuraSnackbar.showOn(
        messenger,
        palette: palette,
        message: '${l10n.deleteAccountFailed} ${AuraFailure.from(error).message}',
        kind: AuraSnackbarKind.error,
      );
    } finally {
      if (mounted) setState(() => _deleting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    return AuraListTile(
      icon: Icons.delete_forever_outlined,
      iconColor: palette.danger,
      title: context.l10n.deleteAccountTitle,
      subtitle: context.l10n.deleteAccountSubtitle,
      trailing: _deleting
          ? SizedBox(
              width: 20,
              height: 20,
              child: CircularProgressIndicator(strokeWidth: 2, color: palette.danger),
            )
          : null,
      onTap: _deleting ? null : _confirmAndDelete,
    );
  }
}
