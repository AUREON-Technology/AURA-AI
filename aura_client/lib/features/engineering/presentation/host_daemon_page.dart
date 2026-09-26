import 'dart:async';
import 'dart:convert';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:http/http.dart' as http;

import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/config/backend_url_provider.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/hostd/hostd_control_client.dart';
import 'package:aura_client/core/hostd/hostd_error_messages.dart';
import 'package:aura_client/core/hostd/hostd_providers.dart';
import 'package:aura_client/core/hostd/hostd_supervisor.dart';

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
    if (mounted) AuraSnackbar.show(context, message: 'Saved.', kind: AuraSnackbarKind.success);
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
      AuraSnackbar.show(context, message: 'Set the aura_hostd executable path first.', kind: AuraSnackbarKind.error);
      return;
    }
    setState(() => _starting = true);
    try {
      await ref.read(hostdSupervisorProvider).start(path);
      await Future<void>.delayed(const Duration(milliseconds: 500));
      ref.invalidate(hostdStatusProvider);
    } catch (error) {
      if (mounted) {
        AuraSnackbar.show(context, message: 'Could not start aura_hostd: ${describeHostdError(error)}', kind: AuraSnackbarKind.error);
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
          AuraSnackbar.show(context, message: 'Could not stop aura_hostd: ${describeHostdError(error)}', kind: AuraSnackbarKind.error);
        }
        return;
      }
    }
    await Future<void>.delayed(const Duration(milliseconds: 500));
    ref.invalidate(hostdStatusProvider);
  }

  Future<void> _pairDevice() async {
    final client = ref.read(hostdControlClientProvider);
    setState(() => _pairing = true);
    try {
      final backendUrl = ref.read(backendUrlProvider);
      final start = await client.startPairing(backendUrl: backendUrl);
      final userCode = start['user_code'] as String;
      final intervalS = (start['interval'] as num).toInt();
      final expiresIn = (start['expires_in'] as num).toInt();
      if (!mounted) return;

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

      // Self-approve: this desktop app is already signed in, and the daemon
      // it's pairing runs on this same machine, so it can approve its own
      // pairing request the same way an already-logged-in browser session
      // can approve a new one -- no second device or separate approval UI
      // is needed (see docs/architecture/011-desktop-engineer-agent.md's
      // "known gaps" section: no approval screen exists anywhere yet, so
      // without this the pairing code can never be approved at all).
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

      if (mounted) Navigator.of(context, rootNavigator: true).pop();
      if (outcome == 'approved') {
        ref.invalidate(hostdStatusProvider);
        if (mounted) AuraSnackbar.show(context, message: 'Paired.', kind: AuraSnackbarKind.success);
      } else if (mounted) {
        AuraSnackbar.show(
          context,
          message: outcome == 'failed' ? 'Pairing failed: $failureReason' : 'Pairing code expired.',
          kind: AuraSnackbarKind.error,
        );
      }
    } catch (error) {
      if (mounted) {
        AuraSnackbar.show(context, message: 'Could not start pairing: ${describeHostdError(error)}', kind: AuraSnackbarKind.error);
      }
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
      if (mounted) AuraSnackbar.show(context, message: 'Could not grant $path: ${error.message}', kind: AuraSnackbarKind.error);
    }
  }

  Future<void> _revokeRoot(String path) async {
    final confirmed = await AuraDialog.show<bool>(
      context: context,
      title: 'Revoke workspace root?',
      child: Text('The agent will no longer be able to read or write anything under $path.'),
      confirmLabel: 'Revoke',
      cancelLabel: 'Cancel',
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
      appBar: AppBar(title: const Text('Desktop Engineer Daemon')),
      body: ListView(
        padding: const EdgeInsets.all(AuraSpacing.lg),
        children: [
          _buildExecutablePathCard(palette),
          const SizedBox(height: AuraSpacing.lg),
          statusAsync.when(
            data: (status) => _buildStatusCard(palette, status, supervisor),
            loading: () => const Center(child: CircularProgressIndicator()),
            error: (error, _) => Text('Could not check status: ${describeHostdError(error)}', style: TextStyle(color: palette.danger)),
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
          Text('aura_hostd executable', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: AuraSpacing.xs),
          Text(
            'No binary is bundled with this app -- point this at wherever you built or installed '
            'aura_hostd (see aura_hostd/README.md).',
            style: TextStyle(color: palette.textSecondary, fontSize: 12),
          ),
          const SizedBox(height: AuraSpacing.md),
          Row(
            children: [
              Expanded(
                child: AuraTextField(controller: _pathController, hintText: '/path/to/aura_hostd'),
              ),
              const SizedBox(width: AuraSpacing.sm),
              IconButton(onPressed: _browseForExecutable, icon: const Icon(Icons.folder_open)),
              IconButton(onPressed: _saveExecutablePath, icon: const Icon(Icons.save_outlined)),
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildStatusCard(AuraPalette palette, HostdStatusSnapshot status, HostdSupervisor supervisor) {
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
                    : (status.authenticated && status.backendConnected != true
                        ? palette.warning
                        : palette.success),
                size: 12,
              ),
              const SizedBox(width: AuraSpacing.sm),
              Text(
                !status.reachable
                    ? 'Not running'
                    : !status.authenticated
                        ? 'Running, not paired'
                        // Paired (stored token) but not connected happens right after a drop --
                        // a backend redeploy, a network blip -- while the daemon's reconnect
                        // loop is in backoff; every tool call fails during that window, so this
                        // must read differently from a genuinely live pairing. A null
                        // `backendConnected` is neither: the daemon is too old to report the
                        // field, so say that rather than accusing it of reconnecting forever.
                        : switch (status.backendConnected) {
                            true => 'Running, paired',
                            false => 'Running, reconnecting...',
                            null => 'Running, paired -- update aura_hostd to see connection status',
                          },
                style: Theme.of(context).textTheme.titleMedium,
              ),
            ],
          ),
          if (status.backendUrl != null) ...[
            const SizedBox(height: AuraSpacing.xs),
            Text('Backend: ${status.backendUrl}', style: TextStyle(color: palette.textSecondary, fontSize: 12)),
          ],
          const SizedBox(height: AuraSpacing.md),
          Wrap(
            spacing: AuraSpacing.sm,
            children: [
              if (!status.reachable)
                AuraButton(label: 'Start', icon: Icons.play_arrow, loading: _starting, onPressed: _startDaemon),
              if (status.reachable)
                AuraButton(
                  label: 'Stop',
                  icon: Icons.stop,
                  variant: AuraButtonVariant.secondary,
                  onPressed: () => _stopDaemon(supervisor),
                ),
              if (status.reachable && !status.authenticated)
                AuraButton(label: 'Pair this device', icon: Icons.link, loading: _pairing, onPressed: _pairDevice),
            ],
          ),
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
              Expanded(child: Text('Workspace roots', style: Theme.of(context).textTheme.titleMedium)),
              AuraButton(label: 'Add', icon: Icons.add, variant: AuraButtonVariant.secondary, onPressed: _addRoot),
            ],
          ),
          if (roots.isEmpty)
            Padding(
              padding: const EdgeInsets.only(top: AuraSpacing.md),
              child: Text(
                'No folders granted yet -- the agent can\'t read or write anything on this machine '
                'until you add one.',
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
                    ? 'Trusted -- commands run without per-call confirmation (still confined to this folder)'
                    : 'Untrusted -- only a read-only inspection allowlist runs',
                style: TextStyle(color: palette.textSecondary, fontSize: 11),
              ),
              trailing: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Switch(value: root.trusted, onChanged: (value) => _setTrusted(root.path, value)),
                  IconButton(
                    icon: const Icon(Icons.folder_open_outlined),
                    tooltip: 'Browse',
                    onPressed: () => context.push('/engineer-daemon/browse', extra: root.path),
                  ),
                  IconButton(
                    icon: const Icon(Icons.delete_outline),
                    tooltip: 'Revoke',
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
          Text('Daemon output', style: Theme.of(context).textTheme.titleMedium),
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
      'Live output is only available for a daemon this app started -- pointing at one already '
      'running elsewhere still works for everything else on this page.',
      style: TextStyle(color: palette.textSecondary, fontSize: 12),
    );
  }
}
