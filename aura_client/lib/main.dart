import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:firebase_core/firebase_core.dart';
import 'core/theme/theme.dart';
import 'core/routing/router.dart';
import 'core/security/encryption_service.dart';
import 'l10n/generated/app_localizations.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
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
