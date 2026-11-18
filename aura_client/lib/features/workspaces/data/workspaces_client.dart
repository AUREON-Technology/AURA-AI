// The customer workspace routes (SaaS P1-F2): workspaces, members, invites, plan, billing and
// shared team chats. Every call goes through [AuraApiClient] (JWT, attestation, correlation id,
// the active-workspace header); a non-2xx answer throws [AuraApiError].

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/network/aura_api_client.dart';
import '../../../core/workspaces/active_workspace.dart';
import 'workspace_models.dart';

class WorkspacesClient {
  WorkspacesClient(this._ref);

  final Ref _ref;

  AuraApiClient get _api => _ref.read(auraApiClientProvider);

  String _ws(String id) => '/api/v3/workspaces/${Uri.encodeComponent(id)}';

  Future<Map<String, dynamic>> _call(String method, String path, [Map<String, dynamic>? body]) =>
      _api.json(method: method, path: path, body: body);

  List<Map<String, dynamic>> _items(Map<String, dynamic> json, String key) => [
        for (final item in (json[key] as List<dynamic>? ?? const []))
          if (item is Map<String, dynamic>) item,
      ];

  // -- workspaces ---------------------------------------------------------------------------

  Future<List<WorkspaceSummary>> list() async =>
      _items(await _call('GET', '/api/v3/workspaces'), 'workspaces').map(WorkspaceSummary.fromJson).toList();

  Future<WorkspaceSummary> createTeam(String name) async =>
      WorkspaceSummary.fromJson(await _call('POST', '/api/v3/workspaces', {'name': name}));

  Future<void> deleteTeam(String id) => _call('DELETE', _ws(id));

  // -- members ------------------------------------------------------------------------------

  Future<List<Member>> members(String id) async =>
      _items(await _call('GET', '${_ws(id)}/members'), 'members').map(Member.fromJson).toList();

  Future<void> changeRole(String id, String userId, String role) =>
      _call('PATCH', '${_ws(id)}/members/${Uri.encodeComponent(userId)}', {'role': role});

  /// Removing yourself is leaving.
  Future<void> remove(String id, String userId) =>
      _call('DELETE', '${_ws(id)}/members/${Uri.encodeComponent(userId)}');

  Future<void> transfer(String id, String userId) =>
      _call('POST', '${_ws(id)}/transfer', {'user_id': userId});

  // -- invites ------------------------------------------------------------------------------

  Future<List<InviteSummary>> invites(String id) async =>
      _items(await _call('GET', '${_ws(id)}/invites'), 'invites').map(InviteSummary.fromJson).toList();

  Future<CreatedInvite> invite(String id, String email, String role) async {
    final json = await _call('POST', '${_ws(id)}/invites', {'email': email, 'role': role});
    return CreatedInvite(
      invite: InviteSummary.fromJson(json['invite'] as Map<String, dynamic>),
      token: json['token'] as String?,
    );
  }

  Future<void> revokeInvite(String id, String inviteId) =>
      _call('DELETE', '${_ws(id)}/invites/${Uri.encodeComponent(inviteId)}');

  Future<WorkspaceSummary?> accept(String token) async {
    final json = await _call('POST', '/api/v3/workspaces/invites/accept', {'token': token});
    final workspace = json['workspace'];
    return workspace is Map<String, dynamic> ? WorkspaceSummary.fromJson(workspace) : null;
  }

  // -- plan and billing ---------------------------------------------------------------------

  Future<PlanUsage> plan(String id) async => PlanUsage.fromJson(await _call('GET', '${_ws(id)}/plan'));

  Future<BillingStatus> billing(String id) async => BillingStatus.fromJson(await _call('GET', '${_ws(id)}/billing'));

  Future<Checkout> checkout(String id, String plan, int seats) async =>
      Checkout.fromJson(await _call('POST', '${_ws(id)}/billing/checkout', {'plan': plan, 'seats': seats}));

  Future<void> cancel(String id) => _call('POST', '${_ws(id)}/billing/cancel');

  /// The binding token to hand to StoreKit / Play Billing before an in-app purchase (SaaS P1-D2):
  /// it is how the store's notification finds this workspace.
  Future<String> storeToken(String id) async =>
      '${(await _call('POST', '${_ws(id)}/billing/store-token'))['token']}';

  // -- shared team chats --------------------------------------------------------------------

  Future<List<SharedChatSummary>> shared(String id) async =>
      _items(await _call('GET', '${_ws(id)}/shared-conversations'), 'conversations')
          .map(SharedChatSummary.fromJson)
          .toList();

  Future<SharedChat> sharedChat(String id, String sharedId) async =>
      SharedChat.fromJson(await _call('GET', '${_ws(id)}/shared-conversations/${Uri.encodeComponent(sharedId)}'));

  Future<SharedChatSummary> share(String id, String sourceId, String title, List<SharedChatMessage> messages) async =>
      SharedChatSummary.fromJson(await _call('POST', '${_ws(id)}/shared-conversations', {
        'source_id': sourceId,
        'title': title,
        'messages': [for (final m in messages) m.toJson()],
      }));

  Future<void> unshare(String id, String sharedId) =>
      _call('DELETE', '${_ws(id)}/shared-conversations/${Uri.encodeComponent(sharedId)}');
}

final workspacesClientProvider = Provider<WorkspacesClient>((ref) => WorkspacesClient(ref));
