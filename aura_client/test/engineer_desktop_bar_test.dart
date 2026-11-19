// WP8 (D-10, D-10b, UX-06): on a phone or the web, an Engineer chat shows a compact bar above
// the composer saying what the user's computer allows -- not connected, remote control off, or a
// folder to work in. The chosen folder is what the turn sends as `workspace_root`, and the choice
// is remembered per account. Inside the Desktop IDE there is no bar: the IDE binds the folder.
//
// Driven through the real ChatPage and chat engine, with only the HTTP edges faked. The chat
// stream records the body `chatStreamBody` -- the function the real SSE client serializes --
// builds from what it was asked to send, so `workspace_root` is checked in the wire body.
import 'dart:convert';

import 'package:aura_client/core/ai/engineer/engineer_consent_repository.dart';
import 'package:aura_client/core/ai/engineer/host_folders_client.dart';
import 'package:aura_client/core/ai/online_agents/sse_agent_client.dart';
import 'package:aura_client/core/ai/vision/chat_images.dart';
import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/config/backend_url_provider.dart';
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/hostd/hostd_control_client.dart';
import 'package:aura_client/core/hostd/hostd_providers.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/security/device_validation_service.dart';
import 'package:aura_client/core/security/encryption_service.dart';
import 'package:aura_client/desktop/agent/agent_panel.dart';
import 'package:aura_client/desktop/events/ide_event_bus.dart';
import 'package:aura_client/desktop/state/state_notifiers.dart';
import 'package:aura_client/features/chat/data/agent_config_repository.dart';
import 'package:aura_client/features/chat/data/agents_repository.dart';
import 'package:aura_client/features/chat/data/engineer_remote_folder.dart';
import 'package:aura_client/features/chat/data/turn_meta.dart';
import 'package:aura_client/features/chat/presentation/chat_page.dart';
import 'package:aura_client/features/chat/presentation/widgets/engineer_desktop_bar.dart';
import 'package:aura_client/features/engineering/presentation/git_init_prompt.dart';
import 'package:aura_client/l10n/generated/app_localizations.dart';
import 'package:aura_client/l10n/generated/app_localizations_en.dart';
import 'package:drift/drift.dart' show Value;
import 'package:drift/native.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';

final _l10n = AppLocalizationsEn();

const _agents = [
  AgentInfo(id: 'orchestrator', description: '', version: '1'),
  AgentInfo(id: 'engineer', description: '', version: '1', requiredCapabilities: {'engineer_agent'}),
];

class _StubTokenManager extends TokenManager {
  _StubTokenManager(super.ref);

  @override
  Future<String?> getValidAccessToken() async => 'jwt-test';
}

/// The backend: consent (already given), the folders route, and the chat stream.
class _Backend {
  _Backend(this.folders);

  Map<String, dynamic> folders;
  final log = <String>[];

  /// The computer's answer to a git-init request, when it refuses one.
  String? gitInitRefusal;
  final streamBodies = <Map<String, dynamic>>[];

  late final http.Client client = MockClient.streaming((request, bodyStream) async {
    switch (request.url.path) {
      case '/api/v3/engineering/consent':
        log.add('${request.method} consent');
        return _json({
          'accepted': true,
          'notice_version': kEngineerConsentNoticeVersion,
          'current_version': kEngineerConsentNoticeVersion,
          'accepted_at': '2026-11-17T09:30:00+00:00',
        });
      case '/api/v3/host/folders':
        log.add('GET folders');
        return _json(folders);
      case '/api/v3/host/folders/git-init':
        final path = (jsonDecode(await bodyStream.bytesToString()) as Map)['path'];
        log.add('POST git-init $path');
        final refusal = gitInitRefusal;
        if (refusal != null) {
          return _json({'error': 'desktop_refused', 'message': refusal, 'retryable': false, 'details': {}}, 409);
        }
        folders = {
          ...folders,
          'roots': [
            for (final root in folders['roots'] as List)
              (root as Map)['path'] == path ? {...root, 'is_git': true} : root,
          ],
        };
        return _json({'ok': true, 'commit': 'abc123'});
    }
    return _json({'error': 'not_found'}, 404);
  });

  static http.StreamedResponse _json(Object body, [int status = 200]) => http.StreamedResponse(
        Stream.value(utf8.encode(jsonEncode(body))),
        status,
        headers: {'content-type': 'application/json'},
      );
}

/// The chat stream: records the wire body of every turn and answers at once.
class _RecordingSseClient extends SseAgentClient {
  _RecordingSseClient(super.ref, this.backend);

