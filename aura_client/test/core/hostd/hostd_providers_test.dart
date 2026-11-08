import 'dart:async';

import 'package:aura_client/core/hostd/hostd_providers.dart';
import 'package:aura_client/core/hostd/hostd_supervisor.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

class _FakeSupervisor extends HostdSupervisor {
  final StreamController<String> _controller = StreamController<String>.broadcast();

  @override
  Stream<String> get output => _controller.stream;

  @override
  bool get isSpawnedHere => true;

  @override
  HostdSupervisorState get state => HostdSupervisorState.idle;

  @override
  int? get pid => null;

  @override
  Stream<HostdSupervisorState> get states => const Stream.empty();

  @override
  Future<void> start(String executablePath) async {}

  @override
  void stop() {}

  @override
  void dispose() => _controller.close();

  void emit(String line) => _controller.add(line);
}

void main() {
  test('hostdLogProvider buffers lines from the supervisor and caps at 500', () async {
    final fake = _FakeSupervisor();
    final container = ProviderContainer(
      overrides: [hostdSupervisorProvider.overrideWithValue(fake)],
    );
    addTearDown(container.dispose);

    // Subscribe so the underlying stream listener attaches (Riverpod builds providers lazily).
    container.listen(hostdLogProvider, (previous, next) {});

    fake.emit('line 1');
    fake.emit('line 2');
    await Future<void>.delayed(Duration.zero);

    expect(container.read(hostdLogProvider), ['line 1', 'line 2']);

    for (var i = 0; i < 600; i++) {
      fake.emit('extra $i');
    }
    await Future<void>.delayed(Duration.zero);

    final state = container.read(hostdLogProvider);
    expect(state.length, 500);
    expect(state.last, 'extra 599');
  });

  test('hostdLogProvider.clear empties the buffer', () async {
    final fake = _FakeSupervisor();
    final container = ProviderContainer(
      overrides: [hostdSupervisorProvider.overrideWithValue(fake)],
    );
    addTearDown(container.dispose);
    container.listen(hostdLogProvider, (previous, next) {});

    fake.emit('line 1');
    await Future<void>.delayed(Duration.zero);
    expect(container.read(hostdLogProvider), isNotEmpty);

    container.read(hostdLogProvider.notifier).clear();
    expect(container.read(hostdLogProvider), isEmpty);
  });
}
