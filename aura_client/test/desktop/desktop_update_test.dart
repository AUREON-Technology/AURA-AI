/// M8.10: desktop updates with rollback (lib/desktop/update/desktop_update.dart).
///
/// The manifest format is shared with scripts/desktop_update_manifest.py through one committed
/// sample (test/fixtures/desktop-update.sample.json). The backend test checks the script writes
/// it; this checks the app reads it.
library;

import 'dart:convert';
import 'dart:io';

import 'package:aura_client/desktop/update/desktop_update.dart';
import 'package:crypto/crypto.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

Map<String, dynamic> _sample() =>
    jsonDecode(File('test/fixtures/desktop-update.sample.json').readAsStringSync()) as Map<String, dynamic>;

AppVersion _v(String raw) => AppVersion.tryParse(raw)!;

void main() {
  group('the shared manifest', () {
    test('parses the sample the release script writes', () {
      final manifest = UpdateManifest.fromJson(_sample());

      expect(manifest.version, _v('3.0.2'));
      expect(manifest.rollback, isFalse);
      expect(manifest.platforms.keys, unorderedEquals(['windows-x64', 'linux-x64']));
      final windows = manifest.platforms['windows-x64']!;
      expect(windows.url.scheme, 'https');
      expect(windows.url.path, endsWith('/desktop-v3.0.2/aura-desktop-windows-x64-3.0.2-setup.exe'));
      expect(windows.sha256, hasLength(64));
    });

    test('refuses an asset that is not served over https', () {
      final json = _sample();
      (json['platforms'] as Map)['windows-x64']['url'] = 'http://example.com/setup.exe';
      expect(() => UpdateManifest.fromJson(json), throwsA(isA<UpdateFormatException>()));
    });

    test('refuses a malformed checksum and an unknown schema', () {
      final badHash = _sample();
      (badHash['platforms'] as Map)['windows-x64']['sha256'] = 'abc';
      expect(() => UpdateManifest.fromJson(badHash), throwsA(isA<UpdateFormatException>()));

      final badSchema = _sample()..['schema'] = 2;
      expect(() => UpdateManifest.fromJson(badSchema), throwsA(isA<UpdateFormatException>()));
    });
  });

  group('versions', () {
    test('compare numerically, not as strings', () {
      expect(_v('3.0.10') > _v('3.0.9'), isTrue);
      expect(_v('3.1.0') > _v('3.0.99'), isTrue);
      expect(_v('10.0.0') > _v('9.9.9'), isTrue);
    });

    test('a pubspec build suffix is ignored; anything else is not a version', () {
      expect(_v('3.0.1+7'), _v('3.0.1'));
      expect(AppVersion.tryParse(''), isNull);
      expect(AppVersion.tryParse('3.0'), isNull);
      expect(AppVersion.tryParse('v3.0.1'), isNull);
    });
  });

  group('deciding', () {
    UpdateManifest manifest(String version, {bool rollback = false}) {
      final json = _sample()
        ..['version'] = version
        ..['rollback'] = rollback;
      return UpdateManifest.fromJson(json);
    }

    test('a newer version is offered', () {
      final update = decideUpdate(_v('3.0.1'), manifest('3.0.2'), 'windows-x64');
      expect(update?.version, _v('3.0.2'));
      expect(update?.isRollback, isFalse);
    });

    test('the same version is never offered', () {
      expect(decideUpdate(_v('3.0.2'), manifest('3.0.2'), 'windows-x64'), isNull);
      expect(decideUpdate(_v('3.0.2'), manifest('3.0.2', rollback: true), 'windows-x64'), isNull);
    });

    test('an older version is refused unless the channel was rolled back on purpose', () {
      expect(decideUpdate(_v('3.0.3'), manifest('3.0.2'), 'windows-x64'), isNull);

      final rolledBack = decideUpdate(_v('3.0.3'), manifest('3.0.2', rollback: true), 'windows-x64');
      expect(rolledBack?.version, _v('3.0.2'));
      expect(rolledBack?.isRollback, isTrue);
    });

    test('nothing is offered for a platform the release has no asset for', () {
      expect(decideUpdate(_v('3.0.1'), manifest('3.0.2'), 'macos-arm64'), isNull);
    });
  });

  group('the updater', () {
    test('is off in a source build: no baked-in version or manifest URL', () async {
      var asked = false;
      final updater = DesktopUpdater(
        appVersion: '',
        manifestUrl: '',
        platformKey: 'windows-x64',
        client: MockClient((_) async {
          asked = true;
          return http.Response('{}', 200);
        }),
      );

      expect(updater.enabled, isFalse);
      expect(await updater.check(), isNull);
      expect(asked, isFalse, reason: 'a dev build must not even contact the channel');
    });

    test('check() fetches the manifest and offers a newer build', () async {
      final updater = DesktopUpdater(
        appVersion: '3.0.1',
        manifestUrl: 'https://github.com/o/r/releases/latest/download/desktop-update.json',
        platformKey: 'windows-x64',
        client: MockClient((request) async {
          expect(request.url.path, endsWith('/desktop-update.json'));
          return http.Response(jsonEncode(_sample()), 200);
        }),
      );

      final update = await updater.check();
      expect(update?.version, _v('3.0.2'));
    });

    group('download', () {
      late Directory dir;
      setUp(() => dir = Directory.systemTemp.createTempSync('desktop_update_'));
      tearDown(() => dir.deleteSync(recursive: true));

      AvailableUpdate updateFor(List<int> bytes) => AvailableUpdate(
            version: _v('3.0.2'),
            isRollback: false,
            asset: UpdateAsset(
              url: Uri.parse('https://github.com/o/r/releases/download/desktop-v3.0.2/setup.exe'),
              sha256: sha256.convert(bytes).toString(),
              size: bytes.length,
            ),
          );

      test('returns the file when its checksum matches', () async {
        final bytes = utf8.encode('the real installer');
        final updater = DesktopUpdater(
          appVersion: '3.0.1',
          manifestUrl: 'https://x/desktop-update.json',
          platformKey: 'windows-x64',
          downloadDir: () => dir,
          client: MockClient((_) async => http.Response.bytes(bytes, 200)),
        );

        final file = await updater.download(updateFor(bytes));

        expect(file.readAsBytesSync(), bytes);
      });

      test('refuses and deletes a file that does not match the checksum', () async {
        final expected = utf8.encode('the real installer');
        final updater = DesktopUpdater(
          appVersion: '3.0.1',
          manifestUrl: 'https://x/desktop-update.json',
          platformKey: 'windows-x64',
          downloadDir: () => dir,
          client: MockClient((_) async => http.Response.bytes(utf8.encode('something else'), 200)),
        );

        await expectLater(updater.download(updateFor(expected)), throwsA(isA<UpdateIntegrityException>()));
        expect(dir.listSync(), isEmpty, reason: 'a mismatched installer must never be left to run');
      });
    });

    test('install() starts the installer silently and detached on Windows', () async {
      final launched = <List<String>>[];
      final updater = DesktopUpdater(
        appVersion: '3.0.1',
        manifestUrl: 'https://x/desktop-update.json',
        platformKey: 'windows-x64',
        launch: (exe, args) async => launched.add([exe, ...args]),
      );

      await updater.install(File('C:/Temp/setup.exe'));

      expect(launched.single.first, 'C:/Temp/setup.exe');
      expect(launched.single.skip(1), containsAll(['/SILENT', '/SUPPRESSMSGBOXES', '/CLOSEAPPLICATIONS']));
    });

    test('install() refuses on Linux, where updating is a manual download', () async {
      final updater = DesktopUpdater(
        appVersion: '3.0.1',
        manifestUrl: 'https://x/desktop-update.json',
        platformKey: 'linux-x64',
        launch: (exe, args) async => fail('nothing should launch'),
      );

      expect(() => updater.install(File('/tmp/app.tar.gz')), throwsUnsupportedError);
    });
  });
}
