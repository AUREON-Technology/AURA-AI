import 'package:flutter/foundation.dart' show kDebugMode;
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:local_auth/local_auth.dart';
import 'auth_provider.dart';
import 'package:aura_client/core/auth/platform_login_support.dart';
import 'package:aura_client/core/design_system/design_system.dart';

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

  final LocalAuthentication _localAuth = LocalAuthentication();
  bool _canCheckBiometrics = false;
  bool _hasCachedCredentials = false;

  @override
  void initState() {
    super.initState();
    _checkBiometricStatus();
  }

  void _checkBiometricStatus() async {
    try {
      final canCheck = await _localAuth.canCheckBiometrics;
      final hasCache = await ref.read(authProvider.notifier).hasCachedCredentials();
      if (!mounted) return;
      setState(() {
        _canCheckBiometrics = canCheck;
        _hasCachedCredentials = hasCache;
      });
      if (canCheck && hasCache) {
        _authenticateBiometrically();
      }
    } catch (_) {}
  }

  Future<void> _authenticateBiometrically() async {
    try {
      final authenticated = await _localAuth.authenticate(
        localizedReason: 'Authenticate to unlock your AURA AI profile',
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
                  Text('AURA AI', style: Theme.of(context).textTheme.displayLarge?.copyWith(letterSpacing: 2.0)),
                  const SizedBox(height: AuraSpacing.xs),
                  Text('Personal AI Operating System', style: Theme.of(context).textTheme.bodyMedium?.copyWith(letterSpacing: 1.0)),
                  const SizedBox(height: AuraSpacing.xxxl),
                  SizedBox(
                    width: 450,
                    child: AuraCard(
                      padding: const EdgeInsets.all(AuraSpacing.xxl),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          Text('Welcome back', style: Theme.of(context).textTheme.titleLarge, textAlign: TextAlign.center),
                          const SizedBox(height: AuraSpacing.sm),
                          Text(
                            'Sign in to sync your schedules, studies, workspace, and agents.',
                            textAlign: TextAlign.center,
                            style: Theme.of(context).textTheme.bodySmall,
                          ),
                          const SizedBox(height: AuraSpacing.xxl),
                          if (authState.isLoading)
                            Center(child: CircularProgressIndicator(color: palette.brand))
                          else ...[
                            if (supportsNativeGoogleSignIn())
                              _GoogleSignInButton(onPressed: () => ref.read(authProvider.notifier).signInWithGoogle())
                            else
                              AuraButton(
                                label: 'Sign in with Google (browser)',
                                icon: Icons.open_in_browser,
                                expand: true,
                                onPressed: () => ref.read(authProvider.notifier).signInWithDesktopOAuth(),
                              ),
                            const SizedBox(height: AuraSpacing.md),
                            if (_canCheckBiometrics && _hasCachedCredentials) ...[
                              AuraButton(
                                label: 'Unlock with biometrics',
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
                                  _showDeveloperPanel ? 'Hide developer panel' : 'Developer / offline access',
                                  style: TextStyle(color: palette.textSecondary, fontSize: 13),
                                ),
                              ),
                              if (_showDeveloperPanel) ...[
                                Divider(color: palette.border, height: AuraSpacing.xxl),
                                TextField(
                                  controller: _nameController,
                                  decoration: const InputDecoration(labelText: 'Developer name', hintText: 'Enter name'),
                                ),
                                const SizedBox(height: AuraSpacing.md),
                                TextField(
                                  controller: _emailController,
                                  decoration: const InputDecoration(labelText: 'Developer email', hintText: 'Enter email'),
                                ),
                                const SizedBox(height: AuraSpacing.lg),
                                AuraButton(
                                  label: 'Access developer environment',
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
                                  Text('Error: ${authState.error}', style: TextStyle(color: palette.danger, fontSize: 12), textAlign: TextAlign.center),
                                  if (kDebugMode) ...[
                                    const SizedBox(height: AuraSpacing.sm),
                                    Text(
                                      'Tip: Google Sign-In failed. To test locally without registering SHA-1 fingerprints in the Google Console, use the "Access Developer Environment" bypass option above.',
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
        label: const Text('Sign in with Google'),
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
