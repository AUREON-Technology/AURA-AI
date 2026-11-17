/// Status bar for Desktop IDE.
///
/// Shows branch, hostd connection, backend URL, cursor position, etc.
library;

import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:url_launcher/url_launcher.dart';
import 'package:aura_client/core/config/backend_url_provider.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/hostd/hostd_providers.dart' hide HostdRoot;
import 'package:aura_client/desktop/shell/editor_position.dart';
import 'package:aura_client/desktop/state/state_notifiers.dart';
import 'package:aura_client/desktop/update/desktop_update.dart';
import 'package:aura_client/desktop/update/desktop_update_providers.dart';
import 'package:aura_client/features/chat/presentation/widgets/sandbox_diff_view.dart';
import 'package:aura_client/l10n/l10n.dart';

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
    // Nullable on purpose -- null means the daemon never reported `backend_connected`, i.e. an
    // aura_hostd older than the field itself. See HostdStatusSnapshot.backendConnected.
    final backendConnected = status?.backendConnected;
    // Paired, but to a server other than the one this app is signed in to: the daemon will never
    // connect, so "Reconnecting..." would be a permanent lie. See
    // HostdStatusSnapshot.pairedToDifferentServer.
    final otherServer = status?.pairedToDifferentServer(ref.watch(backendUrlProvider)) ?? false;
    final pendingApprovals = ref.watch(approvalNotifierProvider).pendingApprovals;
    final update = ref.watch(desktopUpdateProvider).value;

    // Everything below used to be a literal: 'main', 'Ln 1, Col 1', 'LF'.
    // The status bar is the one always-visible readout in the IDE, and a
    // developer checks it right before a commit or a push -- a placeholder
    // branch name there is worse than no branch name at all.
    final branch = ref.watch(gitNotifierProvider.select((g) => g.branch));
    final editorState = ref.watch(editorNotifierProvider);
    final activeTab = editorState.activeTab;
    // `contentFor` is read rather than watched: the buffer lives outside
    // EditorState, so this rebuilds on cursor movement (which is what moves
    // Ln/Col) rather than on every keystroke.
    final content = activeTab == null
        ? null
        : ref.read(editorNotifierProvider.notifier).contentFor(activeTab.id);
    final position = (content == null || activeTab?.cursorPosition == null)
        ? null
        : lineColumnFor(content, activeTab!.cursorPosition!.offset);
    final lineEnding = content == null ? null : lineEndingLabelFor(content);

    return Container(
      height: ideDensity.statusBarHeight,
      color: palette.surfaceVariant,
      child: Row(
        children: [
          // Git branch. `null` covers both "no repository loaded yet" and a real
          // detached HEAD (see GitState.branch) -- neither is a branch name, so
          // neither gets rendered as one.
          _StatusItem(
            icon: Icons.source_outlined,
            text: branch ?? context.l10n.ideStatusNoBranch,
            color: branch == null ? palette.textDisabled : null,
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
                : (authenticated && backendConnected != true ? Icons.cloud_sync : Icons.cloud_done),
            text: !reachable
                ? context.l10n.ideStatusHostNotRunning
                : !authenticated
                    ? context.l10n.ideStatusHostNotPaired
                    : otherServer
                    ? context.l10n.ideStatusHostOtherServer
                    : switch (backendConnected) {
                        true => context.l10n.ideStatusHostConnected,
                        false => context.l10n.ideStatusReconnecting,
                        null => context.l10n.ideStatusHostUnknown,
                      },
            color: !reachable
                ? palette.danger
                : !authenticated
                    ? palette.warning
                    : (backendConnected == true ? palette.success : palette.warning),
            density: ideDensity,
          ),
          // D5: pending interactive-approval count -- tap to review/approve/reject.
          if (pendingApprovals.isNotEmpty)
            InkWell(
              onTap: () => _showApprovalsDialog(context, ref),
              child: _StatusItem(
                icon: Icons.pending_actions_outlined,
                text: context.l10n.ideStatusPendingApprovals(pendingApprovals.length),
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
          // M8.10: a newer release (or a deliberate rollback) on the update channel. Only ever
          // an offer -- nothing downloads until the person chooses it.
          if (update != null)
            Tooltip(
              message: update.isRollback
                  ? context.l10n.ideStatusRolledBackTooltip(update.version.toString())
                  : context.l10n.ideStatusUpdateAvailableTooltip(update.version.toString()),
              child: InkWell(
                onTap: () => _confirmUpdate(context, ref, update),
                child: _StatusItem(
                  icon: update.isRollback ? Icons.history : Icons.system_update_alt,
                  text: update.isRollback
                      ? context.l10n.ideStatusRollBackTo(update.version.toString())
                      : context.l10n.ideStatusUpdateTo(update.version.toString()),
                  color: palette.warning,
                  density: ideDensity,
                ),
              ),
            ),
          // Cursor position, encoding and line endings describe the open
          // buffer -- with no tab open there is no buffer to describe, so
          // they are absent rather than showing a default.
          if (position != null)
            _StatusItem(
              icon: Icons.text_fields_outlined,
              text: context.l10n.ideStatusLineColumn(position.line, position.column),
              density: ideDensity,
            ),
          // UTF-8 is a genuine constant, not another placeholder:
          // WorkspaceFileService reads and writes through readAsString /
          // writeAsString, whose default codec is UTF-8, so the IDE has no
          // other encoding to be in. Shown only alongside an open buffer.
          if (activeTab != null)
            _StatusItem(
              icon: Icons.translate_outlined,
              text: 'UTF-8',
              density: ideDensity,
            ),
          if (lineEnding != null)
            _StatusItem(
              icon: Icons.format_align_left_outlined,
              // `Mixed` is the one label that is a word rather than a
              // terminator's conventional name (LF/CRLF/CR stay as they are).
              text: lineEnding == 'Mixed' ? context.l10n.ideStatusLineEndingMixed : lineEnding,
              density: ideDensity,
            ),
        ],
      ),
    );
  }
}

