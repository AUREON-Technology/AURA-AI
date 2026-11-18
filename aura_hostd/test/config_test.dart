// The daemon's config.json: the backend URL, the device token and (WP8, D-10b) whether this
// computer may be driven from the phone or the web. Exercised against a real file on disk.
import 'dart:convert';
import 'dart:io';

import 'package:aura_hostd/src/config.dart';
import 'package:path/path.dart' as p;
import 'package:test/test.dart';

void main() {
  late Directory configDir;
  late ConfigStore store;
  late File file;

  setUp(() {
    configDir = Directory.systemTemp.createTempSync('aura_hostd_config_test_');
    store = ConfigStore.forConfigDir(configDir);
    file = File(p.join(configDir.path, 'config.json'));
  });

  tearDown(() => configDir.deleteSync(recursive: true));

  group('remote_control (WP8, D-10b)', () {
    test('is off when there is no config file at all', () {
      expect(store.load().remoteControl, isFalse);
      expect(store.remoteControlEnabled(), isFalse);
    });

    test('is off when an existing config.json has no remote_control key (older daemon)', () {
      file.writeAsStringSync(jsonEncode({'backend_url': 'https://a.example', 'auth_token': 't'}));
      expect(store.load().remoteControl, isFalse);
      expect(store.remoteControlEnabled(), isFalse);
    });

    test('is off for anything but a JSON true', () {
      for (final value in ['true', 1, 'yes', null]) {
        file.writeAsStringSync(jsonEncode({'backend_url': 'https://a.example', 'remote_control': value}));
        expect(store.load().remoteControl, isFalse, reason: '$value');
      }
    });

    test('round-trips through the file', () {
      store.save(DaemonConfig(backendUrl: 'https://a.example', authToken: 't', remoteControl: true));
      final raw = jsonDecode(file.readAsStringSync()) as Map<String, dynamic>;
      expect(raw['remote_control'], isTrue);
      expect(store.load().remoteControl, isTrue);
      expect(store.remoteControlEnabled(), isTrue);
    });

    test('an unreadable config.json counts as off, never as on (fail closed)', () {
      store.save(DaemonConfig(backendUrl: 'https://a.example', authToken: 't', remoteControl: true));
      file.writeAsStringSync('{"remote_control": tru');
      expect(store.remoteControlEnabled(), isFalse);
    });

    test('copyWith changes only what it names', () {
      final base = DaemonConfig(backendUrl: 'https://a.example', authToken: 't', remoteControl: true);
      final repaired = base.copyWith(authToken: 'new');
      expect(repaired.backendUrl, 'https://a.example');
      expect(repaired.authToken, 'new');
      expect(repaired.remoteControl, isTrue);
      expect(base.copyWith(remoteControl: false).authToken, 't');
    });

    test('saving new credentials keeps the remote control choice made on this computer', () {
      store.save(DaemonConfig(backendUrl: 'https://a.example', authToken: 'old', remoteControl: true));
      store.saveCredentials(backendUrl: 'https://b.example', authToken: 'new');
      final loaded = store.load();
      expect(loaded.backendUrl, 'https://b.example');
      expect(loaded.authToken, 'new');
      expect(loaded.remoteControl, isTrue);
    });

    test('config.json stays readable by the owner only after a remote_control change', () {
      store.save(DaemonConfig(backendUrl: 'https://a.example', authToken: 't'));
      store.save(store.load().copyWith(remoteControl: true));
      final mode = file.statSync().mode & 0x1FF;
      expect(mode, 0x180, reason: 'expected 0600, got ${mode.toRadixString(8)}');
    }, testOn: '!windows');
  });
}
