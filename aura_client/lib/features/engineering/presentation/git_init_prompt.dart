/// "Initialize git here" (WP4, D-13).
///
/// Isolation -- one git worktree per Engineer chat (D-09) -- needs git, so in a folder that is not
/// a repository the Engineer refuses to work until it is one. These are the two ways out: a banner
/// in the IDE's Engineer panel, which asks the local daemon, and a compact row in the phone/web
/// desktop bar, which asks the backend to relay it. Both confirm first with the same words.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/hostd/hostd_control_client.dart';
import 'package:aura_client/core/hostd/hostd_error_messages.dart';
import 'package:aura_client/core/hostd/hostd_providers.dart';
import 'package:aura_client/core/network/aura_api_error.dart';
import 'package:aura_client/l10n/l10n.dart';

/// Asks before making [path] a repository: what `git init` plus a first commit does, that it
/// stays on this computer, and that anything not in `.gitignore` (a `.env`, say) is committed.
Future<bool> confirmGitInit(BuildContext context, String path) async {
  final l10n = context.l10n;
  final confirmed = await AuraDialog.show<bool>(
    context: context,
    title: l10n.gitInitConfirmTitle,
    child: Text(l10n.gitInitConfirmBody(path)),
    confirmLabel: l10n.gitInitConfirmAction,
    cancelLabel: l10n.cancel,
    onConfirm: () => Navigator.of(context).pop(true),
  );
  return confirmed == true;
}

/// Whether the granted folder [root] is a git repository, asked of the local daemon with the
/// cheapest call that already exists (`GET /git/status`). False only when the daemon says it is
/// not a repository; null when it cannot say (not running, an older daemon, another error), so
/// nothing offers to initialize a folder on a guess.
final hostdFolderIsGitProvider = FutureProvider.autoDispose.family<bool?, String>((ref, root) async {
  try {
    await ref.read(hostdControlClientProvider).gitStatus(root: root);
    return true;
  } on HostdControlError catch (error) {
    final notARepo = error.code == 'not_a_git_repository' ||
        (error.code == null && error.message.toLowerCase().contains('not a git repository'));
    return notARepo ? false : null;
  } on HostdControlUnavailable {
    return null;
  }
}, retry: (_, _) => null);

/// The IDE Engineer panel's banner for a bound folder that is not a git repository.
class WorkspaceGitInitBanner extends ConsumerStatefulWidget {
  const WorkspaceGitInitBanner({super.key, required this.root});

  final String root;

  @override
  ConsumerState<WorkspaceGitInitBanner> createState() => _WorkspaceGitInitBannerState();
}

class _WorkspaceGitInitBannerState extends ConsumerState<WorkspaceGitInitBanner> {
  bool _busy = false;

  Future<void> _init() async {
    if (!await confirmGitInit(context, widget.root) || !mounted) return;
    setState(() => _busy = true);
    try {
      await ref.read(hostdControlClientProvider).initGit(root: widget.root);
      ref.invalidate(hostdFolderIsGitProvider(widget.root));
      if (mounted) {
        AuraSnackbar.show(context, message: context.l10n.gitInitDone, kind: AuraSnackbarKind.success);
      }
    } catch (error) {
      // A daemon refusal arrives already worded by its code (hostd_error_codes.dart).
      if (mounted) {
        AuraSnackbar.show(
          context,
          message: context.l10n.gitInitFailed(describeHostdError(error)),
          kind: AuraSnackbarKind.error,
        );
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final isGit = ref.watch(hostdFolderIsGitProvider(widget.root)).value;
    if (isGit != false) return const SizedBox.shrink();
    final palette = AuraPalette.of(context);
    final l10n = context.l10n;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(AuraSpacing.md),
      decoration: BoxDecoration(
        color: palette.warning.withValues(alpha: 0.12),
        border: Border(bottom: BorderSide(color: palette.border)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            l10n.gitInitBannerTitle,
            style: TextStyle(fontWeight: FontWeight.w600, color: palette.textPrimary, fontSize: 13),
          ),
          const SizedBox(height: AuraSpacing.xs),
          Text(l10n.gitInitBannerBody, style: TextStyle(color: palette.textSecondary, fontSize: 12)),
          const SizedBox(height: AuraSpacing.sm),
          AuraButton(
            label: l10n.gitInitButton,
            icon: Icons.source_outlined,
            loading: _busy,
            onPressed: _busy ? null : _init,
          ),
        ],
      ),
    );
  }
}

/// The compact row the phone/web desktop bar shows under a chosen folder that is not a git
/// repository. [onInit] does the work (the bar asks the backend); this only confirms and shows
/// the outcome.
class CompactGitInitRow extends StatefulWidget {
  const CompactGitInitRow({super.key, required this.path, required this.onInit});

  final String path;

  /// Throws on failure; the message shown is `describe(error)`.
  final Future<void> Function() onInit;

  @override
  State<CompactGitInitRow> createState() => _CompactGitInitRowState();
}

class _CompactGitInitRowState extends State<CompactGitInitRow> {
  bool _busy = false;

  Future<void> _init() async {
    if (!await confirmGitInit(context, widget.path) || !mounted) return;
    setState(() => _busy = true);
    try {
      await widget.onInit();
      if (mounted) {
        AuraSnackbar.show(context, message: context.l10n.gitInitDone, kind: AuraSnackbarKind.success);
      }
    } catch (error) {
      if (mounted) {
        AuraSnackbar.show(
          context,
          message: context.l10n.gitInitFailed(gitInitErrorText(error)),
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
    return Padding(
      padding: const EdgeInsets.only(bottom: AuraSpacing.xs),
      child: Row(
        children: [
          Icon(Icons.info_outline, size: 14, color: palette.warning),
          const SizedBox(width: AuraSpacing.xs),
          Expanded(
            child: Text(l10n.engineerDesktopNotGit, style: TextStyle(fontSize: 11, color: palette.textSecondary)),
          ),
          TextButton(
            onPressed: _busy ? null : _init,
            child: _busy
                ? const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2))
                : Text(l10n.gitInitButtonShort),
          ),
        ],
      ),
    );
  }
}

/// The text for a failed relayed init: the server's `message` -- for `desktop_refused`, the
/// daemon's own words -- else the error itself.
String gitInitErrorText(Object error) =>
    error is AuraApiError && error.message.isNotEmpty ? error.message : '$error';
