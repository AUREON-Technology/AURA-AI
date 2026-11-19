import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/auth/identity_service.dart';
import '../../../core/design_system/design_system.dart';
import '../../../l10n/l10n.dart';
import 'login_page.dart' show identityErrorText;

/// Sign-in methods on this account (SaaS P1-F2, spec §5.1). Firebase keeps one account per email
/// and never merges two on its own, so a person who wants to open the same account with Google,
/// Apple or a password connects each here, while signed in.
class LinkedAccountsPage extends ConsumerStatefulWidget {
  const LinkedAccountsPage({super.key});

  @override
  ConsumerState<LinkedAccountsPage> createState() => _LinkedAccountsPageState();
}

class _LinkedAccountsPageState extends ConsumerState<LinkedAccountsPage> {
  String? _busy;

  Future<void> _link(String provider, Future<void> Function() link) async {
    final l10n = context.l10n;
    setState(() => _busy = provider);
    try {
      await link();
      if (mounted) AuraSnackbar.show(context, message: l10n.signInMethodLinkedDone, kind: AuraSnackbarKind.success);
    } on IdentityError catch (e) {
      if (!mounted || e.kind == IdentityErrorKind.cancelled) return;
      AuraSnackbar.show(
        context,
        message: identityErrorText(e.kind, l10n),
        kind: AuraSnackbarKind.error,
      );
    } finally {
      if (mounted) setState(() => _busy = null);
    }
  }

  Future<void> _linkEmail() async {
    final answer = await showDialog<(String, String)>(context: context, builder: (_) => const _EmailPasswordDialog());
    if (answer == null) return;
    await _link('password', () => ref.read(identityServiceProvider).linkEmailPassword(answer.$1, answer.$2));
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final identity = ref.watch(identityServiceProvider);
    final linked = identity.linkedProviders();
    Widget row(String provider, IconData icon, String title, VoidCallback onLink) {
      final isLinked = linked.contains(provider);
      return AuraListTile(
        key: Key('provider-$provider'),
        icon: icon,
        title: title,
        subtitle: isLinked ? l10n.signInMethodLinked : null,
        trailing: isLinked
            ? const Icon(Icons.check_circle_outline)
            : AuraButton(
                label: l10n.signInMethodLink,
                variant: AuraButtonVariant.secondary,
                loading: _busy == provider,
                onPressed: _busy == null ? onLink : null,
              ),
      );
    }

    return Scaffold(
      appBar: AppBar(title: Text(l10n.signInMethodsTitle)),
      body: ListView(padding: const EdgeInsets.all(AuraSpacing.lg), children: [
        row('google.com', Icons.g_mobiledata, 'Google', () => _link('google.com', identity.linkGoogle)),
        row('apple.com', Icons.apple, 'Apple', () => _link('apple.com', identity.linkApple)),
        row('password', Icons.mail_outline, l10n.teamInviteEmailLabel, _linkEmail),
        const SizedBox(height: AuraSpacing.lg),
        // Optional authenticator code on every sign-in (SaaS P1-I).
        AuraListTile(
          key: const Key('two-step-entry'),
          icon: Icons.verified_user_outlined,
          title: l10n.twoStepTitle,
          trailing: const Icon(Icons.chevron_right),
          onTap: () => context.push('/two-step'),
        ),
      ]),
    );
  }
}

class _EmailPasswordDialog extends StatefulWidget {
  const _EmailPasswordDialog();

  @override
  State<_EmailPasswordDialog> createState() => _EmailPasswordDialogState();
}

class _EmailPasswordDialogState extends State<_EmailPasswordDialog> {
  final _email = TextEditingController();
  final _password = TextEditingController();

  @override
  void dispose() {
    _email.dispose();
    _password.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    return AuraDialog(
      title: l10n.signInMethodLink,
      confirmLabel: l10n.signInMethodLink,
      cancelLabel: l10n.cancel,
      onConfirm: () => Navigator.of(context).pop((_email.text.trim(), _password.text)),
      child: Column(mainAxisSize: MainAxisSize.min, children: [
        AuraTextField(key: const Key('link-email'), controller: _email, label: l10n.teamInviteEmailLabel,
            keyboardType: TextInputType.emailAddress),
        const SizedBox(height: AuraSpacing.sm),
        AuraTextField(key: const Key('link-password'), controller: _password, obscureText: true,
            label: l10n.passwordFieldLabel),
      ]),
    );
  }
}
