/// Host panel for Desktop IDE sidebar.
///
/// Start/Stop/Pair/Full Settings logic ported from `HostDaemonPage`
/// (`features/engineering/presentation/host_daemon_page.dart`) -- that page's route
/// (`/engineer-daemon`) is otherwise unreachable from the Desktop IDE shell (no nav item, no
/// command-palette entry), so this sidebar panel is the only place a person using the IDE shell
/// can actually control `aura_hostd`.
library;

import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:http/http.dart' as http;

import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/config/backend_url_provider.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/hostd/hostd_error_messages.dart';
import 'package:aura_client/core/hostd/hostd_providers.dart';
import 'package:aura_client/core/hostd/hostd_supervisor.dart';

class HostPanel extends ConsumerStatefulWidget {
  const HostPanel({super.key});

  @override
  ConsumerState<HostPanel> createState() => _HostPanelState();
}

class _HostPanelState extends ConsumerState<HostPanel> {
  bool _pairing = false;
  bool _starting = false;

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final statusAsync = ref.watch(hostdStatusProvider);
    final supervisor = ref.watch(hostdSupervisorProvider);

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
              TextButton(
                onPressed: () => context.push('/engineer-daemon'),
                child: const Text('Full Settings'),
              ),
            ],
          ),
          const SizedBox(height: AuraSpacing.sm),
          statusAsync.when(
            data: (status) => _buildStatusCard(context, ref, palette, status, supervisor),
            loading: () => const Center(child: CircularProgressIndicator()),
            error: (error, _) => Text('Error: ${describeHostdError(error)}', style: TextStyle(color: palette.danger, fontSize: 12)),
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
    // Paired (has a stored token) but not yet connected happens right after a drop -- a backend
    // redeploy, a network blip -- while the daemon's reconnect loop is in backoff. Every tool
    // call fails during that window, so this must not read the same as a genuinely live pairing.
    final isConnected = status.backendConnected;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Icon(
              isRunning ? Icons.circle : Icons.circle_outlined,
              color: !isRunning
                  ? palette.textSecondary
                  : (isPaired && !isConnected ? palette.warning : palette.success),
              size: 10,
            ),
            const SizedBox(width: AuraSpacing.xs),
            Text(
              !isRunning
                  ? 'Not running'
                  : !isPaired
                      ? 'Running, not paired'
                      : (isConnected ? 'Running, paired' : 'Running, reconnecting...'),
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
                label: _starting ? 'Starting...' : 'Start',
                icon: Icons.play_arrow,
                onPressed: _starting ? null : () => _startDaemon(context, ref),
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
                label: _pairing ? 'Pairing...' : 'Pair',
                icon: Icons.link,
                variant: AuraButtonVariant.secondary,
                onPressed: _pairing ? null : () => _pairDevice(context, ref),
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
    final path = await ref.read(hostdExecutablePathProvider.future);
    if (path == null || path.isEmpty) {
      if (context.mounted) {
        AuraSnackbar.show(
          context,
          message: 'Set the aura_hostd executable path in Full Settings first.',
          kind: AuraSnackbarKind.error,
        );
      }
      return;
    }
    setState(() => _starting = true);
    try {
      await ref.read(hostdSupervisorProvider).start(path);
      await Future<void>.delayed(const Duration(milliseconds: 500));
      ref.invalidate(hostdStatusProvider);
    } catch (error) {
      if (context.mounted) {
        AuraSnackbar.show(context, message: 'Could not start aura_hostd: ${describeHostdError(error)}', kind: AuraSnackbarKind.error);
      }
    } finally {
      if (mounted) setState(() => _starting = false);
    }
  }

  Future<void> _stopDaemon(BuildContext context, WidgetRef ref, HostdSupervisor supervisor) async {
    if (supervisor.isSpawnedHere) {
      supervisor.stop();
    } else {
      try {
        await ref.read(hostdControlClientProvider).shutdown();
      } catch (error) {
        if (context.mounted) {
          AuraSnackbar.show(context, message: 'Could not stop aura_hostd: ${describeHostdError(error)}', kind: AuraSnackbarKind.error);
        }
        return;
      }
    }
    await Future<void>.delayed(const Duration(milliseconds: 500));
    ref.invalidate(hostdStatusProvider);
  }

  Future<void> _pairDevice(BuildContext context, WidgetRef ref) async {
    final client = ref.read(hostdControlClientProvider);
    setState(() => _pairing = true);
    try {
      final backendUrl = ref.read(backendUrlProvider);
      final start = await client.startPairing(backendUrl: backendUrl);
      final userCode = start['user_code'] as String;
      final intervalS = (start['interval'] as num).toInt();
      final expiresIn = (start['expires_in'] as num).toInt();
      if (!context.mounted) return;

      unawaited(showDialog<void>(
        context: context,
        barrierDismissible: false,
        builder: (dialogContext) => AuraDialog(
          title: 'Pair this device',
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const Text('Approving with your signed-in AURA session...'),
              const SizedBox(height: AuraSpacing.md),
              SelectableText(
                userCode,
                textAlign: TextAlign.center,
                style: const TextStyle(fontFamily: 'Courier', fontSize: 22, fontWeight: FontWeight.bold),
              ),
              const SizedBox(height: AuraSpacing.lg),
              const Center(child: CircularProgressIndicator()),
              const SizedBox(height: AuraSpacing.sm),
              const Text('Waiting for approval...', textAlign: TextAlign.center),
            ],
          ),
        ),
      ));

      // Self-approve: this desktop app is already signed in, and the daemon it's pairing runs on
      // this same machine, so it can approve its own pairing request -- see
      // host_daemon_page.dart's identical comment for why this is safe and why it's needed at
      // all (no separate pairing-approval UI exists anywhere yet).
      final accessToken = await ref.read(tokenManagerProvider).getValidAccessToken();
      if (accessToken == null) {
        throw StateError('Sign in to AURA first -- pairing needs a valid session to approve with.');
      }
      final approveResponse = await http.post(
        Uri.parse('$backendUrl/api/v3/auth/desktop/pair/approve'),
        headers: {
          'Authorization': 'Bearer $accessToken',
          'Content-Type': 'application/json',
        },
        body: jsonEncode({'user_code': userCode}),
      );
      if (approveResponse.statusCode != 200) {
        throw StateError('Approval rejected: HTTP ${approveResponse.statusCode} ${approveResponse.body}');
      }

      final deadline = DateTime.now().add(Duration(seconds: expiresIn));
      String? outcome;
      String? failureReason;
      while (mounted && DateTime.now().isBefore(deadline)) {
        await Future<void>.delayed(Duration(seconds: intervalS > 0 ? intervalS : 2));
        final status = await client.pairingStatus();
        final statusValue = status['status'] as String?;
        if (statusValue == 'approved') {
          outcome = 'approved';
          break;
        }
        if (statusValue != 'pending') {
          outcome = 'failed';
          failureReason = status['reason'] as String? ?? statusValue;
          break;
        }
      }

      if (context.mounted) Navigator.of(context, rootNavigator: true).pop();
      if (outcome == 'approved') {
        ref.invalidate(hostdStatusProvider);
        if (context.mounted) AuraSnackbar.show(context, message: 'Paired.', kind: AuraSnackbarKind.success);
      } else if (context.mounted) {
        AuraSnackbar.show(
          context,
          message: outcome == 'failed' ? 'Pairing failed: $failureReason' : 'Pairing code expired.',
          kind: AuraSnackbarKind.error,
        );
      }
    } catch (error) {
      if (context.mounted) {
        AuraSnackbar.show(context, message: 'Could not start pairing: ${describeHostdError(error)}', kind: AuraSnackbarKind.error);
      }
    } finally {
      if (mounted) setState(() => _pairing = false);
    }
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
