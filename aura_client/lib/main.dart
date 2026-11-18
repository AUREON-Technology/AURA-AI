import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';
import 'package:firebase_core/firebase_core.dart';
import 'core/auth/token_manager.dart';
import 'core/config/backend_url_policy.dart';
import 'core/config/config.dart';
import 'core/design_system/design_system.dart';
import 'core/locale/app_language.dart';
import 'core/platform/document_language.dart';
import 'core/theme/theme_mode_controller.dart';
import 'core/routing/router.dart';
import 'core/security/encryption_service.dart';
import 'features/workspaces/presentation/store_purchase.dart';
import 'l10n/l10n.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  if (kReleaseMode) {
    silenceDebugLogging();
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
  // From app start, so a store transaction left unfinished (the app was killed mid-purchase) is
  // finished on the next launch (SaaS P1-F2b). A no-op where there is no store.
  container.read(storePurchaseListenerProvider);

  runApp(
    UncontrolledProviderScope(
      container: container,
      child: const AuraApp(),
    ),
  );
}

/// `debugPrint` is not stripped from a release build: it writes to logcat and the iOS device
/// log, which other tools on the device and anyone with a cable can read. The app's ~120 calls
/// include server response bodies (sync, memory, token exchange errors). A store build prints
/// none of them.
@visibleForTesting
void silenceDebugLogging() {
  debugPrint = (String? message, {int? wrapWidth}) {};
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

    final themeMode = ref.watch(themeModeProvider);
    final language = ref.watch(appLanguageProvider);

    return MaterialApp.router(
      title: 'AURA AI',
      debugShowCheckedModeBanner: false,
      theme: AuraThemeV2.light,
      darkTheme: AuraThemeV2.dark,
      themeMode: themeMode,
      routerConfig: router,
      // English and Egyptian Arabic (docs/plans/arabic-language_plan.md). `locale` is the
      // user's choice from Settings -> Appearance; null follows the device, resolved against
      // `supportedLocales` (so an ar-EG phone gets `ar`, which formats with Western digits).
      locale: language.locale,
      localizationsDelegates: const [
        AppLocalizations.delegate,
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
      supportedLocales: AppLocalizations.supportedLocales,
      // C1: a device-attestation 403 during token exchange previously only
      // hit debugPrint, indistinguishable from a transient network failure
      // even though it won't self-resolve on retry. Persistent, dismissible,
      // and never blocking — everything not requiring a backend session
      // should stay usable.
      builder: (context, child) {
        if (child == null) return const SizedBox.shrink();
        // Every DateFormat/NumberFormat that names no locale follows the language on screen.
        final languageCode = Localizations.localeOf(context).languageCode;
        Intl.defaultLocale = languageCode;
        setDocumentLanguage(languageCode);
        setCurrentL10n(AppLocalizations.of(context));
        final failure = ref.watch(authExchangeFailureProvider);
        if (failure != AuthExchangeFailure.deviceAttestationRejected) return child;
        return Column(
          children: [
            MaterialBanner(
              content: Text(context.l10n.deviceUnverifiedBanner),
              actions: [
                TextButton(
                  onPressed: () => ref
                      .read(authExchangeFailureProvider.notifier)
                      .set(AuthExchangeFailure.none),
                  child: Text(context.l10n.commonDismiss),
                ),
              ],
            ),
            Expanded(child: child),
          ],
        );
      },
    );
  }
}
