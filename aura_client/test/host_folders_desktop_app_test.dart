// WP8: on the desktop app itself, Engineer turns are desktop-origin (the server reads the
// desktop token's claim), so the computer's "allow control from my phone and the web" setting
// does not apply -- the folder bar must not tell someone at their desk that remote control is off.

import 'package:aura_client/core/ai/engineer/host_folders_client.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  final offWithFolders = HostFolders.fromJson({
    'connected': true,
    'remote_control': false,
    'roots': [
      {'path': '/home/dev/app', 'trusted': true},
    ],
  });

  tearDown(() => debugDefaultTargetPlatformOverride = null);

  test('on a phone, remote control off is what the bar says', () {
    debugDefaultTargetPlatformOverride = TargetPlatform.android;
    expect(offWithFolders.state, HostFoldersState.remoteControlOff);
  });

  for (final platform in [TargetPlatform.windows, TargetPlatform.linux, TargetPlatform.macOS]) {
    test('on the ${platform.name} desktop app the folders are available anyway', () {
      debugDefaultTargetPlatformOverride = platform;
      expect(offWithFolders.state, HostFoldersState.available);
    });
  }

  test('not connected stays not connected on the desktop app', () {
    debugDefaultTargetPlatformOverride = TargetPlatform.linux;
    expect(
      HostFolders.fromJson({'connected': false, 'remote_control': null, 'roots': []}).state,
      HostFoldersState.notConnected,
    );
  });
}
