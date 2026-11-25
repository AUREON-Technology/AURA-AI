import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;

import '../../network/aura_api_client.dart';
import '../../network/aura_api_error.dart';
import '../../platform/aura_platform.dart';

/// One folder the paired computer has granted the Engineer.
class HostFolder {
  const HostFolder({required this.path, required this.trusted, this.isGit});

  final String path;
  final bool trusted;

  /// Whether the folder is a git repository, which isolation needs (D-13). Null from a daemon
  /// too old to say: unknown, so nothing offers to initialize it.
  final bool? isGit;
}

/// What the Engineer chat's desktop bar can say about the user's computer.
enum HostFoldersState {
  /// No paired `aura_hostd` is online.
  notConnected,

  /// Online, but "Allow control from my phone and the web" is off there (D-10b). A daemon too
  /// old to report the flag lands here as well: it has no remote control.
  remoteControlOff,

  /// Online with remote control on, but no folder is granted yet.
  noFolders,

  /// Online, remote control on, and at least one folder to pick.
  available,
}

/// What `GET /api/v3/host/folders` answers (WP8, D-10, UX-06).
class HostFolders {
  const HostFolders({required this.connected, this.remoteControl, this.roots = const []});

  factory HostFolders.fromJson(Map<String, dynamic> json) {
    final connected = json['connected'];
    final remoteControl = json['remote_control'];
    final roots = json['roots'] ?? const [];
    if (connected is! bool || (remoteControl != null && remoteControl is! bool) || roots is! List) {
      throw const FormatException('connected must be a bool, remote_control a bool or null, roots a list');
    }
    return HostFolders(
      connected: connected,
      remoteControl: remoteControl as bool?,
      roots: [
        for (final root in roots)
          if (root is Map && root['path'] is String)
            HostFolder(
              path: root['path'] as String,
              trusted: root['trusted'] == true,
              isGit: root['is_git'] is bool ? root['is_git'] as bool : null,
            ),
      ],
    );
  }

  /// Whether a paired daemon is online for this account.
  final bool connected;

  /// The daemon's own flag; null when no daemon is online to ask.
  final bool? remoteControl;
  final List<HostFolder> roots;

  HostFoldersState get state {
    if (!connected) return HostFoldersState.notConnected;
    // The desktop app's own turns are desktop-origin (its token says so), so the computer's
    // "allow control from my phone and the web" setting is not what decides them: telling
    // someone at their desk that remote control is off would only mislead.
    if (remoteControl != true && !isDesktopApp) return HostFoldersState.remoteControlOff;
    if (roots.isEmpty) return HostFoldersState.noFolders;
    return HostFoldersState.available;
  }
}

/// Client for `/api/v3/host/folders`: the paired computer's status and granted folders, relayed
/// by the backend, for a phone or web Engineer turn to pick a `workspace_root` from (WP8).
///
/// Through [AuraApiClient], like [EngineerConsentRepository]: JWT, device attestation, one retry
/// on 401, and a deadline. The route needs the Engineer capability; without it the server
/// answers 403, which surfaces as [AuraApiError].
class HostFoldersClient {
  HostFoldersClient(Ref ref, {http.Client? httpClient})
      : _api = httpClient == null ? ref.read(auraApiClientProvider) : AuraApiClient(ref, httpClient: httpClient);

  final AuraApiClient _api;

  static const path = '/api/v3/host/folders';

  /// How long [initGit] waits. The gateway waits up to 150 s for the computer (`v3.py`,
  /// `host_folder_git_init`: "rather than giving up first"), because `git add -A` over a large
  /// folder is slow. The shared client's 30 s default made the app report "unreachable" while the
  /// init was still running, and a retry then hit "already a git repository" (audit AUD-13).
  static const initGitTimeout = Duration(seconds: 160);

  /// Throws [AuraApiError] when the request fails and [FormatException] on a body that is not
  /// the documented shape.
  Future<HostFolders> fetch() async => HostFolders.fromJson(await _api.json(method: 'GET', path: path));

  /// Makes the granted folder [folder] on the user's computer a git repository (D-13): `git init`
  /// and a first commit of its current files. Returns that commit's sha (empty if the daemon did
  /// not say). The computer's refusal is a 409 `desktop_refused` whose `message` is the daemon's
  /// own text; 503 means no computer is connected. Both surface as [AuraApiError].
  Future<String> initGit(String folder) async {
    final body = await _api.json(
      method: 'POST',
      path: '$path/git-init',
      body: {'path': folder},
      timeout: initGitTimeout,
    );
    final commit = body['commit'];
    return commit is String ? commit : '';
  }
}

final hostFoldersClientProvider = Provider<HostFoldersClient>((ref) => HostFoldersClient(ref));

/// The user's computer as the backend last reported it. Fetched while something shows it (the
/// Engineer chat's desktop bar) and refreshed by invalidating it -- the bar's "Check again".
/// Not retried automatically: a failed fetch is shown, with the same "Check again".
final hostFoldersProvider = FutureProvider.autoDispose<HostFolders>(
  (ref) => ref.read(hostFoldersClientProvider).fetch(),
  retry: (_, _) => null,
);
