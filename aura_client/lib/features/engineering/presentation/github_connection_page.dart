import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';
import 'package:url_launcher/url_launcher.dart';

import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/config/backend_url_provider.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/network/aura_failure.dart';
import 'package:aura_client/core/network/aura_failure_view.dart';

import '../data/github_integration_repository.dart';

/// Lets the signed-in user connect (or disconnect) their own GitHub account for the engineer
/// agent's GitHub tools, as an alternative to a deployment-wide admin PAT
/// (`AURA_GITHUB_TOKEN`) an operator might otherwise have to configure. Connecting opens
/// GitHub's authorization page in the system browser; GitHub redirects back to the *backend*
/// (not this app) to finish the exchange, so the flow ends with the user manually returning here
/// and pulling to refresh -- there is no deep link back into the app to wire up.
class GithubConnectionPage extends ConsumerWidget {
  const GithubConnectionPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final palette = AuraPalette.of(context);
    final statusAsync = ref.watch(githubConnectionStatusProvider);

    return Scaffold(
      backgroundColor: palette.background,
      appBar: AppBar(
        backgroundColor: palette.background,
        title: const Text('GitHub Connection'),
      ),
      body: RefreshIndicator(
        onRefresh: () => ref.refresh(githubConnectionStatusProvider.future),
        child: statusAsync.when(
          data: (status) => _StatusView(status: status),
          loading: () => const Center(child: CircularProgressIndicator()),
          error: (err, _) => ListView(
            children: [
              AuraFailureView(
                failure: AuraFailure.from(err),
                onRetry: () => ref.invalidate(githubConnectionStatusProvider),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _StatusView extends ConsumerStatefulWidget {
  const _StatusView({required this.status});

  final GithubConnectionStatus status;

  @override
  ConsumerState<_StatusView> createState() => _StatusViewState();
}

class _StatusViewState extends ConsumerState<_StatusView> {
  bool _busy = false;

  Future<void> _connect() async {
    setState(() => _busy = true);
    try {
      final backendUrl = ref.read(backendUrlProvider);
      final accessToken = await ref.read(tokenManagerProvider).getValidAccessToken();
      final authorizeUrl = await fetchGithubAuthorizeUrl(
        backendUrl: backendUrl,
        accessToken: accessToken,
      );
      final opened = await launchUrl(
        Uri.parse(authorizeUrl),
        mode: LaunchMode.externalApplication,
      );
      if (!opened && mounted) {
        AuraSnackbar.show(
          context,
          message: 'Could not open the browser to connect GitHub.',
          kind: AuraSnackbarKind.error,
        );
      }
    } catch (e) {
      if (mounted) {
        AuraSnackbar.show(context, message: AuraFailure.from(e).title, kind: AuraSnackbarKind.error);
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _disconnect() async {
    setState(() => _busy = true);
    try {
      final backendUrl = ref.read(backendUrlProvider);
      final accessToken = await ref.read(tokenManagerProvider).getValidAccessToken();
      await disconnectGithub(backendUrl: backendUrl, accessToken: accessToken);
      ref.invalidate(githubConnectionStatusProvider);
      if (mounted) {
        AuraSnackbar.show(context, message: 'GitHub disconnected.', kind: AuraSnackbarKind.success);
      }
    } catch (e) {
      if (mounted) {
        AuraSnackbar.show(context, message: AuraFailure.from(e).title, kind: AuraSnackbarKind.error);
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final status = widget.status;

    return ListView(
      padding: const EdgeInsets.all(AuraSpacing.md),
      children: [
        AuraCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Icon(
                    status.connected ? Icons.check_circle : Icons.link_off,
                    color: status.connected ? palette.success : palette.textSecondary,
                  ),
                  const SizedBox(width: AuraSpacing.sm),
                  Expanded(
                    child: Text(
                      status.connected
                          ? 'Connected as ${status.githubLogin ?? 'a GitHub account'}'
                          : 'No GitHub account connected',
                      style: Theme.of(context).textTheme.titleSmall,
                    ),
                  ),
                ],
              ),
              if (status.connected && status.connectedAt != null) ...[
                const SizedBox(height: AuraSpacing.xs),
                Text(
                  'Connected ${DateFormat.yMMMd().add_jm().format(status.connectedAt!)}',
                  style: TextStyle(fontSize: 12, color: palette.textSecondary),
                ),
              ],
              const SizedBox(height: AuraSpacing.md),
              Text(
                status.connected
                    ? 'The engineer agent can read and write exactly the repositories you '
                        'granted when you installed the GitHub App. Disconnecting only removes '
                        'what AURA can use -- it does not revoke the app on github.com.'
                    : 'Connect your own GitHub account so the engineer agent (opening PRs, '
                        'reading issues, committing files) acts on the repositories you choose, '
                        'instead of relying on a shared token an operator configured.',
                style: TextStyle(color: palette.textSecondary),
              ),
              const SizedBox(height: AuraSpacing.lg),
              AuraButton(
                label: status.connected ? 'Disconnect GitHub' : 'Connect GitHub',
                variant: status.connected
                    ? AuraButtonVariant.destructive
                    : AuraButtonVariant.primary,
                icon: status.connected ? Icons.link_off : Icons.link,
                loading: _busy,
                expand: true,
                onPressed: _busy ? null : (status.connected ? _disconnect : _connect),
              ),
            ],
          ),
        ),
      ],
    );
  }
}
