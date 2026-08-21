import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:firebase_core/firebase_core.dart';
import 'core/config/backend_url_policy.dart';
import 'core/config/config.dart';
import 'core/theme/theme.dart';
import 'core/routing/router.dart';
import 'core/security/encryption_service.dart';
import 'l10n/generated/app_localizations.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  if (kReleaseMode) {
    BackendUrlPolicy.assertSafeForRelease(AppConfig.backendUrl);
  }
  _warnIfAndroidDebugUsesLoopback();
  await EncryptionService.init();
  
  // Gracefully initialize Firebase for mobile/web,
  // falling back if not configured/supported on current platform
  try {
    await Firebase.initializeApp();
  } catch (e) {
    debugPrint("Firebase initialization skipped/failed: $e. Running in local/developer mode.");
  }

  final container = ProviderContainer();

  runApp(
    UncontrolledProviderScope(
      container: container,
      child: const AuraApp(),
    ),
  );
}

/// Physical Android treats loopback as the phone, not the PC/VPS.
/// Do not throw — `adb reverse` workflows still use localhost.
void _warnIfAndroidDebugUsesLoopback() {
  if (!kDebugMode) return;
  if (kIsWeb || defaultTargetPlatform != TargetPlatform.android) return;

  final host = Uri.tryParse(AppConfig.backendUrl)?.host.toLowerCase() ?? '';
  const loopback = {'localhost', '127.0.0.1', '::1'};
  if (!loopback.contains(host)) return;

  debugPrint(
    'HARD WARNING: Android debug BACKEND_URL is loopback '
    '(${AppConfig.backendUrl}). On a physical device that is the phone, '
    'not the PC or VPS. Use run_client_device.ps1 or the '
    '"AURA Android device (production)" launch config.',
  );
}

class AuraApp extends ConsumerWidget {
  const AuraApp({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final router = ref.watch(routerProvider);

    return MaterialApp.router(
      title: 'AURA AI',
      debugShowCheckedModeBanner: false,
      theme: AuraTheme.darkTheme,
      routerConfig: router,
      // V2 i18n scaffold (Doc/V2_BLUEPRINT.md §12) — only English shipped
      // so far (see lib/l10n/app_en.arb); this wiring is what makes adding
      // a second locale a translation-file change, not an app restructure.
      localizationsDelegates: const [
        AppLocalizations.delegate,
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
      supportedLocales: AppLocalizations.supportedLocales,
    );
  }
}
