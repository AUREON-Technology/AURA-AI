// WP1 (D-03, UX-01): before the first Engineer turn the user sees the Anthropic notice, and
// nothing is sent until they agree. Driven through the desktop Engineer panel and the real chat
// engine, with only the HTTP edges (consent route, chat stream) faked.
import 'dart:convert';

import 'package:aura_client/core/hostd/hostd_providers.dart';
import 'package:aura_client/core/ai/engineer/engineer_consent_repository.dart';
import 'package:aura_client/core/ai/engineer/host_folders_client.dart';
import 'package:aura_client/core/ai/online_agents/sse_agent_client.dart';
import 'package:aura_client/core/ai/vision/chat_images.dart';
import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/config/backend_url_provider.dart';
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/security/device_validation_service.dart';
import 'package:aura_client/core/security/encryption_service.dart';
import 'package:aura_client/desktop/agent/agent_panel.dart';
import 'package:aura_client/desktop/events/ide_event_bus.dart';
import 'package:aura_client/desktop/state/state_notifiers.dart';
import 'package:aura_client/features/chat/data/agent_config_repository.dart';
import 'package:aura_client/features/chat/data/agents_repository.dart';
import 'package:aura_client/features/chat/data/engineer_remote_folder.dart';
import 'package:aura_client/features/chat/presentation/chat_page.dart';
import 'package:aura_client/features/engineering/presentation/engineer_consent_tile.dart';
import 'package:aura_client/l10n/generated/app_localizations.dart';
import 'package:aura_client/l10n/generated/app_localizations_en.dart';
import 'package:drift/native.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_riverpod/misc.dart' show Override;
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:intl/intl.dart';

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

/// The server side of the consent route, and a log of everything that reached "the network".
class _Server {
  _Server({this.accepted = false});

  bool accepted;
  String currentVersion = kEngineerConsentNoticeVersion;
  int postStatus = 200;
  final log = <String>[];

  Map<String, dynamic> get _body => {
        'accepted': accepted,
        'notice_version': accepted ? currentVersion : null,
        'current_version': currentVersion,
        'accepted_at': accepted ? '2026-11-17T09:30:00+00:00' : null,
      };

  late final http.Client client = MockClient((request) async {
    switch (request.method) {
      case 'GET':
        log.add('GET consent');
      case 'POST':
        final version = (jsonDecode(request.body) as Map)['notice_version'];
        log.add('POST consent $version');
        if (postStatus == 409) {
          return http.Response(
            jsonEncode({
              'error': 'consent_version_mismatch',
              'message': 'not current',
              'retryable': false,
              'details': {'current_version': '2027-01-01'},
            }),
            409,
          );
        }
        accepted = true;
      case 'DELETE':
        log.add('DELETE consent');
        accepted = false;
    }
    return http.Response(jsonEncode(_body), 200);
  });

  /// What the chat stream answers, one script per send; an empty queue answers normally.
  final streams = <List<SseChatEvent>>[];

  /// Runs when a turn reaches the chat stream (e.g. consent withdrawn on another device).
  void Function()? onSend;
}

class _ScriptedSseClient extends SseAgentClient {
  _ScriptedSseClient(super.ref, this.server);

  final _Server server;

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
    server.log.add('send $agentId: $message');
    server.onSend?.call();
    server.onSend = null;
    final script = server.streams.isEmpty
        ? [
            SseChatEvent.fromWire('token', {'text': 'On it.'}),
            SseChatEvent.fromWire('done', {}),
          ]
        : server.streams.removeAt(0);
    for (final event in script) {
      yield event;
    }
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late AppDatabase db;
  late IdeEventBus bus;

  // Device attestation does real I/O (a nonce request with a 5 s deadline) that never completes
  // inside a widget test's fake clock. Resolve it once for real; requests then reuse the cache.
  setUpAll(() async {
    final container = ProviderContainer();
    final backendUrl = container.read(backendUrlProvider);
    container.dispose();
    await DeviceValidationService.getAttestationHeaders(backendUrl: backendUrl);
  });

  setUp(() async {
    await EncryptionService.init();
    db = AppDatabase.forTesting(NativeDatabase.memory());
    bus = IdeEventBus();
  });

  tearDown(() async {
    await bus.close();
    await db.close();
  });

