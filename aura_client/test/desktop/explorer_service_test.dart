/// Regression coverage for the Phase 0 desktop audit's Explorer finding (2026-09-25, re-verified
/// against a later commit the same day): `ExplorerService.listDirectory`/`readFile` fell back to
/// `dart:io` silently whenever no granted root matched, whether that was because hostd was
/// offline (the sanctioned fallback) or because hostd was reachable but simply never granted this
/// workspace at all (the case the plan's "Grant Access" UI was built for, but could never reach).
///
/// Fixing that also exposed a second, more fundamental bug the naive gate would have made worse:
/// the granted-root match compared `relativePath` directly against each granted root, and the
/// *workspace root's own* top-level listing always calls this with `relativePath: ''` -- which
/// never starts with any non-empty granted root, so a properly-granted workspace's own top-level
/// listing could never match hostd at all. Without also fixing that match, gating the fallback on
/// `hostConnected` would have made every workspace show "Grant Access" as soon as hostd connects,
/// even a genuinely already-granted one.
library;

import 'dart:io';

import 'package:aura_client/core/hostd/hostd_control_client.dart';
import 'package:aura_client/desktop/explorer/explorer_service.dart';
import 'package:flutter_test/flutter_test.dart';

/// Overrides only what `ExplorerService` calls -- the base class's real HTTP/discovery machinery
/// is never exercised since every test either doesn't reach it (ungranted-root case) or expects
/// this fake's own canned response.
class _FakeHostdClient extends HostdControlClient {
  _FakeHostdClient({this.listDirectoryResult, this.listDirectoryError});

  final Map<String, dynamic>? listDirectoryResult;
  final Object? listDirectoryError;
  int listDirectoryCalls = 0;

  @override
  Future<Map<String, dynamic>> listDirectory({required String root, String path = ''}) async {
    listDirectoryCalls++;
    if (listDirectoryError != null) throw listDirectoryError!;
    return listDirectoryResult!;
  }

  @override
  Future<Map<String, dynamic>> readFile({required String root, required String path}) async {
    throw StateError('not exercised by these tests');
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late Directory workspaceDir;
  late String workspaceRoot;

  setUp(() {
    workspaceDir = Directory.systemTemp.createTempSync('aura_client_explorer_service_test_');
    workspaceRoot = workspaceDir.resolveSymbolicLinksSync();
    File('${workspaceDir.path}/real_file.txt').writeAsStringSync('a real file only dart:io would see');
  });

  tearDown(() {
    workspaceDir.deleteSync(recursive: true);
  });

  group('workspace root listing (relativePath: "")', () {
    test('hostd connected but this workspace was never granted -> access denied, no dart:io read', () async {
      final fake = _FakeHostdClient();
      final service = ExplorerService(fake, workspaceRoot);

      final listing = await service.listDirectory(
        relativePath: '',
        grantedRoots: const [],
        hostConnected: true,
      );

      expect(listing.isAccessDenied, isTrue);
      expect(listing.entries, isEmpty);
      // The real file is right there on disk -- if this ever regresses to the old silent
      // dart:io fallback, this assertion is what would catch it.
      expect(listing.entries.map((e) => e.name), isNot(contains('real_file.txt')));
      expect(fake.listDirectoryCalls, 0);
    });

    test('hostd disconnected -> falls back to dart:io and actually lists the real directory', () async {
      final fake = _FakeHostdClient();
      final service = ExplorerService(fake, workspaceRoot);

      final listing = await service.listDirectory(
        relativePath: '',
        grantedRoots: const [],
        hostConnected: false,
      );

      expect(listing.isAccessDenied, isFalse);
      expect(listing.entries.map((e) => e.name), contains('real_file.txt'));
      expect(fake.listDirectoryCalls, 0);
    });

    test('hostd connected and this exact workspace root is granted -> uses hostd, not dart:io', () async {
      final fake = _FakeHostdClient(listDirectoryResult: {
        'entries': [
          {'name': 'from_hostd.txt', 'is_dir': false, 'size': 3, 'modified': '2026-01-01T00:00:00Z'},
        ],
      });
      final service = ExplorerService(fake, workspaceRoot);

      final listing = await service.listDirectory(
        relativePath: '',
        grantedRoots: [workspaceRoot],
        hostConnected: true,
      );

      expect(listing.isAccessDenied, isFalse);
      expect(listing.entries.map((e) => e.name), ['from_hostd.txt']);
      // Proves the top-level call actually reached hostd this time -- before the matching fix,
      // `relativePath: ''` could never match `workspaceRoot` in `grantedRoots` at all.
      expect(fake.listDirectoryCalls, 1);
    });

    test('a granted root whose hostd call fails falls back to dart:io (transient, not a permissions problem)',
        () async {
      final fake = _FakeHostdClient(listDirectoryError: StateError('daemon hiccup'));
      final service = ExplorerService(fake, workspaceRoot);

      final listing = await service.listDirectory(
        relativePath: '',
        grantedRoots: [workspaceRoot],
        hostConnected: true,
      );

      expect(listing.isAccessDenied, isFalse);
      expect(listing.entries.map((e) => e.name), contains('real_file.txt'));
    });
  });

  group('readFile', () {
    test('hostd connected but ungranted -> null, does not silently read the real file', () async {
      final fake = _FakeHostdClient();
      final service = ExplorerService(fake, workspaceRoot);

      final content = await service.readFile(
        relativePath: 'real_file.txt',
        grantedRoots: const [],
        hostConnected: true,
      );

      expect(content, isNull);
    });

    test('hostd disconnected -> falls back to dart:io and reads the real file', () async {
      final fake = _FakeHostdClient();
      final service = ExplorerService(fake, workspaceRoot);

      final content = await service.readFile(
        relativePath: 'real_file.txt',
        grantedRoots: const [],
        hostConnected: false,
      );

      expect(content, 'a real file only dart:io would see');
    });
  });
}
