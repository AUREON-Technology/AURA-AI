/// The Git panel's "Publish branch" (decision D-01), driven through the real widget: the button
/// appears only for a branch with no upstream, nothing is sent until the person confirms, the
/// dialog names the remote and branch, a choice is offered when there are several remotes, and a
/// repository with no remote says so instead of opening an empty choice.
library;

import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/hostd/hostd_control_client.dart';
import 'package:aura_client/core/hostd/hostd_providers.dart' show hostdControlClientProvider;
import 'package:aura_client/core/security/encryption_service.dart';
import 'package:aura_client/desktop/events/ide_event_bus.dart';
import 'package:aura_client/desktop/git/git_panel.dart';
import 'package:aura_client/desktop/state/desktop_application_state.dart';
import 'package:aura_client/desktop/state/state_notifiers.dart';
import 'package:aura_client/l10n/generated/app_localizations.dart';
import 'package:aura_client/l10n/l10n.dart';
import 'package:drift/native.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

const _root = '/tmp/ws-1';

class _FakeHostd extends HostdControlClient {
  String? upstream;
  List<String> remotes = ['origin'];
  final published = <String>[];

  @override
  Future<Map<String, dynamic>> gitStatus({required String root}) async => {
        'branch': 'feature/login',
        'upstream': upstream,
        'ahead': 0,
        'behind': 0,
        'files': <Map<String, dynamic>>[],
      };

  @override
  Future<Map<String, dynamic>> gitBranches({required String root}) async => {'branches': <dynamic>[]};

  @override
  Future<Map<String, dynamic>> gitRemotes({required String root}) async => {'remotes': remotes};

  @override
  Future<Map<String, dynamic>> gitPublish({required String root, required String remote}) async {
    published.add(remote);
    upstream = '$remote/feature/login';
    return {'ok': true, 'branch': 'feature/login', 'remote': remote};
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late AppDatabase db;
  late IdeEventBus bus;
  late _FakeHostd hostd;
  late ProviderContainer container;
  late AppLocalizations l10n;

  setUp(() async {
    await EncryptionService.init();
    db = AppDatabase.forTesting(NativeDatabase.memory());
    bus = IdeEventBus();
    hostd = _FakeHostd();
    setCurrentL10n(null);
    l10n = currentL10n;
  });

  tearDown(() async {
    container.dispose();
    await bus.close();
    await db.close();
  });

  Future<void> pumpPanel(WidgetTester tester) async {
    container = ProviderContainer(overrides: [
      databaseProvider.overrideWithValue(db),
      ideEventBusProvider.overrideWithValue(bus),
      hostdControlClientProvider.overrideWithValue(hostd),
      workspaceNotifierProvider.overrideWith((ref) => WorkspaceNotifier(db, bus)),
    ]);
    final workspaces = container.read(workspaceNotifierProvider.notifier);
    await workspaces.createWorkspace(id: 'ws-1', rootPath: _root, displayName: 'ws-1');
    await workspaces.updateHostdRoots(const [HostdRoot(path: _root, trusted: false)]);

    await tester.pumpWidget(
      UncontrolledProviderScope(
        container: container,
        child: MaterialApp(
          theme: AuraThemeV2.dark,
          localizationsDelegates: AppLocalizations.localizationsDelegates,
          supportedLocales: AppLocalizations.supportedLocales,
          // Wider than the real 280 px sidebar on purpose: the panel's existing sync-button row
          // (three icon buttons plus Merge and Branch) already overflows at the real width, which
          // is a separate finding (04-gap-and-risk-register F10), not what these tests are about.
          home: const Scaffold(body: SizedBox(width: 560, height: 760, child: GitPanel())),
        ),
      ),
    );
    await tester.pumpAndSettle();
  }

  testWidgets('a branch that has an upstream has nothing to publish', (tester) async {
    hostd.upstream = 'origin/feature/login';
    await pumpPanel(tester);

    expect(find.text(l10n.gitPublishBranch), findsNothing);
  });

  testWidgets('a branch with no upstream offers Publish branch, and nothing is sent before confirming',
      (tester) async {
    await pumpPanel(tester);
    expect(find.text(l10n.gitPublishBranch), findsOneWidget);

    await tester.tap(find.text(l10n.gitPublishBranch));
    await tester.pumpAndSettle();

    expect(find.text(l10n.gitPublishConfirm('feature/login', 'origin')), findsOneWidget);
    expect(hostd.published, isEmpty);

    await tester.tap(find.text(l10n.cancel));
    await tester.pumpAndSettle();
    expect(hostd.published, isEmpty, reason: 'cancel must send nothing');
    expect(find.text(l10n.gitPublishBranch), findsOneWidget, reason: 'still not published');
  });

  testWidgets('confirming publishes to that remote, and the button goes away once it has an upstream',
      (tester) async {
    await pumpPanel(tester);

    await tester.tap(find.text(l10n.gitPublishBranch));
    await tester.pumpAndSettle();
    await tester.tap(find.text(l10n.gitPublishAction));
    await tester.pumpAndSettle();

    expect(hostd.published, ['origin']);
    expect(find.text(l10n.gitPublishBranch), findsNothing);
    expect(container.read(gitNotifierProvider).upstream, 'origin/feature/login');
  });

  testWidgets('with several remotes the person picks one, origin first', (tester) async {
    hostd.remotes = ['upstream', 'origin', 'backup'];
    await pumpPanel(tester);

    await tester.tap(find.text(l10n.gitPublishBranch));
    await tester.pumpAndSettle();

    expect(find.text(l10n.gitPublishChooseRemote('feature/login')), findsOneWidget);
    for (final remote in hostd.remotes) {
      expect(find.text(remote), findsOneWidget);
    }
    expect(tester.widget<RadioGroup<String>>(find.byType(RadioGroup<String>)).groupValue, 'origin');

    await tester.tap(find.text('backup'));
    await tester.pumpAndSettle();
    await tester.tap(find.text(l10n.gitPublishAction));
    await tester.pumpAndSettle();

    expect(hostd.published, ['backup']);
  });

  testWidgets('a repository with no remote says so and opens no dialog', (tester) async {
    hostd.remotes = [];
    await pumpPanel(tester);

    await tester.tap(find.text(l10n.gitPublishBranch));
    await tester.pumpAndSettle();

    expect(find.text(l10n.gitNoRemote), findsOneWidget);
    expect(find.text(l10n.gitPublishAction), findsNothing);
    expect(hostd.published, isEmpty);
  });
}
