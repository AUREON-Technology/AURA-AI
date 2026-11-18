// WP9 (G-12): a daemon the server stopped accepting (close 4401, `credentials_rejected`) must be
// fixable from wherever the person is. The IDE status bar's daemon item now says "Re-pair
// required" and opens pairing; and the pairing dialog closes and says why when pairing fails,
// on the daemon page and in the Host panel alike (both run the one shared flow).
import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/hostd/hostd_control_client.dart';
import 'package:aura_client/core/hostd/hostd_providers.dart';
import 'package:aura_client/core/hostd/hostd_supervisor_state.dart';
import 'package:aura_client/core/security/encryption_service.dart';
import 'package:aura_client/desktop/events/ide_event_bus.dart';
import 'package:aura_client/desktop/host/host_panel.dart';
import 'package:aura_client/desktop/shell/status_bar.dart';
import 'package:aura_client/desktop/state/state_notifiers.dart';
import 'package:aura_client/desktop/update/desktop_update_providers.dart';
import 'package:aura_client/features/engineering/presentation/host_daemon_page.dart';
import 'package:aura_client/l10n/generated/app_localizations.dart';
import 'package:aura_client/l10n/generated/app_localizations_en.dart';
import 'package:drift/native.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

final _l10n = AppLocalizationsEn();

/// The daemon: pairing starts and shows a code.
class _FakeHostd extends HostdControlClient {
  int starts = 0;

  @override
  Future<Map<String, dynamic>> startPairing({String? backendUrl}) async {
    starts++;
    return {'user_code': 'ABCD-1234', 'interval': 1, 'expires_in': 60};
  }
}

/// Signed out: the approval step fails after the code dialog is already up.
class _SignedOut extends TokenManager {
  _SignedOut(super.ref);

  @override
  Future<String?> getValidAccessToken() async => null;
}

class _FixedPath extends HostdExecutablePathNotifier {
  @override
  Future<String?> build() async => null;
}

final _refused = HostdStatusSnapshot(
  reachable: true,
  authenticated: true,
  backendConnected: false,
  credentialsRejected: true,
  lastCloseCode: 4401,
);

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late AppDatabase db;
  late IdeEventBus bus;

  setUp(() async {
    await EncryptionService.init();
    db = AppDatabase.forTesting(NativeDatabase.memory());
    bus = IdeEventBus();
  });

  tearDown(() async {
    await bus.close();
    await db.close();
  });

  Future<_FakeHostd> pump(WidgetTester tester, Widget body, HostdStatusSnapshot status) async {
    tester.view.physicalSize = const Size(1400, 1600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final hostd = _FakeHostd();
    await tester.pumpWidget(ProviderScope(
      overrides: [
        databaseProvider.overrideWithValue(db),
        ideEventBusProvider.overrideWithValue(bus),
        hostdStatusProvider.overrideWith((ref) => Stream.value(status)),
        pendingApprovalsProvider.overrideWith((ref) => Stream.value(const [])),
        hostdSupervisorStateProvider.overrideWith((ref) => Stream.value(HostdSupervisorState.idle)),
        hostdExecutablePathProvider.overrideWith(_FixedPath.new),
        hostdControlClientProvider.overrideWithValue(hostd),
        tokenManagerProvider.overrideWith((ref) => _SignedOut(ref)),
        desktopUpdateProvider.overrideWith((ref) async => null),
        workspaceNotifierProvider.overrideWith((ref) => WorkspaceNotifier(db, bus)),
      ],
      child: MaterialApp(
        theme: AuraThemeV2.dark,
        localizationsDelegates: AppLocalizations.localizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        home: body,
      ),
    ));
    await tester.pump();
    await tester.pump();
    return hostd;
  }

  Future<void> settle(WidgetTester tester) async {
    for (var i = 0; i < 5; i++) {
      await tester.pump(const Duration(milliseconds: 100));
    }
  }

  void expectClosedWithReason(WidgetTester tester) {
    expect(find.text(_l10n.engineeringPairWaiting), findsNothing, reason: 'the code dialog closed');
    expect(find.text('ABCD-1234'), findsNothing);
    expect(
      find.text(_l10n.engineeringPairingStartFailed(_l10n.engineeringPairSignInFirst)),
      findsOneWidget,
      reason: 'and said why',
    );
  }

  /// Unmount inside the test so Drift's stream-close timer fires before the binding checks.
  Future<void> unmount(WidgetTester tester) async {
    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pump(const Duration(milliseconds: 1));
    await tester.pump(const Duration(milliseconds: 1));
  }

  testWidgets('status bar: a refused daemon reads "Re-pair required" and the item opens pairing',
      (tester) async {
    final hostd = await pump(tester, const Scaffold(body: Column(children: [Spacer(), StatusBar()])), _refused);

    expect(find.text(_l10n.hostStatusRepairRequired), findsOneWidget);
    expect(find.text(_l10n.ideStatusReconnecting), findsNothing);
    await tester.tap(find.byKey(const ValueKey('status-bar-repair')));
    await settle(tester);

    expect(hostd.starts, 1);
    expectClosedWithReason(tester);
    await unmount(tester);
  });

  testWidgets('status bar: a healthy daemon item is not a pairing button', (tester) async {
    await pump(
      tester,
      const Scaffold(body: Column(children: [Spacer(), StatusBar()])),
      HostdStatusSnapshot(reachable: true, authenticated: true, backendConnected: true),
    );

    expect(find.byKey(const ValueKey('status-bar-repair')), findsNothing);
    expect(find.text(_l10n.ideStatusHostConnected), findsOneWidget);
    await unmount(tester);
  });

  testWidgets('Host panel: a failed pairing closes the dialog and says why', (tester) async {
    final hostd = await pump(tester, const Scaffold(body: SingleChildScrollView(child: HostPanel())), _refused);

    await tester.tap(find.text(_l10n.hostRepair));
    await settle(tester);

    expect(hostd.starts, 1);
    expectClosedWithReason(tester);
    await unmount(tester);
  });

  testWidgets('daemon page: a failed pairing closes the dialog and says why', (tester) async {
    final hostd = await pump(tester, const HostDaemonPage(), _refused);

    await tester.tap(find.text(_l10n.engineeringPairAgain));
    await settle(tester);

    expect(hostd.starts, 1);
    expectClosedWithReason(tester);
    await unmount(tester);
  });
}
