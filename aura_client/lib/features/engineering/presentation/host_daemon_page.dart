import 'dart:async';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import 'package:aura_client/core/config/backend_url_provider.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/hostd/hostd_control_client.dart';
import 'package:aura_client/core/hostd/hostd_error_messages.dart';
import 'package:aura_client/core/hostd/hostd_providers.dart';
import 'package:aura_client/core/hostd/hostd_supervisor.dart';
import 'package:aura_client/l10n/l10n.dart';

import 'hostd_pairing_flow.dart';
import 'remote_control_toggle.dart';

/// The desktop Engineer daemon screen (ADR-011 Unit 8) -- supervise `aura_hostd`, pair it,
/// grant/trust/revoke workspace roots, and browse them. Everything here talks to the daemon's
/// local control server (`hostd_control_client.dart`), never to the AURA backend directly.
class HostDaemonPage extends ConsumerStatefulWidget {
  const HostDaemonPage({super.key});

  @override
  ConsumerState<HostDaemonPage> createState() => _HostDaemonPageState();
}

class _HostDaemonPageState extends ConsumerState<HostDaemonPage> {
  final _pathController = TextEditingController();
  bool _pairing = false;
  bool _starting = false;

  @override
  void initState() {
    super.initState();
    Future.microtask(() async {
      final path = await ref.read(hostdExecutablePathProvider.future);
      if (mounted && path != null) _pathController.text = path;
    });
  }

  @override
  void dispose() {
    _pathController.dispose();
    super.dispose();
  }

  Future<void> _saveExecutablePath() async {
    await ref.read(hostdExecutablePathProvider.notifier).set(_pathController.text.trim());
    if (mounted) AuraSnackbar.show(context, message: context.l10n.engineeringDaemonSaved, kind: AuraSnackbarKind.success);
  }

  Future<void> _browseForExecutable() async {
    final result = await FilePicker.platform.pickFiles();
    final path = result?.files.single.path;
    if (path != null) {
      _pathController.text = path;
      await _saveExecutablePath();
    }
  }

  Future<void> _startDaemon() async {
    final path = _pathController.text.trim();
    if (path.isEmpty) {
      AuraSnackbar.show(context, message: context.l10n.engineeringDaemonPathMissing, kind: AuraSnackbarKind.error);
      return;
    }
    setState(() => _starting = true);
    try {
      await ref.read(hostdSupervisorProvider).start(path);
      await Future<void>.delayed(const Duration(milliseconds: 500));
      ref.invalidate(hostdStatusProvider);
    } catch (error) {
      if (mounted) {
        AuraSnackbar.show(context, message: context.l10n.engineeringDaemonStartFailed(describeHostdError(error)), kind: AuraSnackbarKind.error);
      }
    } finally {
      if (mounted) setState(() => _starting = false);
    }
  }

  Future<void> _stopDaemon(HostdSupervisor supervisor) async {
    if (supervisor.isSpawnedHere) {
      supervisor.stop();
    } else {
      try {
        await ref.read(hostdControlClientProvider).shutdown();
      } catch (error) {
        if (mounted) {
          AuraSnackbar.show(context, message: context.l10n.engineeringDaemonStopFailed(describeHostdError(error)), kind: AuraSnackbarKind.error);
        }
        return;
      }
    }
    await Future<void>.delayed(const Duration(milliseconds: 500));
    ref.invalidate(hostdStatusProvider);
  }

  Future<void> _pairDevice() async {
    setState(() => _pairing = true);
    try {
      await pairHostDaemon(context, ref);
    } finally {
      if (mounted) setState(() => _pairing = false);
    }
  }

  Future<void> _addRoot() async {
    final path = await FilePicker.platform.getDirectoryPath();
    if (path == null) return;
    try {
      await ref.read(hostdControlClientProvider).grantRoot(path);
      ref.invalidate(hostdStatusProvider);
    } on HostdControlError catch (error) {
      if (mounted) AuraSnackbar.show(context, message: context.l10n.engineeringGrantRootFailed(path, error.message), kind: AuraSnackbarKind.error);
    }
  }

  Future<void> _revokeRoot(String path) async {
    final confirmed = await AuraDialog.show<bool>(
      context: context,
      title: context.l10n.engineeringRevokeRootTitle,
      child: Text(context.l10n.engineeringRevokeRootBody(path)),
      confirmLabel: context.l10n.engineeringRevoke,
      cancelLabel: context.l10n.cancel,
      confirmDestructive: true,
      onConfirm: () => Navigator.of(context).pop(true),
    );
    if (confirmed != true) return;
    await ref.read(hostdControlClientProvider).revokeRoot(path);
    ref.invalidate(hostdStatusProvider);
  }

