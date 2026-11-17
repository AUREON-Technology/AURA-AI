// Exercises parseHostdStatusBody's reading of GET /status's JSON body -- specifically
// backend_connected, which must be read independently of authenticated (see
// hostd_providers.dart's HostdStatusSnapshot doc comment for why the two can disagree).
import 'package:aura_client/core/hostd/hostd_providers.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('backend_connected: true is read even when authenticated is also true', () {
    final snapshot = parseHostdStatusBody({
      'authenticated': true,
      'backend_connected': true,
      'backend_url': null,
      'roots': [],
    });

    expect(snapshot.authenticated, true);
    expect(snapshot.backendConnected, true);
  });

  test('authenticated: true with backend_connected: false surfaces the reconnecting gap', () {
    final snapshot = parseHostdStatusBody({
      'authenticated': true,
      'backend_connected': false,
      'backend_url': null,
      'roots': [],
    });

    // The exact gap this whole fix is for: paired (has a token), but not actually connected --
    // a daemon that believes itself authenticated while its socket to the backend is down.
    expect(snapshot.authenticated, true);
    expect(snapshot.backendConnected, false);
  });

  test('a response missing backend_connected is null -- unknown, neither connected nor not', () {
    final snapshot = parseHostdStatusBody({
      'authenticated': true,
      'backend_url': null,
      'roots': [],
    });

    // An old daemon build predating this field must not be silently treated as connected. It
    // must not be treated as *disconnected* either, which is what defaulting to false did: a
    // stale aura_hostd binary that was genuinely connected (the backend's own "host agent
    // connected" log confirmed it) pinned the UI to "Running, reconnecting..." forever, with
    // nothing on screen pointing at the real cause. null is the honest third answer, and every
    // call site renders it as "update aura_hostd" rather than as a reconnect.
    expect(snapshot.backendConnected, isNull);
  });

  test('backendConnected defaults to null when the snapshot is built without one', () {
    // The unreachable-daemon path (_fetchStatus's HostdControlUnavailable branch) builds a
    // snapshot from `reachable` alone; it must not claim to know the connection state either.
    expect(HostdStatusSnapshot(reachable: false).backendConnected, isNull);
  });

  test('credentials_rejected: true means re-pair is required and Pair is offered (INT-003)', () {
    final snapshot = parseHostdStatusBody({
      'authenticated': true,
      'backend_connected': false,
      'credentials_rejected': true,
      'last_close_code': 4401,
    });

    expect(snapshot.credentialsRejected, isTrue);
    expect(snapshot.lastCloseCode, 4401);
    expect(snapshot.needsRepair, isTrue);
    expect(snapshot.canPair, isTrue, reason: 'a stored-but-refused token must not hide Pair');
  });

  test('a paired, accepted daemon -- or an older one without the field -- needs no re-pair', () {
    final current = parseHostdStatusBody({
      'authenticated': true,
      'backend_connected': false,
      'credentials_rejected': false,
    });
    final old = parseHostdStatusBody({'authenticated': true, 'backend_connected': true});

    for (final snapshot in [current, old]) {
      expect(snapshot.needsRepair, isFalse);
      expect(snapshot.canPair, isFalse);
    }
  });

  test('a status without roots or backend_url (sent without the token, INT-037) still parses', () {
    final snapshot = parseHostdStatusBody({'authenticated': false, 'backend_connected': false});
    expect(snapshot.roots, isEmpty);
    expect(snapshot.backendUrl, isNull);
    expect(snapshot.canPair, isTrue);
  });
}
