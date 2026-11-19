/// What the desktop IDE shows when a panel needs the host daemon and cannot have it yet.
///
/// Source Control and the terminal used to say "Grant this workspace to the host daemon"
/// whenever the workspace was missing from the daemon's granted roots. With the daemon not
/// running at all that list is always empty, so they asked for a grant that could not succeed
/// while the status bar beside them said "Host Not Running". [HostAccessNotice] reads the same
/// `/status` poll the status bar does and asks for the one step that is actually next: start
/// the daemon, pair it, or grant this workspace.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/hostd/hostd_error_messages.dart';
import 'package:aura_client/core/hostd/hostd_providers.dart';
import 'package:aura_client/desktop/explorer/explorer_access.dart';
import 'package:aura_client/features/engineering/presentation/hostd_pairing_flow.dart';
import 'package:aura_client/l10n/l10n.dart';

/// The step the daemon needs before a workspace's files can be reached.
enum HostAccessStep { start, pair, grant }

/// Which step is next, from the latest `/status` snapshot. Null [status] (not polled yet) and an
/// unreachable daemon both mean "start": nothing else can succeed before it answers.
HostAccessStep hostAccessStepFor(HostdStatusSnapshot? status) {
  if (status == null || !status.reachable) return HostAccessStep.start;
  if (!status.authenticated) return HostAccessStep.pair;
  return HostAccessStep.grant;
}

/// Starts the host daemon from the executable path saved in Settings, as the Host panel's Start
/// button always has. Returns whether a start was attempted and did not throw.
Future<bool> startHostDaemon(BuildContext context, WidgetRef ref) async {
  final path = await ref.read(hostdExecutablePathProvider.future);
  if (path == null || path.isEmpty) {
    if (context.mounted) {
      AuraSnackbar.show(
        context,
        message: context.l10n.hostSetExecutableFirst,
        kind: AuraSnackbarKind.error,
      );
    }
    return false;
  }
  try {
    await ref.read(hostdSupervisorProvider).start(path);
    await Future<void>.delayed(const Duration(milliseconds: 500));
    ref.invalidate(hostdStatusProvider);
    return true;
  } catch (error) {
    if (context.mounted) {
      AuraSnackbar.show(
        context,
        message: context.l10n.hostCouldNotStart(describeHostdError(error)),
        kind: AuraSnackbarKind.error,
      );
    }
    return false;
  }
}

/// A centred message plus the one button that moves things forward. [grantMessage] is the
/// panel's own wording for the last step ("…to use Source Control"), shown only once the daemon
/// is running and paired.
class HostAccessNotice extends ConsumerStatefulWidget {
  const HostAccessNotice({
    super.key,
    required this.workspaceRoot,
    required this.grantMessage,
    this.background,
    this.textStyle,
  });

  final String workspaceRoot;
  final String grantMessage;
  final Color? background;
  final TextStyle? textStyle;

  @override
  ConsumerState<HostAccessNotice> createState() => _HostAccessNoticeState();
}

class _HostAccessNoticeState extends ConsumerState<HostAccessNotice> {
  bool _busy = false;

  Future<void> _run(Future<void> Function() action) async {
    setState(() => _busy = true);
    try {
      await action();
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final l10n = context.l10n;
    final step = hostAccessStepFor(ref.watch(hostdStatusProvider).value);
    final (message, label, icon) = switch (step) {
      HostAccessStep.start => (
        l10n.hostRequiredNotRunning,
        l10n.hostStart,
        Icons.play_arrow,
      ),
      HostAccessStep.pair => (
        l10n.hostRequiredNotPaired,
        l10n.hostPair,
        Icons.link,
      ),
      HostAccessStep.grant => (
        widget.grantMessage,
        l10n.explorerGrantAccess,
        Icons.key,
      ),
    };

    return Container(
      color: widget.background ?? palette.surface,
      child: Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(AuraSpacing.lg),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(
                message,
                textAlign: TextAlign.center,
                style:
                    widget.textStyle ?? TextStyle(color: palette.textSecondary),
              ),
              const SizedBox(height: AuraSpacing.md),
              AuraButton(
                label: label,
                icon: icon,
                loading: _busy,
                onPressed: () => _run(() async {
                  switch (step) {
                    case HostAccessStep.start:
                      await startHostDaemon(context, ref);
                    case HostAccessStep.pair:
                      await pairHostDaemon(context, ref);
                    case HostAccessStep.grant:
                      await grantWorkspaceAccess(
                        context,
                        ref,
                        widget.workspaceRoot,
                      );
                  }
                }),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
