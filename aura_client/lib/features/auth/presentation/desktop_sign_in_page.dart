/// `/desktop-sign-in`: the web page the desktop app (Linux, Windows) opens to sign in with
/// Firebase -- Google, Apple or email -- which FlutterFire cannot do on those platforms (module
/// readiness WP12).
///
/// The desktop app opens this page with `redirect_uri` (its loopback listener), `state` and
/// `code_challenge`. The page checks them, signs the person in with Firebase *only* (it starts no
/// AURA web session: no `AuthNotifier`, no `TokenManager`), trades the Firebase ID token at
/// `POST /api/v3/auth/desktop/handoff` for a one-time code, signs this browser out of Firebase
/// again (the computer may be shared), and sends this tab to `redirect_uri?code=&state=`. The
/// desktop app redeems the code with its PKCE verifier; see `desktop_oauth_client_io.dart`'s
/// `signInWithBrowserHandoff`.
///
/// The route is public (`core/routing/router.dart`): it must open signed out, and must not be
/// sent to onboarding when this browser happens to hold an AURA session.
library;

import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import 'package:aura_client/core/auth/desktop_handoff_client.dart';
import 'package:aura_client/core/auth/identity_service.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/l10n/l10n.dart';
import 'email_sign_in_form.dart';
import 'login_page.dart' show identityErrorText;

enum _Phase { choosing, working, done }

class DesktopSignInPage extends ConsumerStatefulWidget {
  const DesktopSignInPage({super.key, required this.query, bool? isWeb}) : _isWeb = isWeb;

  /// The page's query string, as the desktop app built it.
  final Map<String, String> query;

  /// Null means [kIsWeb]; tests pass true to render the web page on the test host.
  final bool? _isWeb;

  @override
  ConsumerState<DesktopSignInPage> createState() => _DesktopSignInPageState();
}

class _DesktopSignInPageState extends ConsumerState<DesktopSignInPage> {
  late final DesktopHandoffRequest? _request = DesktopHandoffRequest.fromQuery(widget.query);
  _Phase _phase = _Phase.choosing;
  String? _error;
  bool _showEmailForm = false;

  bool get _isWeb => widget._isWeb ?? kIsWeb;

  /// Signs in with [signIn], hands the result to the desktop app, and always leaves this browser
  /// signed out of Firebase again.
  Future<void> _handOff(Future<IdentityUser> Function() signIn) async {
    final request = _request;
    if (request == null || _phase != _Phase.choosing) return;
    final l10n = context.l10n;
    final identity = ref.read(identityServiceProvider);
    setState(() {
      _phase = _Phase.working;
      _error = null;
    });
    var signedIn = false;
    String? error;
    var done = false;
    try {
      await signIn();
      signedIn = true;
      final idToken = await identity.idToken();
      if (idToken == null || idToken.isEmpty) {
        throw DesktopHandoffFailure(l10n.desktopSignInNoIdentityToken);
      }
      final code = await ref
          .read(desktopHandoffClientProvider)
          .requestCode(idToken: idToken, request: request);
      // Out of Firebase before leaving: going back to the desktop app unloads this page, and
      // nothing here is needed any more -- the desktop app holds its own session now.
      await _signOut(identity);
      signedIn = false;
      final returned = await ref.read(desktopHandoffReturnProvider)(request.returnUri(code));
      if (!returned) throw DesktopHandoffFailure(l10n.desktopSignInReturnFailed);
      done = true;
    } on IdentityError catch (e) {
      // Closing the provider's window is a choice, not a failure: nothing to show.
      if (e.kind != IdentityErrorKind.cancelled) error = identityErrorText(e.kind, l10n);
    } on DesktopHandoffFailure catch (e) {
      error = e.message;
    } catch (_) {
      error = l10n.desktopSignInServerUnreachable;
    } finally {
      if (signedIn) await _signOut(identity);
    }
    if (!mounted) return;
    setState(() {
      _phase = done ? _Phase.done : _Phase.choosing;
      _error = error;
    });
  }

