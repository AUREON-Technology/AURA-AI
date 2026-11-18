import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/l10n/l10n.dart';
import 'auth_provider.dart';

enum EmailFormMode { signIn, register, reset }

final _email = RegExp(r'^[^@\s]+@[^@\s]+\.[^@\s]+$');
const minPasswordLength = 8;

/// Email and password sign-in, account creation and password reset (SaaS P1-B).
///
/// Validation happens here so the identity provider only sees well-formed requests; what the
/// provider then refuses (wrong password, address taken) arrives as an `IdentityError` on
/// [authProvider] and the login page words it. The reset mode always reports the same sentence,
/// so the form cannot be used to learn which addresses have accounts.
///
/// By default it signs in through [authProvider]. A page that must not start an AURA session of
/// its own -- the web page that signs in for the desktop app (`desktop_sign_in_page.dart`) --
/// passes [onSignIn] and [onPasswordReset] instead; the form then offers no account creation,
/// since that page only hands an existing sign-in to the desktop app.
class EmailSignInForm extends ConsumerStatefulWidget {
  const EmailSignInForm({super.key, this.onSignIn, this.onPasswordReset});

  final Future<void> Function(String email, String password)? onSignIn;
  final Future<void> Function(String email)? onPasswordReset;

  @override
  ConsumerState<EmailSignInForm> createState() => _EmailSignInFormState();
}

class _EmailSignInFormState extends ConsumerState<EmailSignInForm> {
  final _name = TextEditingController();
  final _emailController = TextEditingController();
  final _password = TextEditingController();
  final _confirm = TextEditingController();
  EmailFormMode _mode = EmailFormMode.signIn;
  String? _emailError;
  String? _passwordError;
  String? _confirmError;
  bool _resetSent = false;

  @override
  void dispose() {
    _name.dispose();
    _emailController.dispose();
    _password.dispose();
    _confirm.dispose();
    super.dispose();
  }

  void _switchTo(EmailFormMode mode) => setState(() {
        _mode = mode;
        _emailError = _passwordError = _confirmError = null;
        _resetSent = false;
      });

  bool _validate() {
    final l10n = context.l10n;
    final email = _emailController.text.trim();
    setState(() {
      _emailError = _email.hasMatch(email) ? null : l10n.emailInvalid;
      _passwordError = null;
      _confirmError = null;
      if (_mode == EmailFormMode.register) {
        if (_password.text.length < minPasswordLength) _passwordError = l10n.passwordTooShort;
        if (_confirm.text != _password.text) _confirmError = l10n.passwordsDontMatch;
      } else if (_mode == EmailFormMode.signIn && _password.text.isEmpty) {
        _passwordError = l10n.passwordTooShort;
      }
    });
    return _emailError == null && _passwordError == null && _confirmError == null;
  }

  Future<void> _submit() async {
    if (!_validate()) return;
    final email = _emailController.text.trim();
    switch (_mode) {
      case EmailFormMode.signIn:
        final signIn = widget.onSignIn;
        if (signIn != null) {
          await signIn(email, _password.text);
        } else {
          await ref.read(authProvider.notifier).signInWithEmail(email, _password.text);
        }
      case EmailFormMode.register:
        final name = _name.text.trim();
        await ref
            .read(authProvider.notifier)
            .registerWithEmail(email, _password.text, displayName: name.isEmpty ? null : name);
      case EmailFormMode.reset:
        try {
          final reset = widget.onPasswordReset;
          if (reset != null) {
            await reset(email);
          } else {
            await ref.read(authProvider.notifier).sendPasswordReset(email);
          }
        } catch (_) {
          // Network trouble reads the same as success here; the user can simply try again.
        }
        if (mounted) setState(() => _resetSent = true);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final palette = AuraPalette.of(context);
    final registering = _mode == EmailFormMode.register;
    final resetting = _mode == EmailFormMode.reset;
    return AutofillGroup(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (registering) ...[
            AuraTextField(
              controller: _name,
              label: l10n.nameFieldLabel,
              autofillHints: const [AutofillHints.name],
              textInputAction: TextInputAction.next,
            ),
            const SizedBox(height: AuraSpacing.md),
          ],
          AuraTextField(
            controller: _emailController,
            label: l10n.emailFieldLabel,
            errorText: _emailError,
            keyboardType: TextInputType.emailAddress,
            autofillHints: const [AutofillHints.email],
            textInputAction: resetting ? TextInputAction.done : TextInputAction.next,
          ),
          if (!resetting) ...[
            const SizedBox(height: AuraSpacing.md),
            AuraTextField(
              controller: _password,
              label: l10n.passwordFieldLabel,
              errorText: _passwordError,
              obscureText: true,
              autofillHints: [registering ? AutofillHints.newPassword : AutofillHints.password],
              textInputAction: registering ? TextInputAction.next : TextInputAction.done,
              onSubmitted: registering ? null : (_) => _submit(),
            ),
          ],
          if (registering) ...[
            const SizedBox(height: AuraSpacing.md),
            AuraTextField(
              controller: _confirm,
              label: l10n.confirmPasswordFieldLabel,
              errorText: _confirmError,
              obscureText: true,
              autofillHints: const [AutofillHints.newPassword],
              textInputAction: TextInputAction.done,
              onSubmitted: (_) => _submit(),
            ),
          ],
          const SizedBox(height: AuraSpacing.lg),
          if (resetting && _resetSent) ...[
            AuraBanner(icon: Icons.mark_email_read_outlined, message: l10n.emailResetSent),
            const SizedBox(height: AuraSpacing.md),
          ],
          AuraButton(
            label: switch (_mode) {
              EmailFormMode.signIn => l10n.emailSignInAction,
              EmailFormMode.register => l10n.emailCreateAccountAction,
              EmailFormMode.reset => l10n.emailSendReset,
            },
            expand: true,
            onPressed: _submit,
          ),
          const SizedBox(height: AuraSpacing.sm),
          if (_mode == EmailFormMode.signIn) ...[
            TextButton(
              onPressed: () => _switchTo(EmailFormMode.reset),
              child: Text(l10n.emailForgotPassword, style: TextStyle(color: palette.textSecondary)),
            ),
            if (widget.onSignIn == null)
              TextButton(
                onPressed: () => _switchTo(EmailFormMode.register),
                child: Text(l10n.emailSwitchToRegister),
              ),
          ] else
            TextButton(
              onPressed: () => _switchTo(EmailFormMode.signIn),
              child: Text(l10n.emailSwitchToSignIn),
            ),
        ],
      ),
    );
  }
}
