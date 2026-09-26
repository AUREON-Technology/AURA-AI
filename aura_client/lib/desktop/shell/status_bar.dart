/// Status bar for Desktop IDE.
///
/// Shows branch, hostd connection, backend URL, cursor position, etc.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/hostd/hostd_providers.dart' hide HostdRoot;
import 'package:aura_client/desktop/state/state_notifiers.dart';
import 'package:aura_client/features/chat/presentation/widgets/sandbox_diff_view.dart';

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
    final backendConnected = status?.backendConnected ?? false;
    final pendingApprovals = ref.watch(approvalNotifierProvider).pendingApprovals;

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
          // Hostd connection status. `authenticated` (has a stored pairing token) and
          // `backendConnected` (the daemon's socket to the backend is actually open right now)
          // can disagree for as long as a post-drop reconnect is in backoff -- e.g. right after a
          // backend redeploy -- during which every tool call still fails with "no desktop app is
          // connected" even though this used to show a flat "Host Connected". Surfacing that gap
          // as "Reconnecting..." instead of silently claiming a live connection that isn't.
          _StatusItem(
            icon: !reachable
                ? Icons.cloud_off
                : (authenticated && !backendConnected ? Icons.cloud_sync : Icons.cloud_done),
            text: !reachable
                ? 'Host Not Running'
                : !authenticated
                    ? 'Host Not Paired'
                    : (backendConnected ? 'Host Connected' : 'Reconnecting to server...'),
            color: !reachable
                ? palette.danger
                : !authenticated
                    ? palette.warning
                    : (backendConnected ? palette.success : palette.warning),
            density: ideDensity,
          ),
          // D5: pending interactive-approval count -- tap to review/approve/reject.
          if (pendingApprovals.isNotEmpty)
            InkWell(
              onTap: () => _showApprovalsDialog(context, ref),
              child: _StatusItem(
                icon: Icons.pending_actions_outlined,
                text: '${pendingApprovals.length} pending approval${pendingApprovals.length == 1 ? '' : 's'}',
                color: palette.warning,
                density: ideDensity,
              ),
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

/// D5's approval review UI: every currently-pending approval, stacked (not a single blocking
/// modal that would hide the others while more than one is queued), each with its own
/// Approve/Reject.
void _showApprovalsDialog(BuildContext context, WidgetRef ref) {
  showDialog<void>(
    context: context,
    builder: (dialogContext) => Consumer(
      builder: (context, dialogRef, _) {
        final approvals = dialogRef.watch(approvalNotifierProvider).pendingApprovals;
        final palette = AuraPalette.of(context);
        if (approvals.isEmpty) Navigator.of(dialogContext).pop();
        return AlertDialog(
          backgroundColor: palette.surface,
          title: const Text('Pending Approvals'),
          content: SizedBox(
            width: 480,
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxHeight: 400),
              child: ListView.separated(
                shrinkWrap: true,
                itemCount: approvals.length,
                separatorBuilder: (_, _) => const Divider(height: AuraSpacing.lg),
                itemBuilder: (context, index) {
                  final approval = approvals[index];
                  final root = approval.arguments['root'] as String?;
                  // D6: a write-kind approval (queued by aura_hostd's write-gating retrofit, C.3)
                  // carries a diff instead of a command string -- render it with the same
                  // SandboxDiffView the Git panel and chat's own pending-tool-call previews use,
                  // rather than a new widget.
                  final isWrite = approval.tool == 'write_files';
                  final paths = isWrite ? (approval.arguments['paths'] as List?)?.cast<String>() : null;
                  return Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      if (isWrite) ...[
                        Text(
                          '${paths?.length ?? 0} file(s)${root != null ? ' in $root' : ''}',
                          style: TextStyle(fontSize: 12, color: palette.textSecondary, fontWeight: FontWeight.w600),
                        ),
                        const SizedBox(height: AuraSpacing.xs),
                        SandboxDiffView(diffText: approval.arguments['diff_preview'] as String? ?? '', maxLines: 60),
                      ] else ...[
                        Text(
                          approval.arguments['command'] as String? ?? approval.tool,
                          style: const TextStyle(fontFamily: 'Courier', fontSize: 13),
                        ),
                        if (root != null)
                          Padding(
                            padding: const EdgeInsets.only(top: 4),
                            child: Text(root, style: TextStyle(fontSize: 11, color: palette.textSecondary)),
                          ),
                      ],
                      const SizedBox(height: AuraSpacing.sm),
                      Row(
                        children: [
                          AuraButton(
                            label: 'Approve',
                            onPressed: () => dialogRef.read(approvalNotifierProvider.notifier).respond(approval.id, true),
                          ),
                          const SizedBox(width: AuraSpacing.sm),
                          AuraButton(
                            label: 'Reject',
                            variant: AuraButtonVariant.destructive,
                            onPressed: () => dialogRef.read(approvalNotifierProvider.notifier).respond(approval.id, false),
                          ),
                        ],
                      ),
                    ],
                  );
                },
              ),
            ),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.of(dialogContext).pop(), child: const Text('Close')),
          ],
        );
      },
    ),
  );
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