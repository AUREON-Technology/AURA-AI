// Module readiness UX-04 / D-06 (2026-11-17): a trusted folder is a real shell. Its commands run
// as the person and can reach anything their account can; only paths outside the folder (and any
// `git push`) stop to ask. The label used to promise the opposite -- "still confined to this
// folder" -- which is what someone deciding whether to trust a folder reads. The untrusted label
// said non-allowlisted commands simply don't run; they are queued for approval instead.

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/hostd/hostd_providers.dart';
import 'package:aura_client/features/engineering/presentation/host_daemon_page.dart';
import 'package:aura_client/l10n/generated/app_localizations.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

class _FixedPath extends HostdExecutablePathNotifier {
  @override
  Future<String?> build() async => null;
}

void main() {
  Future<void> pumpPage(WidgetTester tester) async {
    tester.view.physicalSize = const Size(1400, 2400);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);

    final snapshot = HostdStatusSnapshot(
      reachable: true,
      authenticated: true,
      backendConnected: true,
      roots: [
        HostdRoot(path: '/home/dev/trusted-project', trusted: true),
        HostdRoot(path: '/home/dev/other-project', trusted: false),
      ],
    );
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          hostdStatusProvider.overrideWith((ref) => Stream.value(snapshot)),
          hostdExecutablePathProvider.overrideWith(_FixedPath.new),
        ],
        child: MaterialApp(
          theme: AuraThemeV2.dark,
          localizationsDelegates: AppLocalizations.localizationsDelegates,
          supportedLocales: AppLocalizations.supportedLocales,
          home: const HostDaemonPage(),
        ),
      ),
    );
    await tester.pump();
    await tester.pump();
  }

  testWidgets('the trusted label does not promise confinement', (tester) async {
    await pumpPage(tester);

    expect(find.textContaining('confined'), findsNothing);
    expect(find.textContaining('anything your account can'), findsOneWidget);
    expect(find.textContaining('outside this folder'), findsOneWidget);
  });

  testWidgets('the untrusted label says other commands ask first', (tester) async {
    await pumpPage(tester);

    expect(find.textContaining('only a read-only inspection allowlist runs'), findsNothing);
    expect(find.textContaining('anything else asks you first'), findsOneWidget);
  });
}
