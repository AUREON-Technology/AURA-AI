import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import 'package:aura_client/core/security/device_validation_service.dart';
import 'package:aura_client/core/theme/theme.dart';
import 'auth_provider.dart';

class ProfilePage extends ConsumerStatefulWidget {
  const ProfilePage({super.key});

  @override
  ConsumerState<ProfilePage> createState() => _ProfilePageState();
}

class _ProfilePageState extends ConsumerState<ProfilePage> {
  bool _isValidating = false;
  String? _validationStatus;
  List<String> _validationLogs = [];

  Future<void> _runDeviceCheck() async {
    setState(() {
      _isValidating = true;
      _validationStatus = null;
      _validationLogs = ['Reading local attestation headers...'];
    });

    await Future.delayed(const Duration(milliseconds: 400));
    final attestation = await DeviceValidationService.resolve();

    setState(() {
      _validationLogs.add('Identified platform as: ${attestation.platform.toUpperCase()}');
      if (attestation.isDebugBypass) {
        _validationLogs.add('Debug build: sending explicit aura_dev_attestation marker.');
        _validationLogs.add('This is not a Play Integrity or App Attest token.');
        _validationStatus = 'Debug marker only — hardware attestation is not enabled in this app';
      } else if (attestation.isHardwareAttestation) {
        _validationLogs.add('Release build: Play Integrity / App Attest SDK produced a token.');
        _validationLogs.add('Header carries the hardware attestation token for server verify.');
        _validationStatus = 'Hardware attestation token ready';
      } else {
        _validationLogs.add('Release build: SDK could not produce a Play Integrity / App Attest token.');
        _validationLogs.add('Header uses aura_attestation_unsupported so the server will not treat it as real.');
        _validationStatus = 'Hardware attestation unavailable on this device';
      }
      _isValidating = false;
    });
  }

  @override
  Widget build(BuildContext context) {
    final authState = ref.watch(authProvider);

    return Scaffold(
      backgroundColor: AuraTheme.background,
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        elevation: 0,
        title: const Text(
          'User Profile & Security',
          style: TextStyle(fontWeight: FontWeight.bold, color: Colors.white),
        ),
      ),
      body: authState.when(
        data: (profile) {
          if (profile == null) {
            return const Center(child: Text('Not signed in', style: TextStyle(color: Colors.white70)));
          }

          final avatarUrl = profile.photoUrl ?? 'https://api.dicebear.com/7.x/bottts/png?seed=${profile.displayName}';

          return Padding(
            padding: const EdgeInsets.all(24.0),
            child: SingleChildScrollView(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  _buildProfileHeader(profile, avatarUrl),
                  const SizedBox(height: 24),
                  _buildDeviceValidationCard(),
                  const SizedBox(height: 24),
                  _buildSignOutCard(),
                ],
              ),
            ),
          );
        },
        loading: () => const Center(child: CircularProgressIndicator(color: AuraTheme.accentIndigo)),
        error: (err, _) => Center(child: Text('Error: $err', style: const TextStyle(color: Colors.redAccent))),
      ),
    );
  }

  Widget _buildProfileHeader(UserProfile profile, String avatarUrl) {
    return Card(
      color: AuraTheme.cardBg,
      child: Padding(
        padding: const EdgeInsets.all(24.0),
        child: Column(
          children: [
            CircleAvatar(
              radius: 46,
              backgroundImage: NetworkImage(avatarUrl),
              backgroundColor: Colors.white10,
            ),
            const SizedBox(height: 18),
            Text(
              profile.displayName,
              style: const TextStyle(fontSize: 20, fontWeight: FontWeight.bold, color: Colors.white),
            ),
            const SizedBox(height: 4),
            Text(
              profile.email,
              style: const TextStyle(color: AuraTheme.textSecondary, fontSize: 13),
            ),
            const SizedBox(height: 12),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
              decoration: BoxDecoration(
                color: AuraTheme.accentIndigo.withValues(alpha: 0.15),
                borderRadius: BorderRadius.circular(20),
              ),
              child: const Text(
                'Authorized User',
                style: TextStyle(color: AuraTheme.accentIndigo, fontSize: 11, fontWeight: FontWeight.bold),
              ),
            )
          ],
        ),
      ),
    );
  }

  Widget _buildDeviceValidationCard() {
    return Card(
      color: AuraTheme.cardBg,
      child: Padding(
        padding: const EdgeInsets.all(20.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Row(
              children: [
                Icon(Icons.security, color: AuraTheme.accentTeal, size: 24),
                SizedBox(width: 12),
                Expanded(
                  child: Text(
                    'Device Attestation Status',
                    style: TextStyle(fontSize: 15, fontWeight: FontWeight.bold, color: Colors.white),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 10),
            const Text(
              'This client does not request Google Play Integrity or Apple App Attest tokens. Debug builds send an explicit development marker; release builds advertise that hardware attestation is unsupported. Backend verification of real Play Integrity tokens exists, but this app cannot produce them without a vendor SDK and Play Console keys.',
              style: TextStyle(fontSize: 12, color: AuraTheme.textSecondary, height: 1.4),
            ),
            const SizedBox(height: 20),
            Row(
              children: [
                Expanded(
                  child: ElevatedButton.icon(
                    onPressed: _isValidating ? null : _runDeviceCheck,
                    icon: _isValidating
                        ? const SizedBox(
                            width: 16,
                            height: 16,
                            child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                          )
                        : const Icon(Icons.info_outline, size: 16),
                    label: const Text('Show Attestation Status'),
                    style: ElevatedButton.styleFrom(
                      backgroundColor: AuraTheme.accentTeal,
                    ),
                  ),
                ),
              ],
            ),
            if (_validationLogs.isNotEmpty) ...[
              const SizedBox(height: 18),
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: Colors.black26,
                  borderRadius: BorderRadius.circular(8),
                  border: Border.all(color: Colors.white10),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: _validationLogs.map((log) {
                    return Padding(
                      padding: const EdgeInsets.only(bottom: 4.0),
                      child: Text(
                        '> $log',
                        style: const TextStyle(
                          fontFamily: 'monospace',
                          fontSize: 11,
                          color: AuraTheme.textSecondary,
                        ),
                      ),
                    );
                  }).toList(),
                ),
              ),
            ],
            if (_validationStatus != null) ...[
              const SizedBox(height: 12),
              Center(
                child: Text(
                  _validationStatus!,
                  textAlign: TextAlign.center,
                  style: const TextStyle(
                    color: AuraTheme.accentAmber,
                    fontWeight: FontWeight.bold,
                    fontSize: 13,
                  ),
                ),
              ),
            ]
          ],
        ),
      ),
    );
  }

  Widget _buildSignOutCard() {
    return Card(
      color: AuraTheme.cardBg,
      child: Padding(
        padding: const EdgeInsets.all(16.0),
        child: Row(
          children: [
            const Expanded(
              child: Text(
                'Session Operations',
                style: TextStyle(fontWeight: FontWeight.bold, color: Colors.white, fontSize: 14),
              ),
            ),
            TextButton.icon(
              onPressed: () async {
                await ref.read(authProvider.notifier).signOut();
                if (mounted) {
                  context.go('/login');
                }
              },
              icon: const Icon(Icons.logout, size: 16, color: Colors.redAccent),
              label: const Text('Sign Out', style: TextStyle(color: Colors.redAccent)),
            ),
          ],
        ),
      ),
    );
  }
}