/// M8.10: asks before updating. On Windows it downloads (checksum-verified), starts the installer
/// and quits so the installer can replace the app's files; it relaunches the app when done.
/// Unsaved buffers are flushed to the hot-exit backup first, so they come back after the update.
/// On Linux there is no in-app installer: this opens the download instead.
Future<void> _confirmUpdate(BuildContext context, WidgetRef ref, AvailableUpdate update) async {
  final updater = ref.read(desktopUpdaterProvider);
  final windows = Platform.isWindows;
  final confirmed = await showDialog<bool>(
    context: context,
    builder: (dialogContext) => AlertDialog(
      title: Text(
        update.isRollback
            ? dialogContext.l10n.ideUpdateRollbackTitle(update.version.toString())
            : dialogContext.l10n.ideUpdateTitle(update.version.toString()),
      ),
      content: Text(
        windows
            ? (update.isRollback
                ? dialogContext.l10n.ideUpdateWindowsRollbackBody(update.version.toString())
                : dialogContext.l10n.ideUpdateWindowsBody(update.version.toString()))
            : dialogContext.l10n.ideUpdateLinuxBody(update.version.toString()),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(dialogContext).pop(false),
          child: Text(dialogContext.l10n.ideNotNow),
        ),
        TextButton(
          onPressed: () => Navigator.of(dialogContext).pop(true),
          child: Text(windows ? dialogContext.l10n.ideUpdateInstallRestart : dialogContext.l10n.ideUpdateDownload),
        ),
      ],
    ),
  );
  if (confirmed != true || !context.mounted) return;

  if (!windows) {
    await launchUrl(update.asset.url, mode: LaunchMode.externalApplication);
    return;
  }

  showDialog<void>(
    context: context,
    barrierDismissible: false,
    builder: (progressContext) => AlertDialog(
      content: Row(children: [
        const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2)),
        const SizedBox(width: AuraSpacing.md),
        Expanded(child: Text(progressContext.l10n.ideUpdateDownloading)),
      ]),
    ),
  );
  try {
    final installer = await updater.download(update);
    await ref.read(editorNotifierProvider.notifier).flushBackups();
    await updater.install(installer);
    exit(0);
  } catch (error) {
    if (!context.mounted) return;
    Navigator.of(context, rootNavigator: true).pop();
    AuraSnackbar.show(
      context,
      message: error is UpdateIntegrityException
          ? context.l10n.ideUpdateChecksumFailed
          : context.l10n.ideUpdateFailed(error.toString()),
      kind: AuraSnackbarKind.error,
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
          title: Text(context.l10n.idePendingApprovalsTitle),
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
                          root != null
                              ? context.l10n.ideApprovalFilesInRoot(paths?.length ?? 0, root)
                              : context.l10n.ideApprovalFiles(paths?.length ?? 0),
                          style: TextStyle(fontSize: 12, color: palette.textSecondary, fontWeight: FontWeight.w600),
                        ),
                        const SizedBox(height: AuraSpacing.xs),
                        SandboxDiffView(diffText: approval.arguments['diff_preview'] as String? ?? '', maxLines: 60),
                      ] else ...[
                        Text(
                          approval.arguments['command'] as String? ?? approval.tool,
                          style: AuraTypography.code(palette, fontSize: 13),
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
                            label: context.l10n.ideApprove,
                            onPressed: () => dialogRef.read(approvalNotifierProvider.notifier).respond(approval.id, true),
                          ),
                          const SizedBox(width: AuraSpacing.sm),
                          AuraButton(
                            label: context.l10n.pendingActionsReject,
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
            TextButton(onPressed: () => Navigator.of(dialogContext).pop(), child: Text(context.l10n.close)),
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
            style: AuraTypography.terminal(
              palette,
              color: color ?? palette.textSecondary,
              fontSize: 11,
            ),
          ),
        ],
      ),
    );
  }
}