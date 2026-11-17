// Exercises HostdStatusSnapshot.pairedToDifferentServer and sameBackendServer.
//
// Found live (2026-11-17): the production backend moved to api.aureon-ai.tech, and the local
// aura_hostd stayed paired to the old Railway domain, which now answers "Application not found".
// It retried that address forever. The status bar said "Reconnecting to server..." indefinitely,
// the Engineer said it could not touch any file, and Pair was hidden because the daemon was
// "paired" -- so nothing on screen offered a way out. These pin the check that tells that state
// apart from an ordinary reconnect, and the cases where it must stay quiet.
import 'package:aura_client/core/hostd/hostd_providers.dart';
import 'package:flutter_test/flutter_test.dart';

const _oldHost = 'https://aura-ai-production-03a4.up.railway.app';
const _liveHost = 'https://api.aureon-ai.tech';

HostdStatusSnapshot _status({
  bool authenticated = true,
  String? backendUrl = _oldHost,
  bool? backendConnected = false,
}) =>
    HostdStatusSnapshot(
      reachable: true,
      authenticated: authenticated,
      backendConnected: backendConnected,
      backendUrl: backendUrl,
    );

void main() {
  group('pairedToDifferentServer', () {
    test('a daemon paired to the old host while the app is on the live one is flagged', () {
      expect(_status().pairedToDifferentServer(_liveHost), isTrue);
    });

    test('a daemon paired to the same server as the app is not', () {
      expect(_status(backendUrl: _liveHost).pairedToDifferentServer(_liveHost), isFalse);
    });

    // The ordinary reconnect (a redeploy, a network blip) must keep reading as "reconnecting":
    // the daemon is on the right server and will get back by itself, so re-pairing would be noise.
    test('an ordinary reconnect to the right server is not flagged', () {
      final reconnecting = _status(backendUrl: _liveHost, backendConnected: false);
      expect(reconnecting.pairedToDifferentServer(_liveHost), isFalse);
    });

    test('an unpaired daemon has no stale pairing to flag', () {
      expect(_status(authenticated: false).pairedToDifferentServer(_liveHost), isFalse);
    });

    // An aura_hostd too old to report its URL must not raise a false alarm.
    test('a daemon that did not report its URL is not flagged', () {
      expect(_status(backendUrl: null).pairedToDifferentServer(_liveHost), isFalse);
      expect(_status(backendUrl: '  ').pairedToDifferentServer(_liveHost), isFalse);
    });

    test('is judged by the server, so the connection state does not matter', () {
      expect(_status(backendConnected: true).pairedToDifferentServer(_liveHost), isTrue);
      expect(_status(backendConnected: null).pairedToDifferentServer(_liveHost), isTrue);
    });

    test('works from a parsed /status body, the way the screens receive it', () {
      final parsed = parseHostdStatusBody({
        'authenticated': true,
        'backend_connected': false,
        'backend_url': _oldHost,
        'roots': [],
      });
      expect(parsed.pairedToDifferentServer(_liveHost), isTrue);
    });
  });

  group('sameBackendServer', () {
    test('ignores a trailing slash, case and an explicit default port', () {
      expect(sameBackendServer('https://API.aureon-ai.tech/', _liveHost), isTrue);
      expect(sameBackendServer('https://api.aureon-ai.tech:443', _liveHost), isTrue);
      expect(sameBackendServer('  https://api.aureon-ai.tech  ', _liveHost), isTrue);
    });

    test('tells apart hosts, schemes and ports', () {
      expect(sameBackendServer('https://aureon-ai.tech', _liveHost), isFalse);
      expect(sameBackendServer('http://api.aureon-ai.tech', _liveHost), isFalse);
      expect(sameBackendServer('https://api.aureon-ai.tech:8443', _liveHost), isFalse);
    });

    test('a value that is not a URL compares as text instead of guessing a difference', () {
      expect(sameBackendServer('not a url', 'Not A Url/'), isTrue);
      expect(sameBackendServer('not a url', _liveHost), isFalse);
    });
  });
}