  final _Backend backend;

  @override
  Stream<SseChatEvent> streamAgentEvents({
    required String message,
    required String sessionId,
    required String agentId,
    bool useKnowledge = true,
    bool useMemory = true,
    bool useTools = true,
    List<String> approvalIds = const [],
    String? correlationId,
    int maxOutputTokens = 1024,
    double temperature = 0.3,
    List<ChatImageAttachment> images = const [],
    String? workspaceRoot,
    bool desktopTaskIsolation = false,
  }) async* {
    backend.log.add('stream');
    final body = chatStreamBody(
      message: message,
      sessionId: sessionId,
      agentId: agentId,
      useKnowledge: useKnowledge,
      useMemory: useMemory,
      useTools: useTools,
      approvalIds: approvalIds,
      maxOutputTokens: maxOutputTokens,
      temperature: temperature,
      images: images,
      workspaceRoot: workspaceRoot,
      desktopTaskIsolation: desktopTaskIsolation,
    );
    backend.streamBodies.add(jsonDecode(jsonEncode(body)) as Map<String, dynamic>);
    yield SseChatEvent.fromWire('token', {'text': 'On it.'});
    yield SseChatEvent.fromWire('done', {});
  }
}

/// The local daemon, for the IDE: `gitStatus` says whether the folder is a repository.
class _FakeHostd extends HostdControlClient {
  _FakeHostd({required this.isGit});

  bool isGit;
  final inits = <String>[];

  @override
  Future<Map<String, dynamic>> gitStatus({required String root}) async {
    if (!isGit) {
      throw HostdControlError(409, '$root is not a git repository', code: 'not_a_git_repository');
    }
    return {'branch': 'main', 'files': []};
  }

  @override
  Future<Map<String, dynamic>> initGit({required String root}) async {
    inits.add(root);
    isGit = true;
    return {'ok': true, 'commit': 'abc123'};
  }
}

