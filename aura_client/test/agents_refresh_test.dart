// WP9 (G-15): the persona list used to be fetched once per app run, so a capability granted
// while AURA was open (Engineer for a new Developer, say) stayed locked until a restart. It is
// refetched when the app comes back to the foreground, and the picker's error view has Retry.
import 'dart:convert';

import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/config/backend_url_provider.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/network/aura_api_client.dart';
import 'package:aura_client/core/network/aura_api_error.dart';
import 'package:aura_client/core/security/device_validation_service.dart';
import 'package:aura_client/features/chat/data/agents_repository.dart';
import 'package:aura_client/features/chat/presentation/widgets/persona_picker.dart';
import 'package:aura_client/l10n/generated/app_localizations.dart';
import 'package:aura_client/l10n/generated/app_localizations_en.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

final _l10n = AppLocalizationsEn();

class _StubTokenManager extends TokenManager {
  _StubTokenManager(super.ref);

  @override
  Future<String?> getValidAccessToken() async => 'jwt-test';
}

/// `/api/v3/agents`, answering from a queue (the last answer repeats).
class _Agents {
  _Agents(this.answers);

  final List<http.Response Function()> answers;
  int calls = 0;

  late final http.Client client = MockClient((request) async {
    final answer = answers[calls < answers.length ? calls : answers.length - 1];
    calls++;
    return answer();
  });
}

http.Response _list({required bool engineerUsable}) => http.Response(
      jsonEncode({
        'agents': [
          {'id': 'orchestrator', 'description': '', 'version': '1'},
          {
            'id': 'engineer',
            'description': '',
            'version': '1',
            'required_capabilities': ['engineer_agent'],
            'usable': engineerUsable,
          },
        ],
      }),
      200,
    );

void main() {
  // Device attestation does real I/O that never completes inside a widget test's fake clock.
  // Resolve it once for real; requests then reuse the cache.
  setUpAll(() async {
    TestWidgetsFlutterBinding.ensureInitialized();
    final container = ProviderContainer();
    final backendUrl = container.read(backendUrlProvider);
    container.dispose();
    await DeviceValidationService.getAttestationHeaders(backendUrl: backendUrl);
  });

  ProviderContainer containerFor(_Agents agents) {
    final container = ProviderContainer(overrides: [
      tokenManagerProvider.overrideWith((ref) => _StubTokenManager(ref)),
      auraApiClientProvider.overrideWith((ref) => AuraApiClient(ref, httpClient: agents.client)),
    ]);
    addTearDown(container.dispose);
    return container;
  }

  test('coming back to the app refetches, so a newly granted Engineer unlocks', () async {
    final agents = _Agents([() => _list(engineerUsable: false), () => _list(engineerUsable: true)]);
    final container = containerFor(agents);
    final sub = container.listen(agentsProvider, (_, _) {});
    addTearDown(sub.close);

    final first = await container.read(agentsProvider.future);
    expect(first.singleWhere((a) => a.id == 'engineer').usable, isFalse);

    // Backgrounded, then back.
    final binding = WidgetsBinding.instance;
    for (final state in [
      AppLifecycleState.inactive,
      AppLifecycleState.hidden,
      AppLifecycleState.paused,
      AppLifecycleState.hidden,
      AppLifecycleState.inactive,
      AppLifecycleState.resumed,
    ]) {
      binding.handleAppLifecycleStateChanged(state);
    }

    final second = await container.read(agentsProvider.future);
    expect(agents.calls, 2);
    expect(second.singleWhere((a) => a.id == 'engineer').usable, isTrue);
  });

  testWidgets("the picker's error view offers Retry, and Retry loads the list", (tester) async {
    var calls = 0;
    await tester.pumpWidget(ProviderScope(
      overrides: [
        agentsProvider.overrideWith((ref) async {
          calls++;
          if (calls == 1) {
            throw const AuraApiError(error: 'unavailable', message: 'down', retryable: true, statusCode: 503);
          }
          return const [
            AgentInfo(id: 'orchestrator', description: '', version: '1'),
            AgentInfo(id: 'engineer', description: '', version: '1'),
          ];
        }),
      ],
      child: MaterialApp(
        theme: AuraThemeV2.dark,
        localizationsDelegates: AppLocalizations.localizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        home: const Scaffold(body: PersonaPickerSheet()),
      ),
    ));
    await tester.pump();
    await tester.pump();

    expect(find.text(_l10n.retry), findsOneWidget);
    await tester.tap(find.text(_l10n.retry));
    await tester.pump();
    await tester.pump();

    expect(calls, 2);
    expect(find.text(_l10n.chatPersonaEngineer), findsOneWidget);
  });
}
