import 'dart:convert';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;

import '../../auth/token_manager.dart';
import '../../config/backend_url_provider.dart';
import '../../security/device_validation_service.dart';

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
/// Deliberately shaped like [VoiceClient] and the other `core/ai/*` clients -- JWT from
/// [TokenManager], device attestation headers from [DeviceValidationService] -- rather than
/// inventing a second way to authenticate a request.
class EngineerWorkspacesClient {
  EngineerWorkspacesClient(this._ref, {http.Client? httpClient}) : _http = httpClient;

  final Ref _ref;
  final http.Client? _http;

  String get _baseUrl => '${_ref.read(backendUrlProvider)}/api/v3/engineering/workspaces';

  Future<Map<String, String>> _headers() async {
    final token = await _ref.read(tokenManagerProvider).getValidAccessToken();
    final headers = <String, String>{'Content-Type': 'application/json'};
    if (token != null) headers['Authorization'] = 'Bearer $token';
    headers.addAll(
      await DeviceValidationService.getAttestationHeaders(
        backendUrl: _ref.read(backendUrlProvider),
      ),
    );
    return headers;
  }

  http.Client get _client => _http ?? http.Client();

  /// Lists the caller's workspaces, most recently used first.
  ///
  /// Returns [EngineerWorkspaceList.empty] on any failure rather than throwing: this drives a
  /// secondary panel, and a transient network error should leave the rest of the screen usable.
  Future<EngineerWorkspaceList> list() async {
    try {
      final response = await _client.get(Uri.parse(_baseUrl), headers: await _headers());
      if (response.statusCode != 200) return EngineerWorkspaceList.empty;
      final body = jsonDecode(response.body) as Map<String, dynamic>;
      final rows = (body['workspaces'] as List<dynamic>? ?? <dynamic>[])
          .map((row) => EngineerWorkspace.fromJson(row as Map<String, dynamic>))
          .toList();
      return EngineerWorkspaceList(
        enabled: body['enabled'] as bool? ?? false,
        workspaces: rows,
        ttlHours: (body['ttl_hours'] as num?)?.toDouble() ?? 72,
      );
    } catch (_) {
      return EngineerWorkspaceList.empty;
    }
  }

  /// Deletes a workspace and everything in it, including uncommitted work.
  ///
  /// Returns whether the server actually deleted it. The caller is expected to have confirmed
  /// with the user first -- this is not recoverable, and the server will not ask again.
  Future<bool> destroy(String workspaceId) async {
    try {
      final response = await _client.delete(
        Uri.parse('$_baseUrl/$workspaceId'),
        headers: await _headers(),
      );
      return response.statusCode == 200;
    } catch (_) {
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
