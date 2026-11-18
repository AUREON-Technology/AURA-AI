import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/design_system/design_system.dart';
import '../../../core/network/aura_api_error.dart';
import '../../../core/workspaces/active_workspace.dart';
import '../../../l10n/l10n.dart';
import '../data/workspaces_client.dart';
import 'pending_invite.dart';
import 'workspace_providers.dart';

/// Opened from an invite link (`/invite?token=…`), or after signing in with one pending.
class InviteAcceptPage extends ConsumerStatefulWidget {
  const InviteAcceptPage({super.key, this.token});

  final String? token;

  @override
  ConsumerState<InviteAcceptPage> createState() => _InviteAcceptPageState();
}

class _InviteAcceptPageState extends ConsumerState<InviteAcceptPage> {
  String? _token;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    final fromLink = (widget.token?.isNotEmpty ?? false) ? widget.token : null;
    _token = fromLink ?? ref.read(pendingInviteProvider);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      if (fromLink != null && kIsWeb) {
        // Take the single-use token out of the address bar and history: hold it in memory and
        // show the same page at a plain /invite (F2 review #8).
        ref.read(pendingInviteProvider.notifier).hold(fromLink);
        context.replace('/invite');
        return;
      }
      // Forget a held token once this page has it -- after the frame, since a provider cannot
      // change while the tree builds.
      ref.read(pendingInviteProvider.notifier).take();
    });
  }
  String? _problem;

  Future<void> _accept() async {
    final token = _token;
    if (token == null) return;
    setState(() {
      _busy = true;
      _problem = null;
    });
    final l10n = context.l10n;
    try {
      final joined = await ref.read(workspacesClientProvider).accept(token);
      ref.invalidate(workspacesListProvider);
      if (joined != null) await ref.read(activeWorkspaceProvider.notifier).select(joined);
      if (!mounted) return;
      AuraSnackbar.show(context, message: l10n.inviteAccepted(joined?.name ?? ''), kind: AuraSnackbarKind.success);
      context.go(joined != null ? '/workspaces/${joined.id}' : '/today');
    } on AuraApiError catch (e) {
      setState(() => _problem = switch (e.statusCode) {
            409 || 422 => l10n.inviteInvalid,
            403 => l10n.inviteNeedsVerifiedEmail,
            _ => null,
          });
      if (_problem == null && mounted) showFailure(context, e);
    } catch (e) {
      if (mounted) showFailure(context, e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final problem = _token == null ? l10n.inviteInvalid : _problem;
    return Scaffold(
      appBar: AppBar(title: Text(l10n.inviteAcceptTitle)),
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 480),
          child: Padding(
            padding: const EdgeInsets.all(AuraSpacing.lg),
            child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
              Text(l10n.inviteAcceptBody, textAlign: TextAlign.center),
              const SizedBox(height: AuraSpacing.lg),
              if (problem != null)
                Text(problem, key: const Key('invite-problem'), textAlign: TextAlign.center)
              else
                AuraButton(key: const Key('invite-join'), label: l10n.inviteAcceptAction, loading: _busy, onPressed: _accept),
            ]),
          ),
        ),
      ),
    );
  }
}
