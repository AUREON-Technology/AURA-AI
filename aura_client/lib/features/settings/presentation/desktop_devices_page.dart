import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/auth/desktop_devices_client.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/l10n/l10n.dart';

/// Settings > Desktop devices (audit FSA-004): every desktop-app sign-in and paired Engineer
/// daemon on this account, each with a Revoke action. Reachable from any platform on purpose --
/// the phone is where someone revokes a laptop they no longer have.
class DesktopDevicesPage extends ConsumerStatefulWidget {
  const DesktopDevicesPage({super.key});

  @override
  ConsumerState<DesktopDevicesPage> createState() => _DesktopDevicesPageState();
}

class _DesktopDevicesPageState extends ConsumerState<DesktopDevicesPage> {
  late Future<List<DesktopDevice>> _devices;
  String? _revoking;

  @override
  void initState() {
    super.initState();
    _devices = ref.read(desktopDevicesClientProvider).list();
  }

  void _reload() {
    setState(() {
      _devices = ref.read(desktopDevicesClientProvider).list();
    });
  }

  Future<void> _revoke(DesktopDevice device) async {
    final l10n = context.l10n;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AuraDialog(
        title: l10n.devicesRevokeConfirmTitle,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(l10n.devicesRevokeConfirmMessage(device.name ?? l10n.devicesUnnamed)),
            const SizedBox(height: AuraSpacing.lg),
            Row(
              mainAxisAlignment: MainAxisAlignment.end,
              children: [
                AuraButton(
                  label: l10n.devicesKeep,
                  variant: AuraButtonVariant.ghost,
                  onPressed: () => Navigator.of(dialogContext).pop(false),
                ),
                const SizedBox(width: AuraSpacing.sm),
                AuraButton(
                  label: l10n.devicesRevoke,
                  variant: AuraButtonVariant.destructive,
                  onPressed: () => Navigator.of(dialogContext).pop(true),
                ),
              ],
            ),
          ],
        ),
      ),
    );
    if (confirmed != true || !mounted) return;
    setState(() => _revoking = device.deviceId);
    try {
      await ref.read(desktopDevicesClientProvider).revoke(device.deviceId);
      if (!mounted) return;
      AuraSnackbar.show(context, message: l10n.devicesRevoked, kind: AuraSnackbarKind.success);
      _reload();
    } catch (_) {
      if (!mounted) return;
      AuraSnackbar.show(context, message: l10n.devicesRevokeFailed, kind: AuraSnackbarKind.error);
    } finally {
      if (mounted) setState(() => _revoking = null);
    }
  }

  String _kindLabel(AppLocalizations l10n, String kind) => switch (kind) {
        'app' => l10n.devicesKindApp,
        'daemon' => l10n.devicesKindDaemon,
        _ => l10n.devicesKindUnknown,
      };

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final palette = AuraPalette.of(context);
    return Scaffold(
      backgroundColor: palette.background,
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        elevation: 0,
        title: Text(l10n.devicesTitle, style: Theme.of(context).textTheme.headlineMedium),
      ),
      body: FutureBuilder<List<DesktopDevice>>(
        future: _devices,
        builder: (context, snapshot) {
          if (snapshot.connectionState != ConnectionState.done) {
            return const Center(child: CircularProgressIndicator());
          }
          if (snapshot.hasError) {
            return AuraEmptyState(
              icon: Icons.cloud_off_outlined,
              title: l10n.devicesLoadFailed,
              actionLabel: l10n.devicesRetry,
              onAction: _reload,
              tone: AuraStateTone.error,
            );
          }
          final devices = snapshot.data ?? const <DesktopDevice>[];
          if (devices.isEmpty) {
            return AuraEmptyState(
              icon: Icons.devices_other_outlined,
              title: l10n.devicesEmpty,
              message: l10n.devicesEmptyMessage,
            );
          }
          return ListView(
            padding: const EdgeInsets.all(AuraSpacing.lg),
            children: [
              Padding(
                padding: const EdgeInsets.only(bottom: AuraSpacing.md),
                child: Text(l10n.devicesIntro, style: Theme.of(context).textTheme.bodyMedium),
              ),
              AuraCard(
                padding: EdgeInsets.zero,
                child: Column(
                  children: [
                    for (var i = 0; i < devices.length; i++) ...[
                      if (i > 0) const Divider(height: 1),
                      _DeviceTile(
                        device: devices[i],
                        kindLabel: _kindLabel(l10n, devices[i].kind),
                        revoking: _revoking == devices[i].deviceId,
                        onRevoke: () => _revoke(devices[i]),
                      ),
                    ],
                  ],
                ),
              ),
            ],
          );
        },
      ),
    );
  }
}

class _DeviceTile extends StatelessWidget {
  const _DeviceTile({
    required this.device,
    required this.kindLabel,
    required this.revoking,
    required this.onRevoke,
  });

  final DesktopDevice device;
  final String kindLabel;
  final bool revoking;
  final VoidCallback onRevoke;

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final parts = <String>[kindLabel];
    final created = device.createdAt;
    if (created != null) {
      parts.add(l10n.devicesPairedOn(MaterialLocalizations.of(context).formatShortDate(created.toLocal())));
    }
    if (device.current) parts.add(l10n.devicesThisDevice);
    return AuraListTile(
      icon: device.kind == 'daemon' ? Icons.terminal_outlined : Icons.laptop_outlined,
      title: device.name ?? l10n.devicesUnnamed,
      subtitle: parts.join(' \u00b7 '),
      trailing: revoking
          ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2))
          : TextButton(
              key: ValueKey('revoke-${device.deviceId}'),
              onPressed: onRevoke,
              child: Text(l10n.devicesRevoke),
            ),
    );
  }
}
