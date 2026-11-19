/// M8.10: desktop updates with rollback (lib/desktop/update/desktop_update.dart).
///
/// The manifest format is shared with scripts/desktop_update_manifest.py through one committed
/// sample (test/fixtures/desktop-update.sample.json). The backend test checks the script writes
/// it; this checks the app reads it.
library;

import 'dart:convert';
import 'dart:io';

import 'package:aura_client/desktop/update/desktop_update.dart';
import 'package:aura_client/desktop/update/update_signing_key.dart';
import 'package:crypto/crypto.dart';
import 'package:cryptography/cryptography.dart' as ed;
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

Map<String, dynamic> _sample() =>
    jsonDecode(File('test/fixtures/desktop-update.sample.json').readAsStringSync()) as Map<String, dynamic>;

AppVersion _v(String raw) => AppVersion.tryParse(raw)!;

/// A throwaway Ed25519 keypair, generated per test: the app's real key is injected away.
class _TestSigner {
  _TestSigner._(this._pair, this.publicKeyBase64);

  final ed.SimpleKeyPair _pair;
  final String publicKeyBase64;

  static Future<_TestSigner> create() async {
    final pair = await ed.Ed25519().newKeyPair();
    final public = await pair.extractPublicKey();
    return _TestSigner._(pair, base64.encode(public.bytes));
  }

  /// The `.sig` file's content: base64 of the signature over the exact bytes.
  Future<String> sign(List<int> bytes) async =>
      base64.encode((await ed.Ed25519().sign(bytes, keyPair: _pair)).bytes);
}

/// An updater whose channel serves [manifest] and, when [signature] is non-null, its `.sig`.
DesktopUpdater _signedChannel({
  required List<int> manifest,
  required String? signature,
  required String publicKey,
  void Function(Uri url)? onRequest,
}) =>
    DesktopUpdater(
      appVersion: '3.0.1',
      manifestUrl: 'https://github.com/o/r/releases/latest/download/desktop-update.json',
      platformKey: 'windows-x64',
      signingPublicKey: publicKey,
      client: MockClient((request) async {
        onRequest?.call(request.url);
        if (request.url.path.endsWith('.sig')) {
          return signature == null ? http.Response('Not Found', 404) : http.Response(signature, 200);
        }
        return http.Response.bytes(manifest, 200);
      }),
    );

