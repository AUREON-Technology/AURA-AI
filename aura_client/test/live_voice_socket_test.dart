// The live-voice socket keeps its credential fresh and says when the server ends it.
//
// The server re-checks the socket's token every 60 s and closes with 4401 once it has expired
// (aura_core/api/ws_auth.py). The client renewed it only after a turn's `done` frame, with a
// token that could have 31 s left, and ignored the close: the screen stayed "listening".

import 'dart:async';
import 'dart:convert';

import 'package:aura_client/core/ai/voice/live/live_voice_protocol.dart';
import 'package:aura_client/core/ai/voice/live/live_voice_socket.dart';
import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/workspaces/active_workspace.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

class _RecordingTokens extends TokenManager {
  _RecordingTokens(super.ref);
  final validFor = <Duration>[];

  @override
  Future<String?> getValidAccessToken() async => 'jwt-test';

  @override
  Future<String?> getAccessTokenValidFor(Duration minValidity) async {
    validFor.add(minValidity);
    return 'jwt-renewed';
  }
}

class _FakeSink implements WebSocketSink {
  final sent = <dynamic>[];
  @override
  void add(dynamic data) => sent.add(data);
  @override
  Future<void> close([int? closeCode, String? closeReason]) async {}
  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

class _FakeChannel implements WebSocketChannel {
  final incoming = StreamController<dynamic>();
  final fakeSink = _FakeSink();
  int? code;

  @override
  Stream<dynamic> get stream => incoming.stream;
  @override
  WebSocketSink get sink => fakeSink;
  @override
  int? get closeCode => code;
  @override
  String? get closeReason => null;
  @override
  String? get protocol => null;
  @override
  Future<void> get ready => Future.value();

  Iterable<Map<String, dynamic>> framesOfType(String type) => fakeSink.sent
      .whereType<String>()
      .map((s) => jsonDecode(s) as Map<String, dynamic>)
      .where((f) => f['type'] == type);

  // pipe/transform/cast: unused by LiveVoiceSocket.
  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late _FakeChannel channel;
  late _RecordingTokens tokens;
  late LiveVoiceSocket socket;
  late ProviderContainer container;

  setUp(() {
    // Selecting a team persists it; each test starts in the personal scope.
    SharedPreferences.setMockInitialValues({});
    channel = _FakeChannel();
    final socketProvider = Provider<LiveVoiceSocket>((ref) => LiveVoiceSocket(
          ref,
          connect: (_) => channel,
          attestation: (_) async => const {'X-Device-Platform': 'test'},
          reauthInterval: const Duration(milliseconds: 20),
        ));
    container = ProviderContainer(overrides: [
      tokenManagerProvider.overrideWith((ref) => tokens = _RecordingTokens(ref)),
    ]);
    socket = container.read(socketProvider);
  });

  tearDown(() async {
    await socket.dispose();
    container.dispose();
  });

  test('renews the credential on a timer, with a token good past the next re-check', () async {
    await socket.connectAndAuth();
    await Future<void>.delayed(const Duration(milliseconds: 90));

    final reauths = channel.framesOfType('reauth').toList();
    expect(reauths.length, greaterThanOrEqualTo(2), reason: 'not only after a turn finishes');
    expect(reauths.first['token'], 'jwt-renewed');
    expect(tokens.validFor, everyElement(LiveVoiceSocket.reauthMinValidity));
    expect(LiveVoiceSocket.reauthMinValidity, greaterThan(const Duration(seconds: 60)),
        reason: 'must outlast one server re-check interval');
  });

  // AUD-04: typed chat sends X-Aura-Workspace; the live-voice frames carried only attestation,
  // so a voice turn in a team was metered to the personal plan and skipped the team's knowledge.
  test('auth and reauth name the active team, so a voice turn runs in it', () async {
    await container.read(activeWorkspaceProvider.notifier).select(
        const WorkspaceSummary(id: 'ws-team', kind: 'team', name: 'Acme', role: 'owner'));
    await socket.connectAndAuth();
    await Future<void>.delayed(const Duration(milliseconds: 60));

    final auth = channel.framesOfType('auth').single;
    expect((auth['attestation'] as Map)[workspaceHeaderName], 'ws-team');
    expect((auth['attestation'] as Map)['X-Device-Platform'], 'test', reason: 'attestation kept');
    final reauth = channel.framesOfType('reauth').first;
    expect((reauth['attestation'] as Map)[workspaceHeaderName], 'ws-team');
  });

