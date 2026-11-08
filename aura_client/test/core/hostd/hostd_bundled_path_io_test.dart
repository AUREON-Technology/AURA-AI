/// M8.10: a packaged build finds its own aura_hostd next to the app executable, so nobody has to
/// type a path into Settings before the desktop Engineer agent can start.
library;

import 'dart:io';

import 'package:aura_client/core/hostd/hostd_bundled_path_io.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  final name = Platform.isWindows ? 'aura_hostd.exe' : 'aura_hostd';
  final sep = Platform.pathSeparator;

  test('finds the daemon in the same directory as the app executable', () {
    final dir = Directory.systemTemp.createTempSync('bundled_hostd_');
    addTearDown(() => dir.deleteSync(recursive: true));
    final app = File('${dir.path}${sep}aura_client')..writeAsStringSync('');
    File('${dir.path}$sep$name').writeAsStringSync('');

    expect(bundledHostdPath(appExecutable: app.path), '${dir.path}$sep$name');
  });

  test('is null when the build has no bundled daemon (a source checkout)', () {
    final dir = Directory.systemTemp.createTempSync('bundled_hostd_');
    addTearDown(() => dir.deleteSync(recursive: true));
    final app = File('${dir.path}${sep}aura_client')..writeAsStringSync('');

    expect(bundledHostdPath(appExecutable: app.path), isNull);
  });

  test('looks only beside the executable, for the platform\'s file name', () {
    final asked = <String>[];
    bundledHostdPath(appExecutable: '${sep}opt${sep}aura${sep}aura_client', exists: (p) {
      asked.add(p);
      return false;
    });

    expect(asked, ['${sep}opt${sep}aura$sep$name']);
  });
}
