/// Which folder on the user's computer a phone or web Engineer turn works in (WP8, D-10).
///
/// The folders come from `GET /api/v3/host/folders` ([hostFoldersProvider]); the last one the
/// user picked is remembered per account, so a second account on the same phone does not inherit
/// the first one's folder. Nothing here can widen what the computer allows: the daemon only ever
/// accepts a folder it granted, and only while its own remote-control switch is on.
library;

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:aura_client/core/ai/engineer/host_folders_client.dart';
import 'package:aura_client/features/auth/presentation/auth_provider.dart';

/// The signed-in account the choice is remembered for; null while signed out.
final engineerFolderAccountProvider = Provider<String?>((ref) => ref.watch(authProvider).value?.id);

/// The folder the user last picked for this account, or null if they never picked one.
class EngineerRemoteFolderChoice extends AsyncNotifier<String?> {
  static String keyFor(String account) => 'engineer_remote_folder.$account';

  @override
  Future<String?> build() async {
    final account = ref.watch(engineerFolderAccountProvider);
    if (account == null) return null;
    try {
      final prefs = await SharedPreferences.getInstance();
      return prefs.getString(keyFor(account));
    } on Object {
      // No preference store (a test without one, a locked-down browser): nothing remembered.
      return null;
    }
  }

  Future<void> choose(String path) async {
    state = AsyncData(path);
    final account = ref.read(engineerFolderAccountProvider);
    if (account == null) return;
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(keyFor(account), path);
    } on Object {
      // Still used for this session; only the remembering is lost.
    }
  }
}

final engineerRemoteFolderChoiceProvider =
    AsyncNotifierProvider<EngineerRemoteFolderChoice, String?>(EngineerRemoteFolderChoice.new);

/// The folder a turn sent now would use: the remembered one while the computer still grants it,
/// otherwise the first granted folder. Null unless the computer is online with remote control on
/// and at least one folder granted -- then the turn carries no `workspace_root` at all.
String? effectiveRemoteFolder(HostFolders? folders, String? remembered) {
  if (folders == null || folders.state != HostFoldersState.available) return null;
  final paths = folders.roots.map((r) => r.path);
  if (remembered != null && paths.contains(remembered)) return remembered;
  return paths.first;
}

/// [effectiveRemoteFolder] for what the desktop bar currently shows.
final engineerRemoteWorkspaceRootProvider = Provider.autoDispose<String?>((ref) {
  final folders = ref.watch(hostFoldersProvider).value;
  if (folders == null || folders.state != HostFoldersState.available) return null;
  return effectiveRemoteFolder(folders, ref.watch(engineerRemoteFolderChoiceProvider).value);
});