  test('personal scope sends no workspace header', () async {
    await socket.connectAndAuth();
    final auth = channel.framesOfType('auth').single;
    expect((auth['attestation'] as Map).containsKey(workspaceHeaderName), isFalse);
  });

  test('a 4401 close from the server surfaces as session_expired', () async {
    await socket.connectAndAuth();
    final next = socket.frames.first;
    channel.code = LiveVoiceSocket.closeUnauthorized;
    await channel.incoming.close();

    final frame = await next.timeout(const Duration(seconds: 2));
    expect(frame, isA<LiveErrorFrame>().having((f) => f.error, 'error', 'session_expired'));
    expect(socket.isConnected, isFalse);
  });

  test('any other server close surfaces as socket_closed', () async {
    await socket.connectAndAuth();
    final next = socket.frames.first;
    channel.code = 1011;
    await channel.incoming.close();

    final frame = await next.timeout(const Duration(seconds: 2));
    expect(frame, isA<LiveErrorFrame>().having((f) => f.error, 'error', 'socket_closed'));
  });

  test('closing it ourselves reports nothing and stops renewing', () async {
    await socket.connectAndAuth();
    final errors = <LiveServerFrame>[];
    final sub = socket.frames.listen(errors.add);
    await socket.close();
    final sentAtClose = channel.fakeSink.sent.length;
    await Future<void>.delayed(const Duration(milliseconds: 60));

    expect(errors, isEmpty);
    expect(channel.fakeSink.sent.length, sentAtClose, reason: 'no reauth after close');
    await sub.cancel();
  });

  // INT-014: the server allows 5 s from accept to a valid auth frame. A slow attestation used
  // to be fetched *after* connecting, so the window could run out before auth was sent.
  test('fetches token and attestation before connecting; auth is the first frame sent',
      () async {
    final events = <String>[];
    final slowChannel = _FakeChannel();
    final slow = LiveVoiceSocket(
      container.read(_refProvider),
      connect: (_) {
        events.add('connect');
        return slowChannel;
      },
      attestation: (_) async {
        await Future<void>.delayed(const Duration(milliseconds: 120));
        events.add('attested');
        return const {'X-Device-Platform': 'test'};
      },
      reauthInterval: const Duration(hours: 1),
    );
    addTearDown(slow.dispose);

    await slow.connectAndAuth();

    expect(events, ['attested', 'connect']);
    final sent = slowChannel.fakeSink.sent.whereType<String>().toList();
    expect(sent, isNotEmpty);
    final first = jsonDecode(sent.first) as Map<String, dynamic>;
    expect(first['type'], 'auth');
    expect(first['token'], 'jwt-test');
    expect(first['attestation'], {'X-Device-Platform': 'test'});
  });

  // INT-015: each server close code gets its own error and message.
  for (final (code, error) in [
    (LiveVoiceSocket.closeUnavailable, 'feature_unavailable'),
    (LiveVoiceSocket.closeTooMany, 'too_many_sessions'),
    (LiveVoiceSocket.closeBadRequest, 'bad_request'),
  ]) {
    test('a $code close surfaces as $error', () async {
      await socket.connectAndAuth();
      final next = socket.frames.first;
      channel.code = code;
      await channel.incoming.close();

      final frame = await next.timeout(const Duration(seconds: 2));
      expect(frame, isA<LiveErrorFrame>().having((f) => f.error, 'error', error));
      expect(LiveVoiceSocket.terminalErrors, contains(error));
    });
  }

  test('close-code messages are distinct', () {
    final messages = [4401, 4503, 4429, 4400, 1011]
        .map((c) => LiveVoiceSocket.errorForCloseCode(c).message)
        .toSet();
    expect(messages, hasLength(5));
  });
}

/// Lends a test a [Ref] from the container, for constructing a socket by hand.
final _refProvider = Provider<Ref>((ref) => ref);
