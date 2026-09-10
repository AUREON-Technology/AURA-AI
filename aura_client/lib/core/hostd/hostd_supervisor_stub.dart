/// Web build of `hostd_supervisor.dart` -- there is no local process to spawn on web.
library;

class HostdSupervisor {
  Stream<String> get output => const Stream.empty();

  bool get isSpawnedHere => false;

  Future<void> start(String executablePath) async {
    throw UnsupportedError('The desktop Engineer daemon is not available on web.');
  }

  void stop() {}

  void dispose() {}
}
