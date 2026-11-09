/// Riverpod wiring for desktop updates (M8.10) -- see `desktop_update.dart`.
library;

import 'package:flutter/foundation.dart' show debugPrint;
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/desktop/update/desktop_update.dart';

final desktopUpdaterProvider = Provider<DesktopUpdater>((ref) => DesktopUpdater());

/// The update this build should offer, checked once per launch. `null` when there is none, when
/// updates are off (a source build), or when the check failed: an unreachable channel must never
/// get in the way of using the IDE, so a failure is logged and treated as "nothing to offer".
final desktopUpdateProvider = FutureProvider<AvailableUpdate?>((ref) async {
  final updater = ref.watch(desktopUpdaterProvider);
  if (!updater.enabled) return null;
  try {
    return await updater.check();
  } catch (error) {
    debugPrint('[DesktopUpdate] update check failed: $error');
    return null;
  }
});
