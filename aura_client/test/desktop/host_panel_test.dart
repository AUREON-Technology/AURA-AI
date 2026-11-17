// The desktop sidebar's Host Daemon panel, driven by a fixed /status snapshot instead of a real
// daemon. Pins audit INT-003's UI half: a daemon whose token the backend refused (close 4401)
// must read "Re-pair required" and offer pairing again -- it used to read "Running,
// reconnecting..." forever with the Pair button hidden, because `authenticated` (a token is
// stored) stayed true.
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/hostd/hostd_providers.dart';
import 'package:aura_client/core/hostd/hostd_supervisor_state.dart';
import 'package:aura_client/desktop/host/host_panel.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

Future<void> _pump(WidgetTester tester, HostdStatusSnapshot status) async {
  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        hostdStatusProvider.overrideWith((ref) => Stream.value(status)),
        hostdSupervisorStateProvider.overrideWith((ref) => Stream.value(HostdSupervisorState.idle)),
      ],
      child: MaterialApp(
        theme: AuraThemeV2.dark,
        home: const Scaffold(body: SingleChildScrollView(child: HostPanel())),
      ),
    ),
  );
  await tester.pump();
  await tester.pump();
}

void main() {
  testWidgets('a refused token shows "Re-pair required" and a Re-pair button (INT-003)', (tester) async {
    await _pump(
      tester,
      HostdStatusSnapshot(
        reachable: true,
        authenticated: true,
        backendConnected: false,
        credentialsRejected: true,
        lastCloseCode: 4401,
      ),
    );

    expect(find.text('Re-pair required'), findsOneWidget);
    expect(find.text('Running, reconnecting...'), findsNothing);
    expect(find.text('Re-pair'), findsOneWidget);
  });

  testWidgets('a paired daemon in reconnect backoff still reads "reconnecting" with no Pair button',
      (tester) async {
    await _pump(
      tester,
      HostdStatusSnapshot(reachable: true, authenticated: true, backendConnected: false),
    );

    expect(find.text('Running, reconnecting...'), findsOneWidget);
    expect(find.text('Pair'), findsNothing);
    expect(find.text('Re-pair'), findsNothing);
  });

  testWidgets('an unpaired daemon offers Pair', (tester) async {
    await _pump(tester, HostdStatusSnapshot(reachable: true, backendConnected: false));

    expect(find.text('Running, not paired'), findsOneWidget);
    expect(find.text('Pair'), findsOneWidget);
  });
}