  List<Override> overrides(_Server server) => [
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
        engineerConsentRepositoryProvider
            .overrideWith((ref) => EngineerConsentRepository(ref, httpClient: server.client)),
        sseAgentClientProvider.overrideWith((ref) => _ScriptedSseClient(ref, server)),
        // The Engineer chat's desktop bar (WP8) asks about the user's computer; none is online.
        hostFoldersProvider.overrideWith((ref) async => const HostFolders(connected: false)),
        engineerFolderAccountProvider.overrideWithValue('user-1'),
      ];

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

  Future<ProviderContainer> pumpPanel(WidgetTester tester, _Server server) async {
    tester.view.physicalSize = const Size(900, 1400);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);
    final container = ProviderContainer(overrides: overrides(server));
    addTearDown(container.dispose);
    await tester.pumpWidget(
      UncontrolledProviderScope(container: container, child: app(const AgentPanel())),
    );
    await settle(tester);
    return container;
  }

  Future<void> typeAndSend(WidgetTester tester, String text) async {
    await tester.enterText(find.byType(TextField), text);
    await tester.tap(find.byTooltip('Send'));
    await settle(tester);
  }

  /// Unmount inside the test so Drift's stream-close timer fires before the binding checks for
  /// pending timers.
  Future<void> unmount(WidgetTester tester) async {
    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pump(const Duration(milliseconds: 1));
    await tester.pump(const Duration(milliseconds: 1));
  }

  Finder textField() => find.byType(TextField);
  String composerText(WidgetTester tester) =>
      tester.widget<TextField>(textField()).controller!.text;

  group('before the first Engineer send', () {
    testWidgets('consent already current: no sheet, the message goes straight out',
        (tester) async {
      final server = _Server(accepted: true);
      await pumpPanel(tester, server);

      await typeAndSend(tester, 'fix the build');

      expect(find.text(_l10n.engineerConsentTitle), findsNothing);
      expect(server.log, ['GET consent', 'send engineer: fix the build']);
      await unmount(tester);
    });

    testWidgets('no consent: the notice is shown and nothing is sent yet', (tester) async {
      final server = _Server();
      await pumpPanel(tester, server);

      await typeAndSend(tester, 'fix the build');

      expect(find.text(_l10n.engineerConsentTitle), findsOneWidget);
      expect(find.text(_l10n.engineerConsentBodyWhat), findsOneWidget);
      expect(find.text(_l10n.engineerConsentBodyIncludes), findsOneWidget);
      expect(find.text(_l10n.engineerConsentBodyChoice), findsOneWidget);
      expect(server.log, ['GET consent']);
      await unmount(tester);
    });

    testWidgets('"Not now" blocks the send and keeps the message in the composer',
        (tester) async {
      final server = _Server();
      await pumpPanel(tester, server);
      await typeAndSend(tester, 'fix the build');

      await tester.tap(find.text(_l10n.engineerConsentNotNow));
      await settle(tester);

      expect(find.text(_l10n.engineerConsentTitle), findsNothing);
      expect(server.log, ['GET consent'], reason: 'no POST, no send');
      expect(composerText(tester), 'fix the build');
      await unmount(tester);
    });

    testWidgets('"Agree and continue" records consent first, then sends', (tester) async {
      final server = _Server();
      await pumpPanel(tester, server);
      await typeAndSend(tester, 'fix the build');

      await tester.tap(find.text(_l10n.engineerConsentAgree));
      await settle(tester);

      expect(find.text(_l10n.engineerConsentTitle), findsNothing);
      expect(server.log, [
        'GET consent',
        'POST consent 2026-11-17',
        'send engineer: fix the build',
      ]);
      expect(composerText(tester), isEmpty);

      // Asked once: the next send goes straight out.
      await typeAndSend(tester, 'and run the tests');
      expect(find.text(_l10n.engineerConsentTitle), findsNothing);
      expect(server.log.last, 'send engineer: and run the tests');
      await unmount(tester);
    });

    testWidgets('a server requiring a newer notice says "update the app" and sends nothing',
        (tester) async {
      final server = _Server()..currentVersion = '2027-01-01';
      await pumpPanel(tester, server);
      await typeAndSend(tester, 'fix the build');

      expect(find.text(_l10n.engineerConsentUpdateTitle), findsOneWidget);
      expect(find.text(_l10n.engineerConsentTitle), findsNothing, reason: 'not the old words');
      expect(find.text(_l10n.engineerConsentAgree), findsNothing);

      await tester.tap(find.text(_l10n.engineerConsentUpdateOk));
      await settle(tester);
      expect(server.log, ['GET consent']);
      await unmount(tester);
    });

    testWidgets('a 409 on agree turns the sheet into "update the app" and sends nothing',
        (tester) async {
      final server = _Server()..postStatus = 409;
      await pumpPanel(tester, server);
      await typeAndSend(tester, 'fix the build');

      await tester.tap(find.text(_l10n.engineerConsentAgree));
      await settle(tester);

      expect(find.text(_l10n.engineerConsentUpdateTitle), findsOneWidget);
      expect(server.log, ['GET consent', 'POST consent 2026-11-17']);
      await tester.tap(find.text(_l10n.engineerConsentUpdateOk));
      await settle(tester);
      expect(server.log.where((e) => e.startsWith('send')), isEmpty);
      await unmount(tester);
    });
  });

  group('Copilot chat send path', () {
    Future<ProviderContainer> pumpChat(WidgetTester tester, _Server server, String agentId) async {
      tester.view.physicalSize = const Size(500, 1000);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);
      final container = ProviderContainer(overrides: overrides(server));
      addTearDown(container.dispose);
      container.read(activeAgentIdProvider.notifier).setAgent(agentId);
      await tester.pumpWidget(
        UncontrolledProviderScope(container: container, child: app(const ChatPage())),
      );
      await settle(tester);
      return container;
    }

    /// The chat page starts the sync engine's periodic timer; it stops with the container.
    Future<void> unmountChat(WidgetTester tester, ProviderContainer container) async {
      await unmount(tester);
      container.dispose();
      await tester.pump(const Duration(milliseconds: 1));
    }

    testWidgets('with Engineer active, the notice comes first and "Not now" sends nothing',
        (tester) async {
      final server = _Server();
      final container = await pumpChat(tester, server, 'engineer');

      await typeAndSend(tester, 'fix the build');
      expect(find.text(_l10n.engineerConsentTitle), findsOneWidget);

      await tester.tap(find.text(_l10n.engineerConsentNotNow));
      await settle(tester);
      expect(server.log, ['GET consent']);
      expect(composerText(tester), 'fix the build');
      await unmountChat(tester, container);
    });

    testWidgets('with Engineer active, agreeing records consent and then sends', (tester) async {
      final server = _Server();
      final container = await pumpChat(tester, server, 'engineer');

      await typeAndSend(tester, 'fix the build');
      await tester.tap(find.text(_l10n.engineerConsentAgree));
      await settle(tester);

      expect(server.log, [
        'GET consent',
        'POST consent 2026-11-17',
        'send engineer: fix the build',
      ]);
      await unmountChat(tester, container);
    });

    testWidgets('any other persona sends without asking or reading consent', (tester) async {
      final server = _Server();
      final container = await pumpChat(tester, server, 'orchestrator');

      await typeAndSend(tester, 'what is on today');

      expect(find.text(_l10n.engineerConsentTitle), findsNothing);
      expect(server.log, ['send orchestrator: what is on today']);
      await unmountChat(tester, container);
    });
  });

  group('refusals during a turn', () {
    testWidgets('consent_required shows the sheet, not Retry; agreeing resends the turn',
        (tester) async {
      // The app thinks consent is current (it was withdrawn on another device).
      final server = _Server(accepted: true);
      server.streams.add([
        SseChatEvent.fromWire('error', {
          'type': 'error',
          'error': 'consent_required',
          'message': 'consent required',
          'retryable': false,
          'details': {'current_version': '2026-11-17'},
        }),
      ]);
      // Withdrawn on another device after this app last read it.
      server.onSend = () => server.accepted = false;
      await pumpPanel(tester, server);

      await typeAndSend(tester, 'fix the build');

      expect(find.text(_l10n.engineerConsentTitle), findsOneWidget);
      expect(find.text(_l10n.retry), findsNothing);
      expect(find.text(_l10n.engineerConsentNeededNotice), findsOneWidget);

      await tester.tap(find.text(_l10n.engineerConsentAgree));
      await settle(tester);

      expect(find.text(_l10n.engineerConsentTitle), findsNothing);
      expect(server.log, [
        'GET consent',
        'send engineer: fix the build',
        'GET consent', // the stale state is dropped and re-read
        'POST consent 2026-11-17',
        'send engineer: fix the build',
      ]);
      expect(find.text(_l10n.engineerConsentNeededNotice), findsNothing,
          reason: 'the refused turn is replaced by its resend');
      expect(find.text('fix the build'), findsOneWidget);
      await unmount(tester);
    });

    testWidgets('consent_required then "Not now" leaves the notice and resends nothing',
        (tester) async {
      final server = _Server(accepted: true);
      server.streams.add([
        SseChatEvent.fromWire('error', {
          'error': 'consent_required',
          'message': 'consent required',
          'retryable': false,
          'details': {'current_version': '2026-11-17'},
        }),
      ]);
      await pumpPanel(tester, server);
      await typeAndSend(tester, 'fix the build');

      await tester.tap(find.text(_l10n.engineerConsentNotNow));
      await settle(tester);

      expect(server.log.where((e) => e.startsWith('send')), hasLength(1));
      expect(find.text(_l10n.engineerConsentNeededNotice), findsOneWidget);
      expect(find.text(_l10n.retry), findsNothing);
      await unmount(tester);
    });

    testWidgets('user_quota_exhausted shows the limit and its local reset time, no Retry',
        (tester) async {
      const resetAt = '2026-11-18T00:00:00+00:00';
      final server = _Server(accepted: true);
      server.streams.add([
        SseChatEvent.fromWire('error', {
          'error': 'user_quota_exhausted',
          'message': 'daily limit reached',
          'retryable': false,
          'details': {'reset_at': resetAt, 'limit_tokens': 200000},
        }),
      ]);
      await pumpPanel(tester, server);
      await typeAndSend(tester, 'fix the build');

      final localTime = DateFormat.jm().format(DateTime.parse(resetAt).toLocal());
      expect(
        find.textContaining("You’ve reached today’s Engineer limit. It resets at "),
        findsOneWidget,
      );
      expect(find.textContaining(localTime), findsOneWidget);
      expect(find.text(_l10n.retry), findsNothing);
      expect(find.text(_l10n.engineerConsentTitle), findsNothing);
      await unmount(tester);
    });
  });

  group('You → Engineer', () {
    Future<void> pumpTile(WidgetTester tester, _Server server) async {
      await tester.pumpWidget(
        ProviderScope(overrides: overrides(server), child: app(const EngineerConsentTile())),
      );
      await settle(tester);
    }

    testWidgets('shows consent as on since its date, and Withdraw DELETEs after confirming',
        (tester) async {
      final server = _Server(accepted: true);
      await pumpTile(tester, server);

      final date = DateFormat.yMMMd().format(DateTime.utc(2026, 11, 17, 9, 30).toLocal());
      expect(find.text(_l10n.engineerConsentStatusOn(date)), findsOneWidget);

      await tester.tap(find.text(_l10n.engineerConsentWithdraw));
      await settle(tester);
      expect(find.text(_l10n.engineerConsentWithdrawTitle), findsOneWidget);
      expect(server.log, ['GET consent'], reason: 'nothing changes before confirming');

      // The dialog's confirm button (the tile's own button is behind the barrier).
      await tester.tap(find.descendant(
        of: find.byType(Dialog),
        matching: find.text(_l10n.engineerConsentWithdraw),
      ));
      await settle(tester);

      expect(server.log, ['GET consent', 'DELETE consent']);
      expect(find.text(_l10n.engineerConsentStatusOff), findsOneWidget);
      expect(find.text(_l10n.engineerConsentWithdrawn), findsOneWidget);
    });

    testWidgets('cancelling the confirmation withdraws nothing', (tester) async {
      final server = _Server(accepted: true);
      await pumpTile(tester, server);

      await tester.tap(find.text(_l10n.engineerConsentWithdraw));
      await settle(tester);
      await tester.tap(find.text(_l10n.cancel));
      await settle(tester);

      expect(server.log, ['GET consent']);
    });

    testWidgets('shows off when there is no consent', (tester) async {
      final server = _Server();
      await pumpTile(tester, server);

      expect(find.text(_l10n.engineerConsentStatusOff), findsOneWidget);
      expect(find.text(_l10n.engineerConsentWithdraw), findsNothing);
    });
  });
}
