import 'package:flutter/foundation.dart' show TargetPlatform, defaultTargetPlatform, kDebugMode, kIsWeb;
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:local_auth/local_auth.dart';
import 'auth_provider.dart';
import 'email_sign_in_form.dart';
import 'package:aura_client/core/auth/identity_service.dart';
import 'package:aura_client/core/auth/platform_login_support.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/network/aura_failure.dart';
import 'package:aura_client/l10n/l10n.dart';

/// The device's biometric API, behind a provider so tests can stand in for it.
final localAuthenticationProvider = Provider<LocalAuthentication>((ref) => LocalAuthentication());

/// What the login page does on open about a session this device already holds.
enum CachedSessionUnlock {
  /// Nothing: no saved session, or it was just ended by the server ([sessionExpiredProvider]),
  /// in which case the user should read why before anything signs them back in.
  none,

  /// Ask for a fingerprint or face first, then resume it.
  biometric,

  /// Resume it straight away. With no biometric enrolled (or none on this platform: Linux,
  /// Windows without Hello, web) there is nothing to prompt for, and until this existed such a
  /// device had to go through Google on every launch although a valid refresh token was on disk.
  /// The device's own lock screen is the gate here, as it is for any app that keeps you signed
  /// in.
  direct,
}

CachedSessionUnlock cachedSessionUnlock({
  required bool hasCachedSession,
  required bool sessionExpired,
  required bool biometricsEnrolled,
}) {
  if (!hasCachedSession || sessionExpired) return CachedSessionUnlock.none;
  return biometricsEnrolled ? CachedSessionUnlock.biometric : CachedSessionUnlock.direct;
}

/// Where "Continue with Apple" is offered (SaaS P1-B). The App Store requires it on iOS once
/// Google sign-in is offered; macOS and the web support it through Firebase. Android and the
/// Linux/Windows desktop builds offer Google and email instead.
bool appleSignInOffered({required TargetPlatform platform, required bool isWeb}) =>
    isWeb || platform == TargetPlatform.iOS || platform == TargetPlatform.macOS;

/// V2 rebuild (Doc/V2_BLUEPRINT.md §4) — same auth flows as V1, restyled
/// on the design system (light+dark-ready, contrast-checked palette,
/// shared AuraCard/AuraButton components) instead of the page's own
/// hand-rolled BoxDecoration/ElevatedButton.styleFrom calls.
class LoginPage extends ConsumerStatefulWidget {
  const LoginPage({super.key});

