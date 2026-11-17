import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;

import '../../network/aura_api_client.dart';
import '../../network/aura_api_error.dart';

/// One persistent engineer-agent workspace: a real git checkout on the server plus the
/// container that works on it, scoped to a single repository.
///
/// [status] is `hot` (running) or `warm` (stopped, files intact). The distinction is not
/// cosmetic on a single-VPS deployment -- only a hot workspace costs memory -- but from the
/// user's side both mean "your work is still there", which is what the UI says.
class EngineerWorkspace {
  const EngineerWorkspace({
    required this.id,
    required this.repo,
    required this.status,
    required this.createdAt,
    required this.lastActiveAt,
    required this.expiresAt,
    this.branch,
  });

  factory EngineerWorkspace.fromJson(Map<String, dynamic> json) => EngineerWorkspace(
        id: json['id'] as String,
        repo: json['repo'] as String,
        status: json['status'] as String? ?? 'warm',
        branch: json['branch'] as String?,
        createdAt: DateTime.parse(json['created_at'] as String),
        lastActiveAt: DateTime.parse(json['last_active_at'] as String),
        expiresAt: DateTime.parse(json['expires_at'] as String),
      );

  final String id;
  final String repo;
  final String status;
  final String? branch;
  final DateTime createdAt;
  final DateTime lastActiveAt;
  final DateTime expiresAt;

  bool get isRunning => status == 'hot';

  /// How long before this workspace is deleted for inactivity. Negative once it is due, which
  /// the reaper resolves on its next pass rather than exactly at zero -- so the UI should read
  /// this as "about to go", not as a countdown to a precise moment.
  Duration timeUntilExpiry(DateTime now) => expiresAt.difference(now);
}

/// What `GET /api/v3/engineering/workspaces` returned.
///
/// [enabled] is false on a deployment that has not switched the engineer agent on. That is a
/// state to render rather than an error: the endpoint answers 200 with an empty list, so the UI
/// can say "not available here" instead of showing a failure for a feature nobody enabled.
/// Only that answer means "disabled"; a failed request is an error (INT-025).
class EngineerWorkspaceList {
  const EngineerWorkspaceList({
    required this.enabled,
    required this.workspaces,
    required this.ttlHours,
  });

  final bool enabled;
  final List<EngineerWorkspace> workspaces;
  final double ttlHours;

  static const empty = EngineerWorkspaceList(
    enabled: false,
    workspaces: <EngineerWorkspace>[],
    ttlHours: 72,
  );
}

/// Client for the engineer agent's workspace endpoints (`/api/v3/engineering/workspaces`).
///
/// Goes through [AuraApiClient] -- JWT, device attestation, one retry on 401, a deadline and an
/// idempotency key on the DELETE -- rather than a second way to authenticate a request. It used
/// to build a fresh `http.Client` per call and never close it (INT-025); now an injected client
/// is reused, and without one each request uses `package:http`'s self-closing top-level calls.
class EngineerWorkspacesClient {
  EngineerWorkspacesClient(Ref ref, {http.Client? httpClient})
      : _api = httpClient == null
            ? ref.read(auraApiClientProvider)
            : AuraApiClient(ref, httpClient: httpClient);

  final AuraApiClient _api;

  static const _path = '/api/v3/engineering/workspaces';

  /// Lists the caller's workspaces, most recently used first.
  ///
  /// Throws [AuraApiError] when the request fails and [FormatException] when the answer is not
  /// the documented shape. It used to fold every failure -- a 401, a 5xx, no network, a parse
  /// error -- into [EngineerWorkspaceList.empty], which the panel shows as "Workspaces are not
  /// enabled": a wrong statement about the server, and no hint to retry (INT-025). Only the
  /// server's own `enabled: false` means disabled now; the panel renders errors separately.
  Future<EngineerWorkspaceList> list() async {
    final body = await _api.json(method: 'GET', path: _path);
    try {
      final rows = (body['workspaces'] as List<dynamic>? ?? <dynamic>[])
          .map((row) => EngineerWorkspace.fromJson(row as Map<String, dynamic>))
          .toList();
      return EngineerWorkspaceList(
        enabled: body['enabled'] as bool? ?? false,
        workspaces: rows,
        ttlHours: (body['ttl_hours'] as num?)?.toDouble() ?? 72,
      );
    } catch (e) {
      // A cast or date parse failed: the body is not the documented shape.
      throw FormatException('Unexpected workspaces response: $e');
    }
  }

  /// Deletes a workspace and everything in it, including uncommitted work.
  ///
  /// Returns whether the server actually deleted it. The caller is expected to have confirmed
  /// with the user first -- this is not recoverable, and the server will not ask again.
  Future<bool> destroy(String workspaceId) async {
    try {
      // Audit FSA-046: [AuraApiClient] keys every DELETE, and reuses the key on its 401 re-send,
      // so a retried destroy is one destroy.
      final response = await _api.request(
        method: 'DELETE',
        path: '$_path/${Uri.encodeComponent(workspaceId)}',
      );
      return response.statusCode == 200;
    } on AuraApiError {
      return false;
    }
  }
}

final engineerWorkspacesClientProvider = Provider<EngineerWorkspacesClient>((ref) {
  return EngineerWorkspacesClient(ref);
});

/// The caller's workspaces, refreshed on demand.
///
/// A `FutureProvider` rather than a polling stream: workspace state changes when the agent acts
/// or the reaper runs, neither of which is frequent enough to justify polling a phone's radio.
final engineerWorkspacesProvider = FutureProvider<EngineerWorkspaceList>((ref) async {
  return ref.read(engineerWorkspacesClientProvider).list();
});
