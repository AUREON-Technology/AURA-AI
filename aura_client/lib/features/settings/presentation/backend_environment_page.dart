import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/config/backend_url_provider.dart';
import 'package:aura_client/core/config/backend_url_settings.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/network/aura_api_client.dart';

final backendReachabilityProvider = FutureProvider.autoDispose<bool>((ref) async {
  final response = await ref.read(auraApiClientProvider).probeHealth();
  return response.statusCode == 200;
});

/// Picker for production vs development backend presets.
class BackendEnvironmentPage extends ConsumerStatefulWidget {
  const BackendEnvironmentPage({super.key});

  @override
  ConsumerState<BackendEnvironmentPage> createState() => _BackendEnvironmentPageState();
}

class _BackendEnvironmentPageState extends ConsumerState<BackendEnvironmentPage> {
  var _switching = false;

  Future<void> _selectEnvironment(BackendEnvironment environment) async {
    if (_switching) return;

    final current = ref.read(backendUrlProvider.notifier).currentEnvironment;
    if (current == environment) return;

    setState(() => _switching = true);
    try {
      final changed =
          await ref.read(backendUrlProvider.notifier).setEnvironment(environment);
      if (!mounted) return;
      ref.invalidate(backendReachabilityProvider);
      if (changed) {
        AuraSnackbar.show(
          context,
          message: 'Sign in again to continue.',
          kind: AuraSnackbarKind.info,
        );
      }
    } finally {
      if (mounted) setState(() => _switching = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final backendUrl = ref.watch(backendUrlProvider);
    final selected = BackendUrlSettings.environmentForUrl(backendUrl);
    final reachability = ref.watch(backendReachabilityProvider);

    return Scaffold(
      backgroundColor: palette.background,
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        elevation: 0,
        title: Text(
          'Server environment',
          style: Theme.of(context).textTheme.headlineMedium,
        ),
      ),
      body: ListView(
        padding: const EdgeInsets.all(AuraSpacing.lg),
        children: [
          AuraCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('Current server', style: Theme.of(context).textTheme.titleSmall),
                const SizedBox(height: AuraSpacing.sm),
                Text(backendUrl, style: Theme.of(context).textTheme.bodySmall),
                const SizedBox(height: AuraSpacing.md),
                reachability.when(
                  data: (reachable) => Row(
                    children: [
                      Icon(
                        reachable ? Icons.cloud_done_outlined : Icons.cloud_off_outlined,
                        size: 18,
                        color: reachable ? palette.success : palette.warning,
                      ),
                      const SizedBox(width: AuraSpacing.sm),
                      Text(
                        reachable ? 'Reachable' : 'Unreachable',
                        style: Theme.of(context).textTheme.bodySmall,
                      ),
                    ],
                  ),
                  loading: () => Row(
                    children: [
                      SizedBox(
                        width: 16,
                        height: 16,
                        child: CircularProgressIndicator(
                          strokeWidth: 2,
                          color: palette.brand,
                        ),
                      ),
                      const SizedBox(width: AuraSpacing.sm),
                      Text('Checking…', style: Theme.of(context).textTheme.bodySmall),
                    ],
                  ),
                  error: (_, _) => Text(
                    'Could not check reachability',
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: AuraSpacing.lg),
          AuraCard(
            padding: EdgeInsets.zero,
            child: Column(
              children: [
                for (final environment in BackendEnvironment.values) ...[
                  if (environment != BackendEnvironment.values.first)
                    Divider(height: 1, indent: 56, color: palette.border),
                  ListTile(
                    leading: Icon(
                      environment == BackendEnvironment.production
                          ? Icons.public_outlined
                          : Icons.science_outlined,
                      color: palette.textSecondary,
                    ),
                    title: Text(BackendUrlSettings.labelFor(environment)),
                    subtitle: Text(BackendUrlSettings.urlFor(environment)),
                    trailing: _switching
                        ? SizedBox(
                            width: 20,
                            height: 20,
                            child: CircularProgressIndicator(
                              strokeWidth: 2,
                              color: palette.brand,
                            ),
                          )
                        : Icon(
                            selected == environment
                                ? Icons.check_circle
                                : Icons.circle_outlined,
                            color: selected == environment
                                ? palette.brand
                                : palette.textDisabled,
                          ),
                    onTap: _switching ? null : () => _selectEnvironment(environment),
                  ),
                ],
              ],
            ),
          ),
          const SizedBox(height: AuraSpacing.lg),
          Text(
            'Switching servers clears your session on this device. '
            'Sign in again after changing environments.',
            style: Theme.of(context).textTheme.bodySmall,
          ),
        ],
      ),
    );
  }
}
