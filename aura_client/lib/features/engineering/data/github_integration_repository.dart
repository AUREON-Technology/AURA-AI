import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:aura_client/core/network/aura_api_client.dart';

/// `GET /api/v3/integrations/github/status` -- whether the signed-in user has connected a
/// personal GitHub account through the engineer agent's OAuth flow. Distinct from whatever admin
/// PAT the deployment may also have configured (`AURA_GITHUB_TOKEN`): this is per-user state, so
/// it can only ever say yes/no for the caller, never "GitHub is enabled for this deployment".
class GithubConnectionStatus {
  final bool connected;
  final String? githubLogin;
  final String? scopes;
  final DateTime? connectedAt;

  const GithubConnectionStatus({
    required this.connected,
    this.githubLogin,
    this.scopes,
    this.connectedAt,
  });

  factory GithubConnectionStatus.fromJson(Map<String, dynamic> json) {
    return GithubConnectionStatus(
      connected: json['connected'] == true,
      githubLogin: json['github_login']?.toString(),
      scopes: json['scopes']?.toString(),
      connectedAt: DateTime.tryParse(json['connected_at']?.toString() ?? ''),
    );
  }

  static const disconnected = GithubConnectionStatus(connected: false);
}

/// `autoDispose` so the connection page always re-checks on open rather than showing a stale
/// "not connected" from before the user finished the browser round trip.
///
/// All three calls here go through [AuraApiClient] (INT-024): attestation headers, one retry on
/// 401, a deadline, and a typed [AuraApiError] on failure.
final githubConnectionStatusProvider =
    FutureProvider.autoDispose<GithubConnectionStatus>((ref) async {
  final decoded = await ref.read(auraApiClientProvider).json(
        method: 'GET',
        path: '/api/v3/integrations/github/status',
      );
  return GithubConnectionStatus.fromJson(decoded);
});

/// `GET /api/v3/integrations/github/connect` -- returns the GitHub authorization URL to open in
/// a system browser. Deliberately a plain fetch-and-return rather than following a server-side
/// redirect: the backend authenticates this call by bearer JWT, which a browser navigation would
/// not carry, so the client has to be the one to open the URL it gets back.
///
/// Takes the [AuraApiClient] rather than a Riverpod `Ref` -- this is called from a
/// `ConsumerState`'s `WidgetRef` as well as from providers, and both can read
/// [auraApiClientProvider].
Future<String> fetchGithubAuthorizeUrl(AuraApiClient api) async {
  final decoded = await api.json(
    method: 'GET',
    path: '/api/v3/integrations/github/connect',
  );
  final url = decoded['authorize_url']?.toString();
  if (url == null || url.isEmpty) {
    throw Exception('Server did not return a GitHub authorization URL.');
  }
  return url;
}

/// `POST /api/v3/integrations/github/disconnect` -- revokes AURA's stored copy of the token.
/// Does not revoke the GitHub App's own authorization (the user does that from github.com if
/// they want it gone entirely); this only removes what the engineer agent can use.
Future<void> disconnectGithub(AuraApiClient api) async {
  await api.json(
    method: 'POST',
    path: '/api/v3/integrations/github/disconnect',
  );
}
