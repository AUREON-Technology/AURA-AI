/// "Allow control from my phone and the web" (WP8, D-10, D-10b).
///
/// A desktop-only switch: it is the one place the daemon's `remote_control` flag can be set, and
/// it talks to the local control server, never to the backend -- so a phone or a browser cannot
/// turn it on. Off until the person turns it on here; turning it on asks first. Shown on the
/// daemon page and in the IDE's Host panel.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/hostd/hostd_error_messages.dart';
import 'package:aura_client/core/hostd/hostd_providers.dart';
import 'package:aura_client/l10n/l10n.dart';

/// The switch on its own, for the IDE once the daemon is connected (D-10b). The Host panel is
/// only shown while the daemon is offline, and the desktop app has no daemon page to go to
/// (it is the IDE and You only), so this is where a paired computer's switch lives.
class HostdRemoteControlCard extends ConsumerWidget {
  const HostdRemoteControlCard({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final status = ref.watch(hostdStatusProvider).value;
    if (status == null || !status.reachable) return const SizedBox.shrink();
    final palette = AuraPalette.of(context);
    return Container(
      padding: const EdgeInsets.all(AuraSpacing.md),
      decoration: BoxDecoration(border: Border(top: BorderSide(color: palette.border))),
      child: HostdRemoteControlToggle(enabled: status.remoteControl, compact: true),
    );
  }
}

class HostdRemoteControlToggle extends ConsumerStatefulWidget {
  const HostdRemoteControlToggle({super.key, required this.enabled, this.compact = false});

  /// What the daemon's `/status` last reported.
  final bool enabled;

  /// Smaller type for the IDE's narrow Host panel.
  final bool compact;

  @override
  ConsumerState<HostdRemoteControlToggle> createState() => _HostdRemoteControlToggleState();
}

class _HostdRemoteControlToggleState extends ConsumerState<HostdRemoteControlToggle> {
  bool _busy = false;

  /// The daemon's answer to the last change, shown until the next `/status` poll reports it.
  bool? _answered;

  @override
  void didUpdateWidget(HostdRemoteControlToggle oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.enabled != widget.enabled) _answered = null;
  }

  Future<bool> _confirmEnable() async {
    final l10n = context.l10n;
    final confirmed = await AuraDialog.show<bool>(
      context: context,
      title: l10n.hostdRemoteControlConfirmTitle,
      child: Text(l10n.hostdRemoteControlConfirmBody),
      confirmLabel: l10n.hostdRemoteControlConfirmAllow,
      cancelLabel: l10n.cancel,
      onConfirm: () => Navigator.of(context).pop(true),
    );
    return confirmed == true;
  }

  Future<void> _set(bool enabled) async {
    if (enabled && !await _confirmEnable()) return;
    if (!mounted) return;
    setState(() => _busy = true);
    try {
      final now = await ref.read(hostdControlClientProvider).setRemoteControl(enabled);
      if (!mounted) return;
      setState(() => _answered = now);
      ref.invalidate(hostdStatusProvider);
    } catch (error) {
      if (mounted) {
        AuraSnackbar.show(
          context,
          message: context.l10n.hostdRemoteControlFailed(describeHostdError(error)),
          kind: AuraSnackbarKind.error,
        );
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final l10n = context.l10n;
    final value = _answered ?? widget.enabled;
    final titleStyle = widget.compact
        ? TextStyle(fontSize: 12, color: palette.textPrimary, fontWeight: FontWeight.w600)
        : Theme.of(context).textTheme.titleSmall;
    final bodyStyle = TextStyle(fontSize: widget.compact ? 10 : 12, color: palette.textSecondary);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        Row(
          children: [
            Expanded(child: Text(l10n.hostdRemoteControlTitle, style: titleStyle)),
            Switch(value: value, onChanged: _busy ? null : _set),
          ],
        ),
        Text(l10n.hostdRemoteControlExplanation, style: bodyStyle),
      ],
    );
  }
}
