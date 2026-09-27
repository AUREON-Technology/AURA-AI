import 'package:aura_client/core/hostd/hostd_control_client_io.dart';
import 'package:aura_client/core/hostd/hostd_error_messages.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('a HostdControlError passes its own message through unchanged', () {
    final error = HostdControlError(404, 'The local aura_hostd daemon does not recognize this request');
    expect(describeHostdError(error), 'The local aura_hostd daemon does not recognize this request');
  });

  test('a HostdControlUnavailable maps to a plain-language "not running" message', () {
    expect(
      describeHostdError(HostdControlUnavailable('anything internal here')),
      'aura_hostd is not running. Start it and try again.',
    );
  });

  test('an unrecognized exception falls back to its own string form', () {
    expect(describeHostdError(StateError('bad state')), 'Bad state: bad state');
  });
}
