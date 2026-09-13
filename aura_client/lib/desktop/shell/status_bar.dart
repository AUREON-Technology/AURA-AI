/// Status bar for Desktop IDE.
///
/// Shows branch, hostd connection, backend URL, cursor position, etc.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/hostd/hostd_providers.dart' hide HostdRoot;

class StatusBar extends ConsumerWidget {
  const StatusBar({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final palette = AuraPalette.of(context);
    final ideDensity = context.ideDensity;
    // Same source as the sidebar's HostPanel (hostdStatusProvider actively
    // polls /status) — do not read a separately-tracked connection flag here,
    // or the two indicators can disagree (previously this watched
    // hostNotifierProvider, which nothing ever refreshed, so it showed
    // "Disconnected" even while HostPanel correctly showed the host running).
    final statusAsync = ref.watch(hostdStatusProvider);
    final status = statusAsync.value;
    final reachable = status?.reachable ?? false;
    final authenticated = status?.authenticated ?? false;

    return Container(
      height: ideDensity.statusBarHeight,
      color: palette.surfaceVariant,
      child: Row(
        children: [
          // Git branch (placeholder)
          _StatusItem(
            icon: Icons.source_outlined,
            text: 'main',
            density: ideDensity,
          ),
          // Hostd connection status
          _StatusItem(
            icon: reachable ? Icons.cloud_done : Icons.cloud_off,
            text: !reachable
                ? 'Host Not Running'
                : (authenticated ? 'Host Connected' : 'Host Not Paired'),
            color: !reachable
                ? palette.danger
                : (authenticated ? palette.success : palette.warning),
            density: ideDensity,
          ),
          // Backend URL
          if (status?.backendUrl != null)
            _StatusItem(
              icon: Icons.dns_outlined,
              text: status!.backendUrl!,
              density: ideDensity,
            ),
          const Spacer(),
          // Cursor position (placeholder)
          _StatusItem(
            icon: Icons.text_fields_outlined,
            text: 'Ln 1, Col 1',
            density: ideDensity,
          ),
          // Encoding
          _StatusItem(
            icon: Icons.translate_outlined,
            text: 'UTF-8',
            density: ideDensity,
          ),
          // Line endings
          _StatusItem(
            icon: Icons.format_align_left_outlined,
            text: 'LF',
            density: ideDensity,
          ),
        ],
      ),
    );
  }
}

class _StatusItem extends StatelessWidget {
  const _StatusItem({
    required this.icon,
    required this.text,
    this.color,
    required this.density,
  });

  final IconData icon;
  final String text;
  final Color? color;
  final AuraIdeDensity density;

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    return Padding(
      padding: EdgeInsets.symmetric(horizontal: density.spacingScale * AuraSpacing.md),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 14, color: color ?? palette.textSecondary),
          const SizedBox(width: 4),
          Text(
            text,
            style: TextStyle(
              fontSize: 11,
              color: color ?? palette.textSecondary,
              fontFamily: 'Courier',
            ),
          ),
        ],
      ),
    );
  }
}