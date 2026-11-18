// WP1 (D-03): the client half of `/api/v3/engineering/consent` -- read, accept, withdraw, and
// the 409 the server sends when the notice this app shows is not the one it requires.
import 'dart:convert';

import 'package:aura_client/core/ai/engineer/engineer_consent_repository.dart';
import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/network/aura_api_error.dart';
import 'package:aura_client/features/chat/data/agents_repository.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

class _StubTokenManager extends TokenManager {
  _StubTokenManager(super.ref);

  @override
  Future<String?> getValidAccessToken() async => 'jwt-test';
}

Map<String, dynamic> _consent({bool accepted = true, String current = kEngineerConsentNoticeVersion}) => {
      'accepted': accepted,
      'notice_version': accepted ? current : null,
      'current_version': current,
      'accepted_at': accepted ? '2026-11-17T09:30:00+00:00' : null,
    };

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late List<http.Request> requests;

  ProviderContainer containerWith(http.Response Function(http.Request) respond) {
    requests = [];
    final httpClient = MockClient((request) async {
      requests.add(request);
      return respond(request);
    });
    final container = ProviderContainer(overrides: [
      tokenManagerProvider.overrideWith((ref) => _StubTokenManager(ref)),
      engineerConsentRepositoryProvider
          .overrideWith((ref) => EngineerConsentRepository(ref, httpClient: httpClient)),
    ]);
    addTearDown(container.dispose);
    return container;
  }

  test('fetch GETs the consent route with the JWT and parses the answer', () async {
    final container = containerWith((_) => http.Response(jsonEncode(_consent()), 200));

    final consent = await container.read(engineerConsentRepositoryProvider).fetch();

    expect(requests.single.method, 'GET');
    expect(requests.single.url.path, '/api/v3/engineering/consent');
    expect(requests.single.headers['Authorization'], 'Bearer jwt-test');
    expect(consent.accepted, isTrue);
    expect(consent.noticeVersion, kEngineerConsentNoticeVersion);
    expect(consent.isCurrent, isTrue);
    expect(consent.appNoticeIsCurrent, isTrue);
    expect(consent.acceptedAt, DateTime.utc(2026, 11, 17, 9, 30));
  });

  test('an acceptance of an older notice is not current', () {
    final consent = EngineerConsent.fromJson({
      'accepted': true,
      'notice_version': '2026-01-01',
      'current_version': kEngineerConsentNoticeVersion,
      'accepted_at': '2026-01-01T00:00:00Z',
    });
    expect(consent.isCurrent, isFalse);
  });

  test('accept POSTs the version this app shows and returns the new state', () async {
    final container = containerWith((_) => http.Response(jsonEncode(_consent()), 200));

    final consent = await container.read(engineerConsentProvider.notifier).accept();

    // Reading the notifier also builds it (one GET); the POST is the write.
    final post = requests.singleWhere((r) => r.method == 'POST');
    expect(post.url.path, '/api/v3/engineering/consent');
    expect(post.headers['Authorization'], 'Bearer jwt-test');
    expect(jsonDecode(post.body), {'notice_version': '2026-11-17'});
    expect(consent.isCurrent, isTrue);
    expect(container.read(engineerConsentProvider).value?.isCurrent, isTrue);
  });

  test('a 409 version mismatch is its own exception, carrying the server version', () async {
    final container = containerWith((_) => http.Response(
          jsonEncode({
            'error': 'consent_version_mismatch',
            'message': 'notice version 2026-11-17 is not current',
            'retryable': false,
            'details': {'current_version': '2027-01-01'},
          }),
          409,
        ));

    await expectLater(
      container.read(engineerConsentRepositoryProvider).accept(),
      throwsA(isA<EngineerConsentVersionMismatch>()
          .having((e) => e.currentVersion, 'currentVersion', '2027-01-01')),
    );
  });

  test('any other failure stays an AuraApiError', () async {
    final container = containerWith((_) => http.Response('boom', 500));

    await expectLater(
      container.read(engineerConsentRepositoryProvider).accept(),
      throwsA(isA<AuraApiError>()),
    );
  });

  test('withdraw DELETEs and the provider then reads off', () async {
    final container = containerWith((_) => http.Response(jsonEncode(_consent(accepted: false)), 200));

    final consent = await container.read(engineerConsentProvider.notifier).withdraw();

    expect(requests.last.method, 'DELETE');
    expect(requests.last.url.path, '/api/v3/engineering/consent');
    expect(consent.accepted, isFalse);
    expect(consent.noticeVersion, isNull);
    expect(container.read(engineerConsentProvider).value?.isCurrent, isFalse);
  });

  test('a server requiring another notice version is reported as such', () async {
    final container = containerWith(
      (_) => http.Response(jsonEncode(_consent(accepted: false, current: '2027-01-01')), 200),
    );

    final consent = await container.read(engineerConsentProvider.future);

    expect(consent.isCurrent, isFalse);
    expect(consent.appNoticeIsCurrent, isFalse);
  });

  test('the agent list carries consent_required, defaulting to false', () {
    expect(AgentInfo.fromJson({'id': 'engineer', 'consent_required': true}).consentRequired, isTrue);
    expect(AgentInfo.fromJson({'id': 'engineer'}).consentRequired, isFalse);
  });
}
