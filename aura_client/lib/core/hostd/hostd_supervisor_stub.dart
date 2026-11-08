/// Web build of `hostd_supervisor.dart` -- there is no local process to spawn on web.
library;

import 'package:aura_client/core/hostd/hostd_supervisor_state.dart';

export 'package:aura_client/core/hostd/hostd_supervisor_state.dart';

class HostdSupervisor {
  Stream<String> get output => const Stream.empty();

  HostdSupervisorState get state => HostdSupervisorState.idle;

  Stream<HostdSupervisorState> get states => const Stream.empty();

  bool get isSpawnedHere => false;

  int? get pid => null;

  Future<void> start(String executablePath) async {
    throw UnsupportedError('The desktop Engineer daemon is not available on web.');
  }

  void stop() {}

  void dispose() {}
}