Map<String, dynamic> _available([List<String> paths = const ['/home/me/api', '/home/me/web']]) => {
      'connected': true,
      'remote_control': true,
      'roots': [
        for (final path in paths) {'path': path, 'trusted': false},
      ],
    };

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late AppDatabase db;
  late IdeEventBus bus;

  // Device attestation does real I/O that never completes inside a widget test's fake clock.
  // Resolve it once for real; requests then reuse the cache.
  setUpAll(() async {
    final container = ProviderContainer();
    final backendUrl = container.read(backendUrlProvider);
    container.dispose();
    await DeviceValidationService.getAttestationHeaders(backendUrl: backendUrl);
  });

  setUp(() async {
    SharedPreferences.setMockInitialValues({});
    await EncryptionService.init();
    db = AppDatabase.forTesting(NativeDatabase.memory());
    bus = IdeEventBus();
  });

  tearDown(() async {
    await bus.close();
    await db.close();
  });

  ProviderContainer containerFor(_Backend backend, {String account = 'user-1', _FakeHostd? hostd}) {
    final container = ProviderContainer(overrides: [
      if (hostd != null) hostdControlClientProvider.overrideWithValue(hostd),
      databaseProvider.overrideWithValue(db),
      ideEventBusProvider.overrideWithValue(bus),
      workspaceNotifierProvider.overrideWith((ref) => WorkspaceNotifier(db, bus)),
      // The Engineer panel's host banner watches /status, which polls every 3 s; a
      // running, paired daemon keeps the banner hidden and leaves no poll timer behind.
      hostdStatusProvider.overrideWith(
        (ref) => Stream.value(HostdStatusSnapshot(reachable: true, authenticated: true)),
      ),
      agentsProvider.overrideWith((ref) async => _agents),
      tokenManagerProvider.overrideWith((ref) => _StubTokenManager(ref)),
      engineerFolderAccountProvider.overrideWithValue(account),
      engineerConsentRepositoryProvider
          .overrideWith((ref) => EngineerConsentRepository(ref, httpClient: backend.client)),
      hostFoldersClientProvider.overrideWith((ref) => HostFoldersClient(ref, httpClient: backend.client)),
      sseAgentClientProvider.overrideWith((ref) => _RecordingSseClient(ref, backend)),
    ]);
    addTearDown(container.dispose);
    return container;
  }

  Widget app(Widget home) => MaterialApp(
        theme: AuraThemeV2.dark,
        localizationsDelegates: AppLocalizations.localizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        home: Scaffold(body: home),
      );

  Future<void> settle(WidgetTester tester) async {
    for (var i = 0; i < 10; i++) {
      await tester.pump(const Duration(milliseconds: 100));
    }
  }

  Future<ProviderContainer> pumpChat(
    WidgetTester tester,
    _Backend backend, {
    String agentId = 'engineer',
    String account = 'user-1',
  }) async {
    tester.view.physicalSize = const Size(500, 1000);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);
    final container = containerFor(backend, account: account);
    container.read(activeAgentIdProvider.notifier).setAgent(agentId);
    await tester.pumpWidget(UncontrolledProviderScope(container: container, child: app(const ChatPage())));
    await settle(tester);
    return container;
  }

  /// Unmount inside the test so Drift's stream-close timer and the sync engine's periodic timer
  /// stop before the binding checks for pending timers.
  Future<void> unmount(WidgetTester tester, ProviderContainer container) async {
    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pump(const Duration(milliseconds: 1));
    container.dispose();
    await tester.pump(const Duration(milliseconds: 1));
  }

  Future<void> send(WidgetTester tester, String text) async {
    await tester.enterText(find.byType(TextField), text);
    await tester.tap(find.byTooltip('Send'));
    await settle(tester);
  }

  Future<void> choose(WidgetTester tester, String path) async {
    // Pumped, not settled: a reply already in the list keeps animating.
    await tester.tap(find.byType(DropdownButton<String>));
    await settle(tester);
    await tester.tap(find.text(path).last);
    await settle(tester);
  }

  group('status', () {
    testWidgets('no computer online', (tester) async {
      final backend = _Backend({'connected': false, 'remote_control': null, 'roots': []});
      final container = await pumpChat(tester, backend);

      expect(find.byType(EngineerDesktopBar), findsOneWidget);
      expect(find.text(_l10n.engineerDesktopNotConnected), findsOneWidget);
      expect(find.byType(DropdownButton<String>), findsNothing);
      await unmount(tester, container);
    });

    testWidgets('remote control off on the computer: no folders offered', (tester) async {
      final backend = _Backend({
        'connected': true,
        'remote_control': false,
        'roots': [
          {'path': '/home/me/api', 'trusted': true},
        ],
      });
      final container = await pumpChat(tester, backend);

      expect(find.text(_l10n.engineerDesktopRemoteOff), findsOneWidget);
      expect(find.byType(DropdownButton<String>), findsNothing);
      expect(find.text('/home/me/api'), findsNothing);
      await unmount(tester, container);
    });

    testWidgets('remote control on but nothing granted', (tester) async {
      final backend = _Backend(_available(const []));
      final container = await pumpChat(tester, backend);

      expect(find.text(_l10n.engineerDesktopNoFolders), findsOneWidget);
      expect(find.byType(DropdownButton<String>), findsNothing);
      await unmount(tester, container);
    });

    testWidgets('a failed check says so', (tester) async {
      final backend = _Backend({'connected': 'yes'}); // not the documented shape
      final container = await pumpChat(tester, backend);

      expect(find.text(_l10n.engineerDesktopCheckFailed), findsOneWidget);
      await unmount(tester, container);
    });

    testWidgets('"Check again" refetches: turning remote control on shows the folders', (tester) async {
      final backend = _Backend({'connected': true, 'remote_control': false, 'roots': []});
      final container = await pumpChat(tester, backend);
      expect(find.text(_l10n.engineerDesktopRemoteOff), findsOneWidget);

      backend.folders = _available();
      await tester.tap(find.byTooltip(_l10n.engineerDesktopRefresh));
      await settle(tester);

      expect(find.text(_l10n.engineerDesktopRemoteOff), findsNothing);
      expect(find.byType(DropdownButton<String>), findsOneWidget);
      expect(backend.log.where((e) => e == 'GET folders'), hasLength(2));
      await unmount(tester, container);
    });

    testWidgets('another persona shows no bar and asks nothing about the computer', (tester) async {
      final backend = _Backend(_available());
      final container = await pumpChat(tester, backend, agentId: 'orchestrator');

      expect(find.byType(EngineerDesktopBar), findsNothing);
      expect(backend.log, isNot(contains('GET folders')));
      await unmount(tester, container);
    });
  });

  group('workspace_root', () {
    testWidgets('the folder shown is the one sent, and a chosen folder replaces it', (tester) async {
      final backend = _Backend(_available());
      final container = await pumpChat(tester, backend);

      expect(find.text('/home/me/api'), findsOneWidget, reason: 'the first folder until one is chosen');
      await send(tester, 'run the tests');
      expect(backend.streamBodies.last['workspace_root'], '/home/me/api');
      // WP4: work on the computer happens in the conversation's own worktree.
      expect(backend.streamBodies.last['desktop_task_isolation'], isTrue);

      await choose(tester, '/home/me/web');
      await send(tester, 'and lint');
      expect(backend.streamBodies.last['workspace_root'], '/home/me/web');
      await unmount(tester, container);
    });

    testWidgets('no folder to pick: the turn carries no workspace_root', (tester) async {
      final backend = _Backend({'connected': false, 'remote_control': null, 'roots': []});
      final container = await pumpChat(tester, backend);

      await send(tester, 'what is in the repo?');

      expect(backend.streamBodies.single.containsKey('workspace_root'), isFalse);
      expect(backend.streamBodies.single.containsKey('desktop_task_isolation'), isFalse);
      await unmount(tester, container);
    });

    testWidgets('the choice is remembered for the account, and only for it', (tester) async {
      final backend = _Backend(_available());
      var container = await pumpChat(tester, backend);
      await choose(tester, '/home/me/web');
      await unmount(tester, container);

      container = await pumpChat(tester, backend);
      expect(find.text('/home/me/web'), findsOneWidget, reason: 'same account: remembered');
      await send(tester, 'go');
      expect(backend.streamBodies.last['workspace_root'], '/home/me/web');
      await unmount(tester, container);

      container = await pumpChat(tester, backend, account: 'user-2');
      expect(find.text('/home/me/api'), findsOneWidget, reason: 'another account starts fresh');
      await unmount(tester, container);
    });

    testWidgets('a remembered folder the computer no longer grants falls back to the first one',
        (tester) async {
      SharedPreferences.setMockInitialValues({'engineer_remote_folder.user-1': '/home/me/gone'});
      final backend = _Backend(_available());
      final container = await pumpChat(tester, backend);

      await send(tester, 'go');

      expect(backend.streamBodies.last['workspace_root'], '/home/me/api');
      await unmount(tester, container);
    });
  });

  testWidgets('inside the Desktop IDE there is no bar: the IDE binds the folder', (tester) async {
    tester.view.physicalSize = const Size(900, 1400);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);
    final backend = _Backend(_available());
    final container = containerFor(backend);
    await tester.pumpWidget(UncontrolledProviderScope(container: container, child: app(const AgentPanel())));
    await settle(tester);

    expect(find.byType(EngineerDesktopBar), findsNothing);
    expect(find.byType(DropdownButton<String>), findsNothing);

    await send(tester, 'fix the build');
    expect(backend.log, isNot(contains('GET folders')));
    expect(backend.streamBodies.single.containsKey('workspace_root'), isFalse,
        reason: 'no IDE workspace open, and no remote folder substituted for it');
    // WP4: every Engineer turn from the IDE panel asks for its own worktree.
    expect(backend.streamBodies.single['desktop_task_isolation'], isTrue);
    await unmount(tester, container);
  });

  // WP4 (D-13): isolation needs git, so a folder that is not a repository offers to become one.
  group('initialize git', () {
    Map<String, dynamic> notGit() => {
          'connected': true,
          'remote_control': true,
          'roots': [
            {'path': '/home/me/notes', 'trusted': false, 'is_git': false},
            {'path': '/home/me/api', 'trusted': false, 'is_git': true},
          ],
        };

    testWidgets('phone: a chosen folder that is not a repository explains and offers to initialize it',
        (tester) async {
      final backend = _Backend(notGit());
      final container = await pumpChat(tester, backend);

      expect(find.text(_l10n.engineerDesktopNotGit), findsOneWidget);
      await tester.tap(find.text(_l10n.gitInitButtonShort));
      await settle(tester);
      expect(find.text(_l10n.gitInitConfirmTitle), findsOneWidget);
      expect(find.text(_l10n.gitInitConfirmBody('/home/me/notes')), findsOneWidget);

      await tester.tap(find.text(_l10n.gitInitConfirmAction));
      await settle(tester);

      expect(backend.log, contains('POST git-init /home/me/notes'));
      expect(backend.log.where((e) => e == 'GET folders'), hasLength(2), reason: 'refreshed after');
      expect(find.text(_l10n.engineerDesktopNotGit), findsNothing);
      expect(find.text(_l10n.gitInitDone), findsOneWidget);
      await unmount(tester, container);
    });

    testWidgets('phone: Cancel sends nothing', (tester) async {
      final backend = _Backend(notGit());
      final container = await pumpChat(tester, backend);

      await tester.tap(find.text(_l10n.gitInitButtonShort));
      await settle(tester);
      await tester.tap(find.text(_l10n.cancel));
      await settle(tester);

      expect(backend.log.where((e) => e.startsWith('POST git-init')), isEmpty);
      expect(find.text(_l10n.engineerDesktopNotGit), findsOneWidget);
      await unmount(tester, container);
    });

    testWidgets("phone: the computer's refusal is shown in its own words", (tester) async {
      final backend = _Backend(notGit())..gitInitRefusal = 'remote control is off on this computer';
      final container = await pumpChat(tester, backend);

      await tester.tap(find.text(_l10n.gitInitButtonShort));
      await settle(tester);
      await tester.tap(find.text(_l10n.gitInitConfirmAction));
      await settle(tester);

      expect(find.text(_l10n.gitInitFailed('remote control is off on this computer')), findsOneWidget);
      expect(find.text(_l10n.engineerDesktopNotGit), findsOneWidget);
      await unmount(tester, container);
    });

    testWidgets('phone: a repository, or an older daemon that does not say, offers nothing', (tester) async {
      final backend = _Backend({
        'connected': true,
        'remote_control': true,
        'roots': [
          {'path': '/home/me/old', 'trusted': false},
        ],
      });
      final container = await pumpChat(tester, backend);

      expect(find.text(_l10n.engineerDesktopNotGit), findsNothing);
      expect(find.text(_l10n.gitInitButtonShort), findsNothing);
      await unmount(tester, container);
    });

    Future<ProviderContainer> pumpIde(WidgetTester tester, _FakeHostd hostd) async {
      tester.view.physicalSize = const Size(900, 1400);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);
      final container = containerFor(_Backend(_available()), hostd: hostd);
      await container
          .read(workspaceNotifierProvider.notifier)
          .createWorkspace(id: 'w1', rootPath: '/home/me/notes', displayName: 'notes');
      await tester.pumpWidget(UncontrolledProviderScope(container: container, child: app(const AgentPanel())));
      await settle(tester);
      return container;
    }

    testWidgets('IDE: a bound folder that is not a repository shows why, and initializes on confirm',
        (tester) async {
      final hostd = _FakeHostd(isGit: false);
      final container = await pumpIde(tester, hostd);

      expect(find.byType(WorkspaceGitInitBanner), findsOneWidget);
      expect(find.text(_l10n.gitInitBannerTitle), findsOneWidget);
      expect(find.text(_l10n.gitInitBannerBody), findsOneWidget);

      await tester.tap(find.text(_l10n.gitInitButton));
      await settle(tester);
      expect(find.text(_l10n.gitInitConfirmBody('/home/me/notes')), findsOneWidget);
      await tester.tap(find.text(_l10n.gitInitConfirmAction));
      await settle(tester);

      expect(hostd.inits, ['/home/me/notes']);
      expect(find.text(_l10n.gitInitBannerTitle), findsNothing, reason: 'refreshed: now a repository');
      await unmount(tester, container);
    });

    testWidgets('IDE: a repository shows no banner', (tester) async {
      final container = await pumpIde(tester, _FakeHostd(isGit: true));

      expect(find.text(_l10n.gitInitBannerTitle), findsNothing);
      await unmount(tester, container);
    });
  });

  // G-14a: the chat screen brings back a confirmation still waiting from before a restart.
  testWidgets('opening the chat after a restart shows a held confirmation again', (tester) async {
    await tester.runAsync(() async {
      await db.into(db.messages).insert(MessagesCompanion.insert(
            id: 'u1',
            conversationId: 'default_session',
            role: 'user',
            content: 'write main.py',
            createdAt: DateTime.now().subtract(const Duration(minutes: 5, seconds: 1)),
          ));
      await db.into(db.messages).insert(MessagesCompanion.insert(
            id: 'a1',
            conversationId: 'default_session',
            role: 'assistant',
            content: '',
            agentId: const Value('engineer'),
            createdAt: DateTime.now().subtract(const Duration(minutes: 5)),
            turnMetaJson: Value(const TurnMeta(
              turnId: 'turn-1',
              needsConfirmation: true,
              pendingToolCalls: [
                PendingToolCall(tool: 'run_command', arguments: {'command': 'pytest'}, approvalId: 'apr.1'),
              ],
            ).encode()),
          ));
    });
    final container = await pumpChat(tester, _Backend(_available()), agentId: 'orchestrator');

    expect(find.text(_l10n.chatConfirmRunCommand), findsOneWidget);
    expect(find.text(_l10n.chatAwaitingConfirmation), findsOneWidget);
    await unmount(tester, container);
  });
}
