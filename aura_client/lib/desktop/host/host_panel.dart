/// Host panel for Desktop IDE sidebar.
///
/// Extracted from HostDaemonPage for reuse in IDE sidebar footer.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/hostd/hostd_control_client.dart';
import 'package:aura_client/core/hostd/hostd_providers.dart';
import 'package:aura_client/core/hostd/hostd_supervisor.dart';
import 'package:aura_client/desktop/state/state_notifiers.dart';

class HostPanel extends ConsumerWidget {
  const HostPanel({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final palette = AuraPalette.of(context);
    final statusAsync = ref.watch(hostdStatusProvider);
    final supervisor = ref.watch(hostdSupervisorProvider);
    final hostState = ref.watch(hostNotifierProvider);

    return Padding(
      padding: const EdgeInsets.all(AuraSpacing.md),
      child: AuraCard(
        padding: const EdgeInsets.all(AuraSpacing.md),
        child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          Row(
            children: [
              Expanded(
                child: Text('Host Daemon', style: Theme.of(context).textTheme.titleSmall),
              ),
              if (!supervisor.isSpawnedHere)
                TextButton(
                  onPressed: () => _showFullPage(context),
                  child: const Text('Full Settings'),
                ),
            ],
          ),
          const SizedBox(height: AuraSpacing.sm),
          statusAsync.when(
            data: (status) => _buildStatusCard(context, ref, palette, status, supervisor),
            loading: () => const Center(child: CircularProgressIndicator()),
            error: (error, _) => Text('Error: $error', style: TextStyle(color: palette.danger, fontSize: 12)),
          ),
        ],
        ),
      ),
    );
  }

  Widget _buildStatusCard(
    BuildContext context,
    WidgetRef ref,
    AuraPalette palette,
    HostdStatusSnapshot status,
    HostdSupervisor supervisor,
  ) {
    final isRunning = status.reachable;
    final isPaired = status.authenticated;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Icon(
              isRunning ? Icons.circle : Icons.circle_outlined,
              color: isRunning ? palette.success : palette.textSecondary,
              size: 10,
            ),
            const SizedBox(width: AuraSpacing.xs),
            Text(
              isRunning
                  ? (isPaired ? 'Running, paired' : 'Running, not paired')
                  : 'Not running',
              style: TextStyle(fontSize: 12, color: palette.textPrimary),
            ),
          ],
        ),
        if (status.backendUrl != null) ...[
          const SizedBox(height: AuraSpacing.xs),
          Text('Backend: ${status.backendUrl}', style: TextStyle(fontSize: 10, color: palette.textSecondary)),
        ],
        const SizedBox(height: AuraSpacing.sm),
        Wrap(
          spacing: AuraSpacing.xs,
          children: [
            if (!isRunning)
              _SmallAuraButton(
                label: 'Start',
                icon: Icons.play_arrow,
                onPressed: () => _startDaemon(context, ref),
              ),
            if (isRunning)
              _SmallAuraButton(
                label: 'Stop',
                icon: Icons.stop,
                variant: AuraButtonVariant.secondary,
                onPressed: () => _stopDaemon(context, ref, supervisor),
              ),
            if (isRunning && !isPaired)
              _SmallAuraButton(
                label: 'Pair',
                icon: Icons.link,
                variant: AuraButtonVariant.secondary,
                onPressed: () => _pairDevice(context, ref),
              ),
          ],
        ),
        if (isRunning && isPaired && status.roots.isNotEmpty) ...[
          const SizedBox(height: AuraSpacing.md),
          _buildRootsList(context, palette, status.roots),
        ],
      ],
    );
  }

  Widget _buildRootsList(BuildContext context, AuraPalette palette, List<HostdRoot> roots) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('Granted Roots', style: Theme.of(context).textTheme.labelSmall?.copyWith(color: palette.textSecondary)),
        const SizedBox(height: AuraSpacing.xs),
        ...roots.map((root) => Padding(
          padding: const EdgeInsets.only(bottom: AuraSpacing.xs),
          child: Row(
            children: [
              Icon(Icons.folder_outlined, size: 12, color: palette.textSecondary),
              const SizedBox(width: AuraSpacing.xs),
              Expanded(
                child: Text(
                  root.path,
                  style: TextStyle(fontSize: 11, color: palette.textPrimary, fontFamily: 'Courier'),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              if (root.trusted)
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
                  decoration: BoxDecoration(
                    color: palette.success.withValues(alpha: 0.1),
                    borderRadius: BorderRadius.circular(AuraRadius.pill),
                  ),
                  child: Text(
                    'Trusted',
                    style: TextStyle(fontSize: 9, color: palette.success),
                  ),
                ),
            ],
          ),
        )),
      ],
    );
  }

  Future<void> _startDaemon(BuildContext context, WidgetRef ref) async {
    // Implementation from HostDaemonPage
  }

  Future<void> _stopDaemon(BuildContext context, WidgetRef ref, HostdSupervisor supervisor) async {
    // Implementation from HostDaemonPage
  }

  Future<void> _pairDevice(BuildContext context, WidgetRef ref) async {
    // Implementation from HostDaemonPage
  }

  void _showFullPage(BuildContext context) {
    // Navigate to full HostDaemonPage
  }
}

/// Small button variant for compact spaces (like host panel).
class _SmallAuraButton extends StatelessWidget {
  const _SmallAuraButton({
    required this.label,
    required this.onPressed,
    this.icon,
    this.variant = AuraButtonVariant.primary,
  });

  final String label;
  final VoidCallback? onPressed;
  final IconData? icon;
  final AuraButtonVariant variant;

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final isDisabled = onPressed == null;

    final (bg, fg, border) = switch (variant) {
      AuraButtonVariant.primary => (palette.brand, palette.onBrand, null),
      AuraButtonVariant.secondary => (
        palette.surfaceVariant,
        palette.textPrimary,
        palette.border,
      ),
      AuraButtonVariant.ghost => (
        Colors.transparent,
        palette.brandOnSurfaceText,
        null,
      ),
      AuraButtonVariant.destructive => (palette.danger, Colors.white, null),
    };

    final child = Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        if (icon != null) ...[
          Icon(icon, size: 14, color: fg),
          const SizedBox(width: 6),
        ],
        Text(
          label,
          style: TextStyle(
            color: fg,
            fontWeight: FontWeight.w600,
            fontSize: 12,
          ),
        ),
      ],
    );

    return Material(
      color: isDisabled ? bg.withValues(alpha: 0.5) : bg,
      borderRadius: BorderRadius.circular(AuraRadius.sm),
      child: InkWell(
        borderRadius: BorderRadius.circular(AuraRadius.sm),
        onTap: isDisabled ? null : onPressed,
        child: Container(
          constraints: const BoxConstraints(minHeight: 32),
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
          decoration: border != null
              ? BoxDecoration(
                  border: Border.all(color: border),
                  borderRadius: BorderRadius.circular(AuraRadius.sm),
                )
              : null,
          alignment: Alignment.center,
          child: child,
        ),
      ),
    );
  }
}