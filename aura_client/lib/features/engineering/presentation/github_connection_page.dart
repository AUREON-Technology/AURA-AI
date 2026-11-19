import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';
import 'package:url_launcher/url_launcher.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/network/aura_api_client.dart';
import 'package:aura_client/core/network/aura_failure.dart';
import 'package:aura_client/core/network/aura_failure_view.dart';
import 'package:aura_client/l10n/l10n.dart';

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
        title: Text(context.l10n.settingsGithubTitle),
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
      final authorizeUrl = await fetchGithubAuthorizeUrl(ref.read(auraApiClientProvider));
      final opened = await launchUrl(
        Uri.parse(authorizeUrl),
        mode: LaunchMode.externalApplication,
      );
      if (!opened && mounted) {
        AuraSnackbar.show(
          context,
          message: context.l10n.engineeringGithubBrowserFailed,
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
      await disconnectGithub(ref.read(auraApiClientProvider));
      ref.invalidate(githubConnectionStatusProvider);
      if (mounted) {
        AuraSnackbar.show(context, message: context.l10n.engineeringGithubDisconnected, kind: AuraSnackbarKind.success);
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
                          ? (status.githubLogin == null
                              ? context.l10n.engineeringGithubConnectedUnknown
                              : context.l10n.engineeringGithubConnectedAs(status.githubLogin!))
                          : context.l10n.engineeringGithubNotConnected,
                      style: Theme.of(context).textTheme.titleSmall,
                    ),
                  ),
                ],
              ),
              if (status.connected && status.connectedAt != null) ...[
                const SizedBox(height: AuraSpacing.xs),
                Text(
                  context.l10n.engineeringGithubConnectedAt(DateFormat.yMMMd().add_jm().format(status.connectedAt!)),
                  style: TextStyle(fontSize: 12, color: palette.textSecondary),
                ),
              ],
              const SizedBox(height: AuraSpacing.md),
              Text(
                status.connected
                    ? context.l10n.engineeringGithubConnectedBody
                    : context.l10n.engineeringGithubDisconnectedBody,
                style: TextStyle(color: palette.textSecondary),
              ),
              const SizedBox(height: AuraSpacing.lg),
              AuraButton(
                label: status.connected ? context.l10n.engineeringGithubDisconnect : context.l10n.engineeringGithubConnect,
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
