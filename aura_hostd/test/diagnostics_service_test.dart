import 'dart:io';

import 'package:aura_hostd/src/diagnostics_service.dart';
import 'package:path/path.dart' as p;
import 'package:test/test.dart';

void main() {
  late Directory root;

  setUp(() => root = Directory.systemTemp.createTempSync('aura_hostd_diag_test_'));
  tearDown(() => root.deleteSync(recursive: true));

  group('DiagnosticsService (G-17, SEC-10)', () {
    test('refuses an untrusted folder without running anything', () async {
      final service = DiagnosticsService(toolCommands: {
        'marker': ['touch', 'ran'],
      });
      await expectLater(
        service.run(root.path, 'marker', trusted: false),
        throwsA(isA<DiagnosticsNotTrustedError>()),
      );
      expect(File(p.join(root.path, 'ran')).existsSync(), isFalse);
    });

    test('runs in a trusted folder', () async {
      final service = DiagnosticsService(toolCommands: {
        'marker': ['touch', 'ran'],
      });
      final result = await service.run(root.path, 'marker', trusted: true);
      expect(result.exitCode, 0);
      expect(File(p.join(root.path, 'ran')).existsSync(), isTrue);
    }, testOn: '!windows');

    test('a linter that hangs is killed at the timeout', () async {
      final service = DiagnosticsService(
        timeout: const Duration(seconds: 1),
        toolCommands: {
          'hang': ['sh', '-c', 'sleep 30'],
        },
      );
      final stopwatch = Stopwatch()..start();
      final result = await service.run(root.path, 'hang', trusted: true);
      expect(stopwatch.elapsed, lessThan(const Duration(seconds: 10)));
      expect(result.timedOut, isTrue);
      expect(result.exitCode, isNot(0));
    }, testOn: '!windows');

    test('runs with the minimal environment, not the daemon\'s', () async {
      final service = DiagnosticsService(
        toolCommands: {
          'env': ['env'],
        },
        parentEnvironment: {...Platform.environment, 'AURA_TEST_PLANTED_SECRET': 'hunter2'},
      );
      final result = await service.run(root.path, 'env', trusted: true);
      expect(result.stdout, isNot(contains('hunter2')));
      expect(result.stdout, contains('PATH='));
    }, testOn: '!windows');

    test('still rejects an unknown tool', () async {
      await expectLater(
        DiagnosticsService().run(root.path, 'not_a_tool', trusted: true),
        throwsArgumentError,
      );
    });
  });
}
