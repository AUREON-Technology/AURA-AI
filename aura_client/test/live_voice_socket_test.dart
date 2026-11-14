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
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
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
    channel = _FakeChannel();
    final socketProvider = Provider<LiveVoiceSocket>((ref) => LiveVoiceSocket(
          ref,
          connect: (_) => channel,
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
}