  Future<void> _signOut(IdentityService identity) async {
    try {
      await identity.signOut();
    } catch (_) {}
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final theme = Theme.of(context);
    final palette = AuraPalette.of(context);
    return Scaffold(
      body: Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(AuraSpacing.xl),
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 440),
            child: AuraCard(
              padding: const EdgeInsets.all(AuraSpacing.xxl),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Text(
                    l10n.desktopSignInTitle,
                    style: theme.textTheme.titleLarge,
                    textAlign: TextAlign.center,
                  ),
                  const SizedBox(height: AuraSpacing.lg),
                  ..._body(context, l10n, theme, palette),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  List<Widget> _body(
    BuildContext context,
    AppLocalizations l10n,
    ThemeData theme,
    AuraPalette palette,
  ) {
    if (!_isWeb) {
      return [
        Text(l10n.desktopSignInWebOnly, textAlign: TextAlign.center),
        const SizedBox(height: AuraSpacing.lg),
        AuraButton(
          label: l10n.desktopSignInGoToLogin,
          expand: true,
          onPressed: () => GoRouter.maybeOf(context)?.go('/login'),
        ),
      ];
    }
    if (_request == null) {
      return [
        AuraBanner(
          key: const Key('desktop-sign-in-invalid'),
          icon: Icons.link_off,
          tone: AuraStateTone.error,
          message: l10n.desktopSignInInvalidLink,
        ),
      ];
    }
    switch (_phase) {
      case _Phase.done:
        return [
          AuraBanner(
            icon: Icons.check_circle_outline,
            tone: AuraStateTone.neutral,
            message: l10n.desktopSignInDone,
          ),
          const SizedBox(height: AuraSpacing.md),
          Text(
            l10n.desktopSignInDoneHint,
            textAlign: TextAlign.center,
            style: theme.textTheme.bodySmall,
          ),
        ];
      case _Phase.choosing:
      case _Phase.working:
        // The choices stay on screen while working (the email form keeps what was typed, for a
        // retry after a wrong password); [_handOff] ignores them until the attempt is over.
        final working = _phase == _Phase.working;
        final identity = ref.read(identityServiceProvider);
        return [
          Text(
            l10n.desktopSignInSubtitle,
            textAlign: TextAlign.center,
            style: theme.textTheme.bodySmall,
          ),
          const SizedBox(height: AuraSpacing.xl),
          if (working) ...[
            Center(child: CircularProgressIndicator(color: palette.brand)),
            const SizedBox(height: AuraSpacing.md),
            Text(l10n.desktopSignInWorking, textAlign: TextAlign.center),
            const SizedBox(height: AuraSpacing.lg),
          ],
          if (_error != null) ...[
            AuraBanner(
              key: const Key('desktop-sign-in-error'),
              icon: Icons.error_outline,
              tone: AuraStateTone.error,
              message: _error!,
            ),
            const SizedBox(height: AuraSpacing.lg),
          ],
          AuraButton(
            label: l10n.loginGoogle,
            icon: Icons.login,
            expand: true,
            onPressed: working ? null : () => _handOff(identity.signInWithGoogle),
          ),
          // The web signs in to Apple through a Firebase popup on every platform.
          const SizedBox(height: AuraSpacing.md),
          AuraButton(
            label: l10n.loginApple,
            icon: Icons.apple,
            expand: true,
            variant: AuraButtonVariant.secondary,
            onPressed: working ? null : () => _handOff(identity.signInWithApple),
          ),
          const SizedBox(height: AuraSpacing.md),
          if (_showEmailForm)
            EmailSignInForm(
              onSignIn: (email, password) =>
                  _handOff(() => identity.signInWithEmail(email, password)),
              onPasswordReset: identity.sendPasswordReset,
            )
          else
            AuraButton(
              label: l10n.loginEmail,
              icon: Icons.mail_outline,
              expand: true,
              variant: AuraButtonVariant.secondary,
              onPressed: working ? null : () => setState(() => _showEmailForm = true),
            ),
          const SizedBox(height: AuraSpacing.lg),
          Text(
            l10n.desktopSignInSharedComputer,
            textAlign: TextAlign.center,
            style: theme.textTheme.bodySmall?.copyWith(color: palette.textSecondary),
          ),
        ];
    }
  }
}
