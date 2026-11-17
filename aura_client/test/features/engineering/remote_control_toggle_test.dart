// WP8 (D-10, D-10b): "Allow control from my phone and the web" is a desktop-only switch on the
// daemon page and in the IDE's Host panel. It shows what the daemon's /status says, turning it on
// asks first, and every change goes to the local daemon (never the backend).
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/hostd/hostd_control_client.dart';
import 'package:aura_client/core/hostd/hostd_providers.dart';
import 'package:aura_client/core/hostd/hostd_supervisor_state.dart';
import 'package:aura_client/desktop/host/host_panel.dart';
import 'package:aura_client/features/engineering/presentation/host_daemon_page.dart';
import 'package:aura_client/features/engineering/presentation/remote_control_toggle.dart';
import 'package:aura_client/l10n/generated/app_localizations.dart';
import 'package:aura_client/l10n/generated/app_localizations_en.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

final _l10n = AppLocalizationsEn();

/// Stands in for the local control server: records every remote-control change and answers the
/// way the daemon does, with the value it now holds.
class _FakeControlClient extends HostdControlClient {
  _FakeControlClient({this.fail});

  final calls = <bool>[];
  final Object? fail;

  @override
  Future<bool> setRemoteControl(bool enabled) async {
    calls.add(enabled);
    if (fail != null) throw fail!;
    return enabled;
  }
}

class _FixedPath extends HostdExecutablePathNotifier {
  @override
  Future<String?> build() async => null;
}

HostdStatusSnapshot _status({bool remoteControl = false, bool reachable = true}) => HostdStatusSnapshot(
      reachable: reachable,
      authenticated: true,
      backendConnected: true,
      remoteControl: remoteControl,
    );

void main() {
  Future<void> pump(WidgetTester tester, Widget home, HostdStatusSnapshot status, _FakeControlClient client) async {
    tester.view.physicalSize = const Size(1400, 2400);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          hostdStatusProvider.overrideWith((ref) => Stream.value(status)),
          hostdControlClientProvider.overrideWithValue(client),
          hostdExecutablePathProvider.overrideWith(_FixedPath.new),
          hostdSupervisorStateProvider.overrideWith((ref) => Stream.value(HostdSupervisorState.idle)),
        ],
        child: MaterialApp(
          theme: AuraThemeV2.dark,
          localizationsDelegates: AppLocalizations.localizationsDelegates,
          supportedLocales: AppLocalizations.supportedLocales,
          home: home,
        ),
      ),
    );
    await tester.pump();
    await tester.pump();
  }

  Finder toggle() => find.descendant(of: find.byType(HostdRemoteControlToggle), matching: find.byType(Switch));
  bool shown(WidgetTester tester) => tester.widget<Switch>(toggle()).value;

  final surfaces = <String, Widget>{
    'daemon page': const HostDaemonPage(),
    'IDE Host panel': const Scaffold(body: SingleChildScrollView(child: HostPanel())),
  };

  for (final surface in surfaces.entries) {
    group(surface.key, () {
      testWidgets('shows the switch, its explanation, and the state /status reports', (tester) async {
        await pump(tester, surface.value, _status(remoteControl: true), _FakeControlClient());

        expect(find.text(_l10n.hostdRemoteControlTitle), findsOneWidget);
        expect(find.text(_l10n.hostdRemoteControlExplanation), findsOneWidget);
        expect(shown(tester), isTrue);
      });

      testWidgets('reads off for a daemon that reports it off (or too old to report it)', (tester) async {
        await pump(tester, surface.value, _status(), _FakeControlClient());
        expect(shown(tester), isFalse);
      });

      testWidgets('turning it on asks first; Cancel changes nothing', (tester) async {
        final client = _FakeControlClient();
        await pump(tester, surface.value, _status(), client);

        await tester.tap(toggle());
        await tester.pumpAndSettle();

        expect(find.text(_l10n.hostdRemoteControlConfirmTitle), findsOneWidget);
        expect(find.text(_l10n.hostdRemoteControlConfirmBody), findsOneWidget);
        await tester.tap(find.text(_l10n.cancel));
        await tester.pumpAndSettle();

        expect(client.calls, isEmpty);
        expect(shown(tester), isFalse);
      });

      testWidgets('turning it on and allowing calls the daemon', (tester) async {
        final client = _FakeControlClient();
        await pump(tester, surface.value, _status(), client);

        await tester.tap(toggle());
        await tester.pumpAndSettle();
        await tester.tap(find.text(_l10n.hostdRemoteControlConfirmAllow));
        await tester.pumpAndSettle();

        expect(client.calls, [true]);
        expect(shown(tester), isTrue);
      });

      testWidgets('turning it off needs no confirmation', (tester) async {
        final client = _FakeControlClient();
        await pump(tester, surface.value, _status(remoteControl: true), client);

        await tester.tap(toggle());
        await tester.pumpAndSettle();

        expect(find.text(_l10n.hostdRemoteControlConfirmTitle), findsNothing);
        expect(client.calls, [false]);
        expect(shown(tester), isFalse);
      });

      testWidgets('a daemon that refuses leaves the switch where it was and says why', (tester) async {
        final client = _FakeControlClient(fail: HostdControlError(404, 'aura_hostd is out of date'));
        await pump(tester, surface.value, _status(remoteControl: true), client);

        await tester.tap(toggle());
        await tester.pumpAndSettle();

        expect(client.calls, [false]);
        expect(shown(tester), isTrue);
        expect(find.textContaining('out of date'), findsOneWidget);
      });

      testWidgets('is not offered while the daemon is not running', (tester) async {
        await pump(tester, surface.value, _status(reachable: false), _FakeControlClient());
        expect(find.byType(HostdRemoteControlToggle), findsNothing);
      });
    });
  }
}
