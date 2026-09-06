import 'dart:convert';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;
import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/config/backend_url_provider.dart';

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
final githubConnectionStatusProvider =
    FutureProvider.autoDispose<GithubConnectionStatus>((ref) async {
  final backendUrl = ref.read(backendUrlProvider);
  final token = await ref.read(tokenManagerProvider).getValidAccessToken();
  final response = await http.get(
    Uri.parse('$backendUrl/api/v3/integrations/github/status'),
    headers: {if (token != null) 'Authorization': 'Bearer $token'},
  );
  if (response.statusCode != 200) {
    throw Exception('Failed to load GitHub connection status: ${response.statusCode}');
  }
  return GithubConnectionStatus.fromJson(jsonDecode(response.body) as Map<String, dynamic>);
});

/// `GET /api/v3/integrations/github/connect` -- returns the GitHub authorization URL to open in
/// a system browser. Deliberately a plain fetch-and-return rather than following a server-side
/// redirect: the backend authenticates this call by bearer JWT, which a browser navigation would
/// not carry, so the client has to be the one to open the URL it gets back.
///
/// Takes the backend URL and access token directly rather than a Riverpod `Ref` -- this is
/// called from a `ConsumerState`'s `WidgetRef` as well as from providers, and passing plain
/// values sidesteps needing both ref flavours to satisfy the same parameter type.
Future<String> fetchGithubAuthorizeUrl({required String backendUrl, String? accessToken}) async {
  final response = await http.get(
    Uri.parse('$backendUrl/api/v3/integrations/github/connect'),
    headers: {if (accessToken != null) 'Authorization': 'Bearer $accessToken'},
  );
  if (response.statusCode != 200) {
    throw Exception('Failed to start the GitHub connection: ${response.statusCode}');
  }
  final decoded = jsonDecode(response.body) as Map<String, dynamic>;
  final url = decoded['authorize_url']?.toString();
  if (url == null || url.isEmpty) {
    throw Exception('Server did not return a GitHub authorization URL.');
  }
  return url;
}

/// `POST /api/v3/integrations/github/disconnect` -- revokes AURA's stored copy of the token.
/// Does not revoke the GitHub App's own authorization (the user does that from github.com if
/// they want it gone entirely); this only removes what the engineer agent can use.
Future<void> disconnectGithub({required String backendUrl, String? accessToken}) async {
  final response = await http.post(
    Uri.parse('$backendUrl/api/v3/integrations/github/disconnect'),
    headers: {if (accessToken != null) 'Authorization': 'Bearer $accessToken'},
  );
  if (response.statusCode != 200) {
    throw Exception('Failed to disconnect GitHub: ${response.statusCode}');
  }
}
