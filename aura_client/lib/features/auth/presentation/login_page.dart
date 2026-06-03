import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:local_auth/local_auth.dart';
import 'auth_provider.dart';
import '../../../core/theme/theme.dart';

class LoginPage extends ConsumerStatefulWidget {
  const LoginPage({super.key});

  @override
  ConsumerState<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends ConsumerState<LoginPage> {
  final _nameController = TextEditingController(text: 'Anas Ibrahim');
  final _emailController = TextEditingController(text: 'anas.ibrahim.gnedy@gmail.com');
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
      setState(() {
        _canCheckBiometrics = canCheck;
        _hasCachedCredentials = hasCache;
      });
      // Auto trigger if biometric is active and credentials exist
      if (canCheck && hasCache) {
        _authenticateBiometrically();
      }
    } catch (_) {}
  }

  Future<void> _authenticateBiometrically() async {
    try {
      final authenticated = await _localAuth.authenticate(
        localizedReason: 'Authenticate to unlock your AURA AI profile',
        options: const AuthenticationOptions(
          stickyAuth: true,
          biometricOnly: true,
        ),
      );
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
    final authState = ref.watch(authProvider);

    return Scaffold(
      body: Container(
        decoration: const BoxDecoration(
          gradient: RadialGradient(
            center: Alignment(0.3, -0.5),
            radius: 1.5,
            colors: [
              Color(0xFF1E1B4B), // Indigo dark
              Color(0xFF0F0C1B), // Core background
            ],
          ),
        ),
        child: Center(
          child: SingleChildScrollView(
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 24.0),
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  // Logo & Brand Header
                  Container(
                    width: 96,
                    height: 96,
                    decoration: BoxDecoration(
                      gradient: AuraTheme.primaryGradient,
                      borderRadius: BorderRadius.circular(28),
                      boxShadow: [
                        BoxShadow(
                          color: AuraTheme.accentIndigo.withValues(alpha: 0.5),
                          blurRadius: 30,
                          offset: const Offset(0, 8),
                        ),
                      ],
                    ),
                    child: ClipRRect(
                      borderRadius: BorderRadius.circular(28),
                      child: Padding(
                        padding: const EdgeInsets.all(12.0),
                        child: Image.asset(
                          'assets/app_icon.png',
                          fit: BoxFit.contain,
                        ),
                      ),
                    ),
                  ),
                  const SizedBox(height: 24),
                  Text(
                    'AURA AI',
                    style: Theme.of(context).textTheme.displayLarge?.copyWith(
                          letterSpacing: 2.0,
                          fontWeight: FontWeight.w900,
                        ),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    'Personal AI Operating System',
                    style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                          letterSpacing: 1.0,
                          color: AuraTheme.textSecondary,
                        ),
                  ),
                  const SizedBox(height: 48),

                  // Auth Card (Glassmorphic)
                  AnimatedContainer(
                    duration: const Duration(milliseconds: 300),
                    width: 450,
                    padding: const EdgeInsets.all(32),
                    decoration: BoxDecoration(
                      color: AuraTheme.cardBg,
                      borderRadius: BorderRadius.circular(24),
                      border: Border.all(
                        color: const Color(0x1F94A3B8),
                        width: 1,
                      ),
                    ),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        Text(
                          'Welcome back',
                          style: Theme.of(context).textTheme.titleLarge,
                          textAlign: TextAlign.center,
                        ),
                        const SizedBox(height: 8),
                        const Text(
                          'Sign in to sync your schedules, studies, workspace, and agents.',
                          textAlign: TextAlign.center,
                          style: TextStyle(color: AuraTheme.textSecondary, fontSize: 13),
                        ),
                        const SizedBox(height: 32),

                        // Login Action
                        if (authState.isLoading)
                          const Center(
                            child: CircularProgressIndicator(color: AuraTheme.accentIndigo),
                          )
                        else ...[
                          // Google Sign-In Button
                           ElevatedButton.icon(
                            onPressed: () {
                              ref.read(authProvider.notifier).signInWithGoogle();
                            },
                            icon: Image.network(
                              'https://upload.wikimedia.org/wikipedia/commons/thumb/5/53/Google_%22G%22_Logo.svg/512px-Google_%22G%22_Logo.svg.png',
                              height: 18,
                              width: 18,
                              errorBuilder: (context, error, stackTrace) => const Icon(
                                Icons.login,
                                color: Colors.black87,
                              ),
                            ),
                            label: const Text('Sign in with Google'),
                            style: ElevatedButton.styleFrom(
                              backgroundColor: Colors.white,
                              foregroundColor: Colors.black87,
                              padding: const EdgeInsets.symmetric(vertical: 16),
                            ),
                          ),
                          const SizedBox(height: 16),

                          if (_canCheckBiometrics && _hasCachedCredentials) ...[
                            ElevatedButton.icon(
                              onPressed: _authenticateBiometrically,
                              icon: const Icon(Icons.fingerprint, color: Colors.white),
                              label: const Text('Unlock with Biometrics'),
                              style: ElevatedButton.styleFrom(
                                backgroundColor: AuraTheme.accentIndigo,
                                foregroundColor: Colors.white,
                                padding: const EdgeInsets.symmetric(vertical: 16),
                              ),
                            ),
                            const SizedBox(height: 16),
                          ],

                          // Developer Bypass Options Toggle
                          TextButton(
                            onPressed: () {
                              setState(() {
                                _showDeveloperPanel = !_showDeveloperPanel;
                              });
                            },
                            child: Text(
                              _showDeveloperPanel
                                  ? 'Hide Developer Panel'
                                  : 'Developer / Offline Access',
                              style: const TextStyle(
                                color: AuraTheme.textSecondary,
                                fontSize: 13,
                              ),
                            ),
                          ),

                          if (_showDeveloperPanel) ...[
                            const Divider(color: Color(0x1F94A3B8), height: 32),
                            TextField(
                              controller: _nameController,
                              decoration: const InputDecoration(
                                labelText: 'Developer Name',
                                hintText: 'Enter name',
                              ),
                              style: const TextStyle(color: Colors.white),
                            ),
                            const SizedBox(height: 12),
                            TextField(
                              controller: _emailController,
                              decoration: const InputDecoration(
                                labelText: 'Developer Email',
                                hintText: 'Enter email',
                              ),
                              style: const TextStyle(color: Colors.white),
                            ),
                            const SizedBox(height: 20),
                            ElevatedButton(
                              onPressed: () {
                                ref.read(authProvider.notifier).signInMock(
                                      _nameController.text.trim(),
                                      _emailController.text.trim(),
                                    );
                              },
                              style: ElevatedButton.styleFrom(
                                backgroundColor: AuraTheme.accentIndigo,
                                foregroundColor: Colors.white,
                              ),
                              child: const Text('Access Developer Environment'),
                            ),
                          ],
                        ],

                        // Error message
                        if (authState.hasError) ...[
                          const SizedBox(height: 16),
                          Text(
                            'Error: ${authState.error}',
                            style: const TextStyle(color: Colors.redAccent, fontSize: 13),
                            textAlign: TextAlign.center,
                          ),
                        ],
                      ],
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