  @override
  ConsumerState<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends ConsumerState<LoginPage> {
  final _nameController = TextEditingController();
  final _emailController = TextEditingController();
  bool _showDeveloperPanel = false;
  bool _showEmailForm = false;

  late final LocalAuthentication _localAuth = ref.read(localAuthenticationProvider);
  bool _canCheckBiometrics = false;
  bool _hasCachedCredentials = false;

  @override
  void initState() {
    super.initState();
    _resumeCachedSession();
  }

  /// Whether a fingerprint or face is actually enrolled. `canCheckBiometrics` alone only says
  /// the hardware exists, and `biometricOnly` then fails on a phone with nothing enrolled.
  /// Each check is guarded on its own: local_auth has no Linux implementation, and a throw
  /// there must not also hide the saved session.
  Future<bool> _biometricsEnrolled() async {
    try {
      if (!await _localAuth.canCheckBiometrics) return false;
      return (await _localAuth.getAvailableBiometrics()).isNotEmpty;
    } catch (_) {
      return false;
    }
  }

  Future<void> _resumeCachedSession() async {
    final enrolled = await _biometricsEnrolled();
    var hasCache = false;
    try {
      hasCache = await ref.read(authProvider.notifier).hasCachedCredentials();
    } catch (_) {}
    if (!mounted) return;
    setState(() {
      _canCheckBiometrics = enrolled;
      _hasCachedCredentials = hasCache;
    });
    switch (cachedSessionUnlock(
      hasCachedSession: hasCache,
      sessionExpired: ref.read(sessionExpiredProvider),
      biometricsEnrolled: enrolled,
    )) {
      case CachedSessionUnlock.biometric:
        await _authenticateBiometrically();
      case CachedSessionUnlock.direct:
        await ref.read(authProvider.notifier).signInCached();
      case CachedSessionUnlock.none:
        break;
    }
  }

  Future<void> _authenticateBiometrically() async {
    try {
      final authenticated = await _localAuth.authenticate(
        localizedReason: context.l10n.loginBiometricReason,
        options: const AuthenticationOptions(stickyAuth: true, biometricOnly: true),
      );
      if (!mounted) return;
      if (authenticated) {
        await ref.read(authProvider.notifier).signInCached();
      }
    } catch (e) {
      debugPrint("Biometric auth failed: $e");
    }
  }

  @override
  void dispose() {
    _nameController.dispose();
    _emailController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    // ref.listen must be called directly in the owning ConsumerState's
    // build() — Riverpod asserts on this — not from a method invoked via a
    // nested Builder callback (which _buildBody's own context is, since
    // it's used purely to get a BuildContext scoped under the Theme override).
    ref.listen(authProvider, (previous, next) {
      if (next.hasError && kDebugMode) {
        if (!mounted) return;
        setState(() => _showDeveloperPanel = true);
      }
    });

    return _buildBody(context);
  }

  Widget _buildBody(BuildContext context) {
    final palette = AuraPalette.of(context);
    final authState = ref.watch(authProvider);
    final l10n = context.l10n;

    return Scaffold(
      body: Container(
        decoration: BoxDecoration(
          gradient: RadialGradient(
            center: const Alignment(0.3, -0.5),
            radius: 1.5,
            colors: [palette.brand.withValues(alpha: 0.25), palette.background],
          ),
        ),
        child: Center(
          child: SingleChildScrollView(
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: AuraSpacing.xl),
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Container(
                    width: 96,
                    height: 96,
                    decoration: BoxDecoration(
                      gradient: palette.brandGradient,
                      borderRadius: BorderRadius.circular(AuraRadius.xl),
                      boxShadow: [BoxShadow(color: palette.brand.withValues(alpha: 0.5), blurRadius: 30, offset: const Offset(0, 8))],
                    ),
                    child: ClipRRect(
                      borderRadius: BorderRadius.circular(AuraRadius.xl),
                      child: Padding(
                        padding: const EdgeInsets.all(12.0),
                        child: Image.asset('assets/app_icon.png', fit: BoxFit.contain),
                      ),
                    ),
                  ),
                  const SizedBox(height: AuraSpacing.xl),
                  Text(l10n.appName, style: Theme.of(context).textTheme.displayLarge?.copyWith(letterSpacing: 2.0)),
                  const SizedBox(height: AuraSpacing.xs),
                  Text(l10n.loginTagline, style: Theme.of(context).textTheme.bodyMedium?.copyWith(letterSpacing: 1.0)),
                  const SizedBox(height: AuraSpacing.xxxl),
                  SizedBox(
                    width: 450,
                    child: AuraCard(
                      padding: const EdgeInsets.all(AuraSpacing.xxl),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          Text(l10n.loginWelcomeBack, style: Theme.of(context).textTheme.titleLarge, textAlign: TextAlign.center),
                          const SizedBox(height: AuraSpacing.sm),
                          Text(
                            l10n.loginSubtitle,
                            textAlign: TextAlign.center,
                            style: Theme.of(context).textTheme.bodySmall,
                          ),
                          if (ref.watch(sessionExpiredProvider)) ...[
                            const SizedBox(height: AuraSpacing.lg),
                            AuraBanner(
                              icon: Icons.lock_clock_outlined,
                              message: l10n.loginSessionExpired,
                            ),
                          ],
                          const SizedBox(height: AuraSpacing.xxl),
                          if (authState.isLoading)
                            Center(child: CircularProgressIndicator(color: palette.brand))
                          else ...[
                            if (supportsNativeGoogleSignIn())
                              _GoogleSignInButton(onPressed: () => ref.read(authProvider.notifier).signInWithGoogle())
                            else
                              AuraButton(
                                label: l10n.loginGoogleBrowser,
                                icon: Icons.open_in_browser,
                                expand: true,
                                onPressed: () => ref.read(authProvider.notifier).signInWithDesktopOAuth(),
                              ),
                            if (supportsNativeGoogleSignIn()) ...[
                              if (appleSignInOffered(platform: defaultTargetPlatform, isWeb: kIsWeb)) ...[
                                const SizedBox(height: AuraSpacing.md),
                                AuraButton(
                                  label: l10n.loginApple,
                                  icon: Icons.apple,
                                  expand: true,
                                  variant: AuraButtonVariant.secondary,
                                  onPressed: () => ref.read(authProvider.notifier).signInWithApple(),
                                ),
                              ],
                              const SizedBox(height: AuraSpacing.md),
                              if (_showEmailForm)
                                const EmailSignInForm()
                              else
                                AuraButton(
                                  label: l10n.loginEmail,
                                  icon: Icons.mail_outline,
                                  expand: true,
                                  variant: AuraButtonVariant.secondary,
                                  onPressed: () => setState(() => _showEmailForm = true),
                                ),
                            ],
                            const SizedBox(height: AuraSpacing.md),
                            if (_canCheckBiometrics && _hasCachedCredentials) ...[
                              AuraButton(
                                label: l10n.loginUnlockBiometrics,
                                icon: Icons.fingerprint,
                                onPressed: _authenticateBiometrically,
                                expand: true,
                              ),
                              const SizedBox(height: AuraSpacing.md),
                            ],
                            if (kDebugMode) ...[
                              TextButton(
                                onPressed: () => setState(() => _showDeveloperPanel = !_showDeveloperPanel),
                                child: Text(
                                  _showDeveloperPanel ? l10n.loginHideDeveloperPanel : l10n.loginShowDeveloperPanel,
                                  style: TextStyle(color: palette.textSecondary, fontSize: 13),
                                ),
                              ),
                              if (_showDeveloperPanel) ...[
                                Divider(color: palette.border, height: AuraSpacing.xxl),
                                TextField(
                                  controller: _nameController,
                                  decoration: InputDecoration(labelText: l10n.loginDeveloperName, hintText: l10n.loginDeveloperNameHint),
                                ),
                                const SizedBox(height: AuraSpacing.md),
                                TextField(
                                  controller: _emailController,
                                  decoration: InputDecoration(labelText: l10n.loginDeveloperEmail, hintText: l10n.loginDeveloperEmailHint),
                                ),
                                const SizedBox(height: AuraSpacing.lg),
                                AuraButton(
                                  label: l10n.loginDeveloperAccess,
                                  expand: true,
                                  onPressed: () => ref.read(authProvider.notifier).signInMock(
                                        _nameController.text.trim(),
                                        _emailController.text.trim(),
                                      ),
                                ),
                              ],
                            ],
                          ],
                          if (authState.hasError) ...[
                            const SizedBox(height: AuraSpacing.md),
                            Container(
                              padding: const EdgeInsets.all(AuraSpacing.md),
                              decoration: BoxDecoration(
                                color: palette.danger.withValues(alpha: 0.1),
                                borderRadius: BorderRadius.circular(AuraRadius.sm),
                                border: Border.all(color: palette.danger.withValues(alpha: 0.3)),
                              ),
                              child: Column(
                                children: [
                                  // Through AuraFailure, not the raw exception (audit F-33); the
                                  // exception itself is only shown to developers.
                                  Text(
                                    _signInFailureText(authState.error, l10n),
                                    style: TextStyle(color: palette.danger, fontSize: 12),
                                    textAlign: TextAlign.center,
                                  ),
                                  if (kDebugMode) ...[
                                    if (AuraFailure.from(authState.error).detail != null) ...[
                                      const SizedBox(height: AuraSpacing.sm),
                                      Text(
                                        AuraFailure.from(authState.error).detail!,
                                        style: TextStyle(color: palette.textSecondary, fontSize: 11),
                                        textAlign: TextAlign.center,
                                      ),
                                    ],
                                    const SizedBox(height: AuraSpacing.sm),
                                    Text(
                                      l10n.loginDeveloperTip,
                                      style: TextStyle(color: palette.textSecondary, fontSize: 11),
                                      textAlign: TextAlign.center,
                                    ),
                                  ],
                                ],
                              ),
                            ),
                          ],
                        ],
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// Google's brand guidelines require a white pill button with their logo —
/// kept as a one-off rather than forced through [AuraButton]'s variants.
class _GoogleSignInButton extends StatelessWidget {
  final VoidCallback onPressed;
  const _GoogleSignInButton({required this.onPressed});

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: double.infinity,
      child: ElevatedButton.icon(
        onPressed: onPressed,
        icon: Image.network(
          'https://upload.wikimedia.org/wikipedia/commons/thumb/5/53/Google_%22G%22_Logo.svg/512px-Google_%22G%22_Logo.svg.png',
          height: 18,
          width: 18,
          errorBuilder: (context, error, stackTrace) => const Icon(Icons.login, color: Colors.black87),
        ),
        label: Text(context.l10n.loginGoogle),
        style: ElevatedButton.styleFrom(
          backgroundColor: Colors.white,
          foregroundColor: Colors.black87,
          padding: const EdgeInsets.symmetric(vertical: 16),
          minimumSize: const Size(0, 44),
        ),
      ),
    );
  }
}

/// What a failed sign-in says to the user: [AuraFailure]'s words, never the
/// exception's `toString()`.
String _signInFailureText(Object? error, AppLocalizations l10n) {
  if (error is IdentityError) return identityErrorText(error.kind, l10n);
  final failure = AuraFailure.from(error);
  return '${failure.title}. ${failure.message}';
}

/// What the identity provider's refusal says to the user (SaaS P1-B). `cancelled` never reaches
/// here: `AuthNotifier` treats closing the provider's sheet as no sign-in at all.
String identityErrorText(IdentityErrorKind kind, AppLocalizations l10n) => switch (kind) {
      IdentityErrorKind.wrongCredentials => l10n.identityWrongCredentials,
      IdentityErrorKind.emailInUse => l10n.identityEmailInUse,
      IdentityErrorKind.weakPassword => l10n.identityWeakPassword,
      IdentityErrorKind.invalidEmail => l10n.identityInvalidEmail,
      IdentityErrorKind.network => l10n.identityNetwork,
      IdentityErrorKind.accountExistsWithDifferentMethod => l10n.identityDifferentMethod,
      IdentityErrorKind.cancelled || IdentityErrorKind.unavailable => l10n.identityUnavailable,
    };