void main() {
  // F18: the SHA-256 that guards the installer comes from the same release as the installer, so a
  // compromised release could swap both. The manifest is now signed with a key the app pins, and
  // nothing in it is trusted until that signature verifies.
  group('the signed manifest', () {
    late _TestSigner signer;
    late List<int> manifest;

    setUp(() async {
      signer = await _TestSigner.create();
      manifest = utf8.encode(jsonEncode(_sample()));
    });

    test('a manifest signed by the pinned key is accepted', () async {
      final updater = _signedChannel(
        manifest: manifest,
        signature: await signer.sign(manifest),
        publicKey: signer.publicKeyBase64,
      );

      expect((await updater.check())?.version, _v('3.0.2'));
    });

    test('a tampered manifest with a valid-looking checksum is refused', () async {
      final signature = await signer.sign(manifest);
      // The attacker points the installer at their own file, with that file's correct hash.
      final tampered = _sample();
      (tampered['platforms'] as Map)['windows-x64']['url'] = 'https://evil.example/setup.exe';
      (tampered['platforms'] as Map)['windows-x64']['sha256'] =
          sha256.convert(utf8.encode('malware')).toString();
      final updater = _signedChannel(
        manifest: utf8.encode(jsonEncode(tampered)),
        signature: signature,
        publicKey: signer.publicKeyBase64,
      );

      await expectLater(updater.check(), throwsA(isA<UpdateSignatureException>()));
    });

    test('even a one-byte change (whitespace) breaks the signature', () async {
      final signature = await signer.sign(manifest);
      final updater = _signedChannel(
        manifest: [...manifest, 0x0a],
        signature: signature,
        publicKey: signer.publicKeyBase64,
      );

      await expectLater(updater.check(), throwsA(isA<UpdateSignatureException>()));
    });

    test('a signature from another key is refused', () async {
      final other = await _TestSigner.create();
      final updater = _signedChannel(
        manifest: manifest,
        signature: await other.sign(manifest),
        publicKey: signer.publicKeyBase64,
      );

      await expectLater(updater.check(), throwsA(isA<UpdateSignatureException>()));
    });

    test('a missing signature is refused', () async {
      final updater = _signedChannel(manifest: manifest, signature: null, publicKey: signer.publicKeyBase64);

      await expectLater(updater.check(), throwsA(isA<UpdateSignatureException>()));
    });

    test('a malformed signature is refused', () async {
      for (final bad in ['', 'not base64 !!', base64.encode(List<int>.filled(63, 1))]) {
        final updater = _signedChannel(manifest: manifest, signature: bad, publicKey: signer.publicKeyBase64);
        await expectLater(updater.check(), throwsA(isA<UpdateSignatureException>()), reason: bad);
      }
    });

    test('with no real key configured (the placeholder) nothing is offered or even fetched', () async {
      final requested = <Uri>[];
      final updater = _signedChannel(
        manifest: manifest,
        signature: await signer.sign(manifest),
        publicKey: kUpdateSigningKeyPlaceholder,
        onRequest: requested.add,
      );

      await expectLater(
        updater.check(),
        throwsA(isA<UpdateSignatureException>().having((e) => e.message, 'message', contains('key'))),
      );
      expect(requested, isEmpty);
    });

    test('the key this build ships is a real 32-byte key or the placeholder, never something between', () {
      if (kUpdateSigningPublicKeyBase64 == kUpdateSigningKeyPlaceholder) return;
      expect(base64.decode(kUpdateSigningPublicKeyBase64), hasLength(32));
    });

    // Cross-implementation check: produced by Python's `cryptography` (the release script's side)
    // with a fixed test-only seed (bytes 0..31), over exactly these bytes.
    test('verifies a signature made by the release tooling (Python Ed25519)', () async {
      const manifestText =
          '{"schema":1,"version":"3.0.2","rollback":false,"platforms":{"windows-x64":{"url":'
          '"https://example.com/desktop-v3.0.2/setup.exe","sha256":'
          '"0000000000000000000000000000000000000000000000000000000000000000","size":1}}}';
      const publicKey = 'A6EHv/POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg=';
      const signature =
          '9IYbcq4XHGnDPPMTiYz45Z5lPWwZebgBki3VAn4nvecUpDM3BkcZwV+8XHPxIwEW966twtpD6VjSnlH/OFWVAw==';

      await verifyManifestSignature(utf8.encode(manifestText), signature, publicKey);
      await expectLater(
        verifyManifestSignature(utf8.encode(manifestText.replaceFirst('3.0.2', '3.0.3')), signature, publicKey),
        throwsA(isA<UpdateSignatureException>()),
      );
    });
  });

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

    test('check() fetches the manifest and its signature, and offers a newer build', () async {
      final keys = await _TestSigner.create();
      final manifest = utf8.encode(jsonEncode(_sample()));
      final requested = <String>[];
      final updater = DesktopUpdater(
        appVersion: '3.0.1',
        manifestUrl: 'https://github.com/o/r/releases/latest/download/desktop-update.json',
        platformKey: 'windows-x64',
        signingPublicKey: keys.publicKeyBase64,
        client: MockClient((request) async {
          requested.add(request.url.path.split('/').last);
          if (request.url.path.endsWith('/desktop-update.json.sig')) {
            return http.Response(await keys.sign(manifest), 200);
          }
          expect(request.url.path, endsWith('/desktop-update.json'));
          return http.Response.bytes(manifest, 200);
        }),
      );

      final update = await updater.check();
      expect(update?.version, _v('3.0.2'));
      expect(requested, containsAll(['desktop-update.json', 'desktop-update.json.sig']));
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
