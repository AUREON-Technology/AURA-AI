import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/auth/desktop_pairing_client.dart';
import '../../../core/design_system/design_system.dart';
import '../data/hostd_control_client.dart';

/// Desktop Engineer workspace panel: daemon status, pairing code, roots, YOLO banner.
class HostAgentPage extends ConsumerStatefulWidget {
  const HostAgentPage({super.key});

  @override
  ConsumerState<HostAgentPage> createState() => _HostAgentPageState();
}

class _HostAgentPageState extends ConsumerState<HostAgentPage> {
  final _control = HostdControlClient();
  final _pathController = TextEditingController();
  HostdStatus? _status;
  PairingCode? _pairing;
  String? _error;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  @override
  void dispose() {
    _pathController.dispose();
    super.dispose();
  }

  Future<void> _refresh() async {
    final status = await _control.status();
    if (!mounted) return;
    setState(() => _status = status);
  }

  Future<void> _startPairing() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final code = await ref.read(desktopPairingClientProvider).startPairing();
      if (!mounted) return;
      setState(() => _pairing = code);
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = e.toString());
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _grant() async {
    final path = _pathController.text.trim();
    if (path.isEmpty) return;
    setState(() => _busy = true);
    final ok = await _control.grant(path);
    if (!mounted) return;
    setState(() {
      _busy = false;
      if (!ok) _error = 'Could not grant path (is aura_hostd running?)';
    });
    _pathController.clear();
    await _refresh();
  }

  Future<void> _toggleYolo(bool enabled) async {
    setState(() => _busy = true);
    final ok = await _control.setYolo(enabled);
    if (!mounted) return;
    setState(() {
      _busy = false;
      if (!ok) _error = 'Could not update YOLO (is aura_hostd running?)';
    });
    await _refresh();
  }

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final status = _status;
    final online = status != null;

    return Scaffold(
      body: ListView(
        padding: const EdgeInsets.all(AuraSpacing.xl),
        children: [
          const AuraSectionHeader(title: 'Desktop Engineer'),
          const SizedBox(height: AuraSpacing.xs),
          Text(
            'Pair aura_hostd and grant local workspace folders.',
            style: Theme.of(context).textTheme.bodySmall,
          ),
          if (_error != null) ...[
            const SizedBox(height: AuraSpacing.md),
            Text(_error!, style: TextStyle(color: palette.danger)),
          ],
          const SizedBox(height: AuraSpacing.lg),
          AuraCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  online
                      ? (status.connected
                          ? 'Daemon connected to backend'
                          : 'Daemon control plane up (backend WS not connected yet)')
                      : 'Daemon not reachable on 127.0.0.1:8765',
                  style: Theme.of(context).textTheme.titleMedium,
                ),
                const SizedBox(height: AuraSpacing.sm),
                Text(
                  online
                      ? 'Backend: ${status.backendUrl}'
                      : 'Start with: dart run bin/aura_hostd.dart run',
                  style: Theme.of(context).textTheme.bodySmall,
                ),
                const SizedBox(height: AuraSpacing.md),
                AuraButton(
                  label: 'Refresh status',
                  onPressed: _busy ? null : _refresh,
                ),
              ],
            ),
          ),
          if (status?.yolo == true) ...[
            const SizedBox(height: AuraSpacing.md),
            AuraCard(
              child: Row(
                children: [
                  Icon(Icons.warning_amber_rounded, color: palette.warning),
                  const SizedBox(width: AuraSpacing.md),
                  Expanded(
                    child: Text(
                      'YOLO is on — commands in granted roots skip the allowlist. '
                      'Hard denylist and path containment still apply.',
                      style: Theme.of(context).textTheme.bodyMedium,
                    ),
                  ),
                ],
              ),
            ),
          ],
          const SizedBox(height: AuraSpacing.xl),
          const AuraSectionHeader(title: 'Pair daemon'),
          const SizedBox(height: AuraSpacing.sm),
          Text(
            'Generate a one-time code, then run: aura_hostd auth pair <CODE>',
            style: Theme.of(context).textTheme.bodySmall,
          ),
          const SizedBox(height: AuraSpacing.md),
          AuraButton(
            label: _busy ? 'Working…' : 'Generate pairing code',
            onPressed: _busy ? null : _startPairing,
          ),
          if (_pairing != null) ...[
            const SizedBox(height: AuraSpacing.md),
            AuraCard(
              child: Row(
                children: [
                  Expanded(
                    child: SelectableText(
                      _pairing!.code,
                      style: Theme.of(context).textTheme.headlineMedium?.copyWith(
                            letterSpacing: 4,
                            fontWeight: FontWeight.w700,
                          ),
                    ),
                  ),
                  IconButton(
                    tooltip: 'Copy',
                    onPressed: () => Clipboard.setData(ClipboardData(text: _pairing!.code)),
                    icon: const Icon(Icons.copy),
                  ),
                ],
              ),
            ),
            Text(
              'Expires in ${_pairing!.expiresIn}s',
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ],
          const SizedBox(height: AuraSpacing.xl),
          const AuraSectionHeader(title: 'Workspace roots'),
          const SizedBox(height: AuraSpacing.sm),
          Row(
            children: [
              Expanded(
                child: AuraTextField(
                  controller: _pathController,
                  label: 'Absolute path',
                  hintText: r'C:\dev\my-project or /home/me/src/app',
                ),
              ),
              const SizedBox(width: AuraSpacing.md),
              AuraButton(label: 'Grant', onPressed: _busy ? null : _grant),
            ],
          ),
          const SizedBox(height: AuraSpacing.md),
          if (status == null || status.roots.isEmpty)
            const AuraEmptyState(
              icon: Icons.folder_off_outlined,
              title: 'No roots granted',
              message: 'Grant a project folder so the Engineer agent can read and edit it.',
            )
          else
            for (final root in status.roots)
              Padding(
                padding: const EdgeInsets.only(bottom: AuraSpacing.sm),
                child: AuraCard(
                  child: Row(
                    children: [
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(root.path),
                            Text(
                              root.trusted ? 'Trusted' : 'Untrusted (allowlist only)',
                              style: Theme.of(context).textTheme.bodySmall,
                            ),
                          ],
                        ),
                      ),
                      TextButton(
                        onPressed: _busy
                            ? null
                            : () async {
                                await _control.setTrusted(root.path, trusted: !root.trusted);
                                await _refresh();
                              },
                        child: Text(root.trusted ? 'Untrust' : 'Trust'),
                      ),
                      TextButton(
                        onPressed: _busy
                            ? null
                            : () async {
                                await _control.revoke(root.path);
                                await _refresh();
                              },
                        child: const Text('Revoke'),
                      ),
                    ],
                  ),
                ),
              ),
          const SizedBox(height: AuraSpacing.xl),
          const AuraSectionHeader(title: 'Consent'),
          SwitchListTile(
            title: const Text('YOLO mode'),
            subtitle: const Text(
              'Skip per-command allowlist prompts. Never lifts path containment.',
            ),
            value: status?.yolo ?? false,
            onChanged: (!online || _busy) ? null : _toggleYolo,
          ),
        ],
      ),
    );
  }
}