  Future<void> _setTrusted(String path, bool trusted) async {
    await ref.read(hostdControlClientProvider).setTrusted(path, trusted);
    ref.invalidate(hostdStatusProvider);
  }

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final statusAsync = ref.watch(hostdStatusProvider);
    final supervisor = ref.watch(hostdSupervisorProvider);
    final logLines = ref.watch(hostdLogProvider);

    return Scaffold(
      appBar: AppBar(title: Text(context.l10n.engineeringDaemonTitle)),
      body: ListView(
        padding: const EdgeInsets.all(AuraSpacing.lg),
        children: [
          _buildExecutablePathCard(palette),
          const SizedBox(height: AuraSpacing.lg),
          statusAsync.when(
            data: (status) => _buildStatusCard(palette, status, supervisor),
            loading: () => const Center(child: CircularProgressIndicator()),
            error: (error, _) => Text(context.l10n.engineeringDaemonStatusFailed(describeHostdError(error)), style: TextStyle(color: palette.danger)),
          ),
          const SizedBox(height: AuraSpacing.lg),
          statusAsync.maybeWhen(
            data: (status) => status.reachable && status.authenticated
                ? _buildRootsCard(palette, status.roots)
                : const SizedBox.shrink(),
            orElse: () => const SizedBox.shrink(),
          ),
          const SizedBox(height: AuraSpacing.lg),
          if (supervisor.isSpawnedHere) _buildLogCard(palette, logLines) else _buildNoLogsNote(palette),
        ],
      ),
    );
  }

  Widget _buildExecutablePathCard(AuraPalette palette) {
    return AuraCard(
      padding: const EdgeInsets.all(AuraSpacing.lg),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(context.l10n.engineeringExecutableTitle, style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: AuraSpacing.xs),
          Text(
            context.l10n.engineeringExecutableHelp,
            style: TextStyle(color: palette.textSecondary, fontSize: 12),
          ),
          const SizedBox(height: AuraSpacing.md),
          Row(
            children: [
              Expanded(
                child: AuraTextField(controller: _pathController, hintText: '/path/to/aura_hostd'),
              ),
              const SizedBox(width: AuraSpacing.sm),
              IconButton(
                tooltip: context.l10n.hostdBrowseExecutable,
                onPressed: _browseForExecutable,
                icon: const Icon(Icons.folder_open),
              ),
              IconButton(
                tooltip: context.l10n.hostdSaveExecutable,
                onPressed: _saveExecutablePath,
                icon: const Icon(Icons.save_outlined),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildStatusCard(AuraPalette palette, HostdStatusSnapshot status, HostdSupervisor supervisor) {
    // Paired, but to a server other than the one this app is signed in to -- see
    // HostdStatusSnapshot.pairedToDifferentServer. Pairing again is the only way to move it.
    final appBackendUrl = ref.watch(backendUrlProvider);
    final otherServer = status.pairedToDifferentServer(appBackendUrl);
    return AuraCard(
      padding: const EdgeInsets.all(AuraSpacing.lg),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(
                status.reachable ? Icons.circle : Icons.circle_outlined,
                color: !status.reachable
                    ? palette.textSecondary
                    : status.needsRepair
                        ? palette.danger
                        : (status.authenticated && status.backendConnected != true
                        ? palette.warning
                        : palette.success),
                size: 12,
              ),
              const SizedBox(width: AuraSpacing.sm),
              Text(
                !status.reachable
                    ? context.l10n.engineeringDaemonNotRunning
                    : !status.authenticated
                        ? context.l10n.engineeringDaemonRunningNotPaired
                        // INT-003: the backend refused the stored token (4401) and the daemon
                        // stopped reconnecting -- only pairing again helps.
                        : status.needsRepair
                            ? context.l10n.engineeringDaemonRepairRequired
                        : otherServer
                        ? context.l10n.engineeringDaemonRunningOtherServer
                        // Paired (stored token) but not connected happens right after a drop --
                        // a backend redeploy, a network blip -- while the daemon's reconnect
                        // loop is in backoff; every tool call fails during that window, so this
                        // must read differently from a genuinely live pairing. A null
                        // `backendConnected` is neither: the daemon is too old to report the
                        // field, so say that rather than accusing it of reconnecting forever.
                        : switch (status.backendConnected) {
                            true => context.l10n.engineeringDaemonRunningPaired,
                            false => context.l10n.engineeringDaemonReconnecting,
                            null => context.l10n.engineeringDaemonPairedUpdateNeeded,
                          },
                style: Theme.of(context).textTheme.titleMedium,
              ),
            ],
          ),
          if (status.backendUrl != null) ...[
            const SizedBox(height: AuraSpacing.xs),
            Text(context.l10n.engineeringDaemonBackend(status.backendUrl!), style: TextStyle(color: palette.textSecondary, fontSize: 12)),
          ],
          if (otherServer) ...[
            const SizedBox(height: AuraSpacing.xs),
            Text(
              context.l10n.hostdPairedToOtherServer(appBackendUrl),
              style: TextStyle(color: palette.warning, fontSize: 12),
            ),
          ],
          const SizedBox(height: AuraSpacing.md),
          Wrap(
            spacing: AuraSpacing.sm,
            children: [
              if (!status.reachable)
                AuraButton(label: context.l10n.engineeringDaemonStart, icon: Icons.play_arrow, loading: _starting, onPressed: _startDaemon),
              if (status.reachable)
                AuraButton(
                  label: context.l10n.engineeringDaemonStop,
                  icon: Icons.stop,
                  variant: AuraButtonVariant.secondary,
                  onPressed: () => _stopDaemon(supervisor),
                ),
              if (status.canPair || (status.reachable && otherServer))
                AuraButton(
                  label: status.needsRepair || otherServer
                      ? context.l10n.engineeringPairAgain
                      : context.l10n.engineeringPairThisDevice,
                  icon: Icons.link,
                  loading: _pairing,
                  onPressed: _pairDevice,
                ),
            ],
          ),
          // WP8 (D-10b): set only here, on the computer itself.
          if (status.reachable) ...[
            const SizedBox(height: AuraSpacing.md),
            HostdRemoteControlToggle(enabled: status.remoteControl),
          ],
        ],
      ),
    );
  }

  Widget _buildRootsCard(AuraPalette palette, List<HostdRoot> roots) {
    return AuraCard(
      padding: const EdgeInsets.all(AuraSpacing.lg),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(child: Text(context.l10n.engineeringWorkspaceRoots, style: Theme.of(context).textTheme.titleMedium)),
              AuraButton(label: context.l10n.commonAdd, icon: Icons.add, variant: AuraButtonVariant.secondary, onPressed: _addRoot),
            ],
          ),
          if (roots.isEmpty)
            Padding(
              padding: const EdgeInsets.only(top: AuraSpacing.md),
              child: Text(
                context.l10n.engineeringNoRoots,
                style: TextStyle(color: palette.textSecondary),
              ),
            ),
          for (final root in roots)
            ListTile(
              contentPadding: EdgeInsets.zero,
              leading: const Icon(Icons.folder_outlined),
              title: Text(root.path, style: const TextStyle(fontFamily: 'Courier', fontSize: 13)),
              subtitle: Text(
                root.trusted
                    ? context.l10n.engineeringRootTrusted
                    : context.l10n.engineeringRootUntrusted,
                style: TextStyle(color: palette.textSecondary, fontSize: 11),
              ),
              trailing: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Switch(value: root.trusted, onChanged: (value) => _setTrusted(root.path, value)),
                  IconButton(
                    icon: const Icon(Icons.folder_open_outlined),
                    tooltip: context.l10n.engineeringBrowse,
                    onPressed: () => context.push('/engineer-daemon/browse', extra: root.path),
                  ),
                  IconButton(
                    icon: const Icon(Icons.delete_outline),
                    tooltip: context.l10n.engineeringRevoke,
                    onPressed: () => _revokeRoot(root.path),
                  ),
                ],
              ),
            ),
        ],
      ),
    );
  }

  Widget _buildLogCard(AuraPalette palette, List<String> lines) {
    return AuraCard(
      padding: const EdgeInsets.all(AuraSpacing.lg),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(context.l10n.engineeringDaemonOutput, style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: AuraSpacing.sm),
          Container(
            height: 240,
            width: double.infinity,
            padding: const EdgeInsets.all(AuraSpacing.sm),
            decoration: BoxDecoration(
              color: Colors.black,
              borderRadius: BorderRadius.circular(AuraRadius.sm),
            ),
            child: ListView.builder(
              reverse: true,
              itemCount: lines.length,
              itemBuilder: (context, index) {
                final line = lines[lines.length - 1 - index];
                return Text(
                  line,
                  style: const TextStyle(fontFamily: 'Courier', fontSize: 11, color: Colors.white),
                );
              },
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildNoLogsNote(AuraPalette palette) {
    return Text(
      context.l10n.engineeringNoLogsNote,
      style: TextStyle(color: palette.textSecondary, fontSize: 12),
    );
  }
}
