import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';

import 'package:aura_client/core/ai/engineer/engineer_consent_repository.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/network/aura_failure.dart';
import 'package:aura_client/l10n/l10n.dart';

/// You → Engineer: whether the Engineer may send code to Anthropic, and a way to withdraw
/// (D-03). Withdrawing stops the Engineer until the user agrees again, which the next Engineer
/// send asks for.
class EngineerConsentTile extends ConsumerStatefulWidget {
  const EngineerConsentTile({super.key});

  @override
  ConsumerState<EngineerConsentTile> createState() => _EngineerConsentTileState();
}

class _EngineerConsentTileState extends ConsumerState<EngineerConsentTile> {
  var _withdrawing = false;

  Future<void> _confirmAndWithdraw() async {
    if (_withdrawing) return;
    final l10n = context.l10n;
    final confirmed = await AuraDialog.show<bool>(
      context: context,
      title: l10n.engineerConsentWithdrawTitle,
      confirmLabel: l10n.engineerConsentWithdraw,
      cancelLabel: l10n.cancel,
      confirmDestructive: true,
      // showDialog pushes onto the root navigator; this page sits under a ShellRoute.
      onConfirm: () => Navigator.of(context, rootNavigator: true).pop(true),
      child: Text(l10n.engineerConsentWithdrawBody, style: Theme.of(context).textTheme.bodyMedium),
    );
    if (confirmed != true || !mounted) return;

    final messenger = ScaffoldMessenger.of(context);
    final palette = AuraPalette.of(context);
    setState(() => _withdrawing = true);
    try {
      await ref.read(engineerConsentProvider.notifier).withdraw();
      AuraSnackbar.showOn(
        messenger,
        palette: palette,
        message: l10n.engineerConsentWithdrawn,
        kind: AuraSnackbarKind.success,
      );
    } catch (error) {
      AuraSnackbar.showOn(
        messenger,
        palette: palette,
        message: l10n.engineerConsentWithdrawFailed(AuraFailure.from(error).message),
        kind: AuraSnackbarKind.error,
      );
    } finally {
      if (mounted) setState(() => _withdrawing = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final palette = AuraPalette.of(context);
    const icon = Icons.privacy_tip_outlined;
    const spinner = SizedBox(
      width: 20,
      height: 20,
      child: CircularProgressIndicator(strokeWidth: 2),
    );

    return switch (ref.watch(engineerConsentProvider)) {
      AsyncData(:final value) when value.accepted => AuraListTile(
          icon: icon,
          title: value.acceptedAt == null
              ? l10n.engineerConsentStatusOnNoDate
              : l10n.engineerConsentStatusOn(
                  DateFormat.yMMMd().format(value.acceptedAt!.toLocal()),
                ),
          subtitle: l10n.engineerConsentStatusOnSubtitle,
          trailing: _withdrawing
              ? spinner
              : AuraButton(
                  label: l10n.engineerConsentWithdraw,
                  variant: AuraButtonVariant.ghost,
                  onPressed: _confirmAndWithdraw,
                ),
        ),
      AsyncData() => AuraListTile(
          icon: icon,
          title: l10n.engineerConsentStatusOff,
          subtitle: l10n.engineerConsentStatusOffSubtitle,
          trailing: const SizedBox.shrink(),
        ),
      AsyncError() => AuraListTile(
          icon: icon,
          iconColor: palette.warning,
          title: l10n.engineerConsentStatusTitle,
          subtitle: l10n.engineerConsentStatusError,
          trailing: const Icon(Icons.refresh),
          onTap: () => ref.invalidate(engineerConsentProvider),
        ),
      // Words, not a spinner: this row is one of many on the page, and an endless animation
      // would be the only thing on it still moving.
      _ => AuraListTile(
          icon: icon,
          title: l10n.engineerConsentStatusTitle,
          subtitle: l10n.engineerConsentStatusLoading,
          trailing: const SizedBox.shrink(),
        ),
    };
  }
}
