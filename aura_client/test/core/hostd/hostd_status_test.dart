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

  test('a response missing backend_connected entirely defaults to false, not true', () {
    final snapshot = parseHostdStatusBody({
      'authenticated': true,
      'backend_url': null,
      'roots': [],
    });

    // An old daemon build predating this field must not be silently treated as connected --
    // the honest default when the daemon hasn't told us either way is "not connected".
    expect(snapshot.backendConnected, false);
  });
}
