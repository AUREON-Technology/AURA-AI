// The Desktop IDE's host-daemon prompts and the welcome screen's layout.
//
// Source Control and the terminal used to ask the user to "grant this workspace" whenever the
// daemon had not granted it -- including while the daemon was not running at all, when no grant
// can succeed and the status bar beside them said "Host Not Running". And the welcome screen was
// a plain Column: with the terminal panel open and three recent folders it overflowed (a debug
// build's "BOTTOM OVERFLOWED BY 69 PIXELS"; a release build just cut the last recent off).
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/hostd/hostd_providers.dart';
import 'package:aura_client/core/security/encryption_service.dart';
import 'package:aura_client/desktop/host/host_access.dart';
import 'package:aura_client/desktop/workspace/workspace_welcome_page.dart';
import 'package:aura_client/l10n/l10n.dart';
import 'package:drift/native.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

Widget _app(Widget child, HostdStatusSnapshot status) => ProviderScope(
      overrides: [hostdStatusProvider.overrideWith((ref) => Stream.value(status))],
      child: MaterialApp(
        theme: AuraThemeV2.dark,
        localizationsDelegates: AppLocalizations.localizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        home: Scaffold(body: child),
      ),
    );

void main() {
  group('hostAccessStepFor', () {
    test('no answer yet, or an unreachable daemon, means start it', () {
      expect(hostAccessStepFor(null), HostAccessStep.start);
      expect(hostAccessStepFor(HostdStatusSnapshot(reachable: false, authenticated: false)), HostAccessStep.start);
    });

    test('a running daemon with no token means pair it', () {
      expect(hostAccessStepFor(HostdStatusSnapshot(reachable: true, authenticated: false)), HostAccessStep.pair);
    });

    test('a running, paired daemon leaves only the grant', () {
      expect(hostAccessStepFor(HostdStatusSnapshot(reachable: true, authenticated: true)), HostAccessStep.grant);
    });
  });

  group('HostAccessNotice', () {
    const grant = 'Grant this workspace to the host daemon to use Source Control.';

    testWidgets('with the daemon down it offers Start, not a grant that cannot succeed', (tester) async {
      await tester.pumpWidget(_app(
        const HostAccessNotice(workspaceRoot: r'E:\VELVET', grantMessage: grant),
        HostdStatusSnapshot(reachable: false, authenticated: false),
      ));
      await tester.pump();

      expect(find.textContaining("isn't running"), findsOneWidget);
      expect(find.text('Start'), findsOneWidget);
      expect(find.text(grant), findsNothing);
    });

    testWidgets('with the daemon running unpaired it offers Pair', (tester) async {
      await tester.pumpWidget(_app(
        const HostAccessNotice(workspaceRoot: r'E:\VELVET', grantMessage: grant),
        HostdStatusSnapshot(reachable: true, authenticated: false),
      ));
      await tester.pump();

      expect(find.textContaining("isn't paired"), findsOneWidget);
      expect(find.text('Pair'), findsOneWidget);
    });

    testWidgets('with the daemon running and paired it asks for the grant', (tester) async {
      await tester.pumpWidget(_app(
        const HostAccessNotice(workspaceRoot: r'E:\VELVET', grantMessage: grant),
        HostdStatusSnapshot(reachable: true, authenticated: true),
      ));
      await tester.pump();

      expect(find.text(grant), findsOneWidget);
      expect(find.text('Grant Access'), findsOneWidget);
      expect(find.text('Start'), findsNothing);
    });
  });

  group('WorkspaceWelcomePage', () {
    setUpAll(EncryptionService.init);

    testWidgets('three recents in a short editor area scroll instead of overflowing', (tester) async {
      // The editor area in the reported screenshot: half the window, under an open terminal.
      tester.view.physicalSize = const Size(1080, 560);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);

      final db = AppDatabase.forTesting(NativeDatabase.memory());
      await tester.runAsync(() async {
        for (final (i, name) in ['VELVET', 'Automated AI Factory', 'AURA AI'].indexed) {
          await db.into(db.desktopWorkspaces).insert(
                DesktopWorkspacesCompanion.insert(
                  id: 'ws$i',
                  rootPath: 'E:\\$name',
                  displayName: name,
                  lastOpenedAt: DateTime(2026, 11, 19, 19, i),
                ),
              );
        }
      });

      await tester.pumpWidget(ProviderScope(
        overrides: [
          databaseProvider.overrideWith((ref) {
            ref.onDispose(db.close);
            return db;
          }),
          hostdStatusProvider.overrideWith(
            (ref) => Stream.value(HostdStatusSnapshot(reachable: false, authenticated: false)),
          ),
        ],
        child: MaterialApp(
          theme: AuraThemeV2.dark,
          localizationsDelegates: AppLocalizations.localizationsDelegates,
          supportedLocales: AppLocalizations.supportedLocales,
          home: const WorkspaceWelcomePage(),
        ),
      ));
      // Let the notifier's initial load from the in-memory database land.
      await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 50)));
      await tester.pump();

      expect(find.text('AURA AI'), findsOneWidget);
      expect(tester.takeException(), isNull, reason: 'a RenderFlex overflow is reported as an exception');

      await tester.scrollUntilVisible(find.text('AURA AI'), 100, scrollable: find.byType(Scrollable).first);
      expect(find.text('AURA AI').hitTestable(), findsOneWidget, reason: 'the last recent can be reached');

      await tester.pumpWidget(const SizedBox.shrink());
      await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 10)));
    });
  });
}
