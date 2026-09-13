/// Slim host-daemon status panel for the IDE sidebar footer.
///
/// A trimmed-down view of [HostDaemonPage] (status + a link to the full
/// settings page) so the IDE doesn't need to duplicate pairing/root-granting
/// UI in the sidebar — those stay on the full page at `/engineer-daemon`.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/hostd/hostd_providers.dart' hide HostdRoot;

class HostPanel extends ConsumerWidget {
  const HostPanel({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final palette = AuraPalette.of(context);
    final statusAsync = ref.watch(hostdStatusProvider);

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: AuraSpacing.md, vertical: AuraSpacing.sm),
      decoration: BoxDecoration(
        border: Border(top: BorderSide(color: palette.border)),
      ),
      child: statusAsync.when(
        data: (status) => Row(
          children: [
            Icon(
              status.reachable ? Icons.circle : Icons.circle_outlined,
              size: 10,
              color: status.reachable ? palette.success : palette.textDisabled,
            ),
            const SizedBox(width: AuraSpacing.xs),
            Expanded(
              child: Text(
                status.reachable
                    ? (status.authenticated ? 'Host connected' : 'Host running — not paired')
                    : 'Host not running',
                style: TextStyle(fontSize: 11, color: palette.textSecondary),
                overflow: TextOverflow.ellipsis,
              ),
            ),
            IconButton(
              icon: Icon(Icons.settings_outlined, size: 15, color: palette.textSecondary),
              tooltip: 'Host daemon settings',
              padding: const EdgeInsets.all(4),
              constraints: const BoxConstraints(),
              onPressed: () => context.push('/engineer-daemon'),
            ),
          ],
        ),
        loading: () => Row(
          children: [
            SizedBox(
              width: 10,
              height: 10,
              child: CircularProgressIndicator(strokeWidth: 1.5, color: palette.textDisabled),
            ),
            const SizedBox(width: AuraSpacing.xs),
            Text('Checking host…', style: TextStyle(fontSize: 11, color: palette.textSecondary)),
          ],
        ),
        error: (_, _) => Text(
          'Host status unavailable',
          style: TextStyle(fontSize: 11, color: palette.danger),
        ),
      ),
    );
  }
}
