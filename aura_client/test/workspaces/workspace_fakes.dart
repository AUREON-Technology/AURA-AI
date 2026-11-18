// Shared fakes for the workspace screens' tests (SaaS P1-F2).

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/network/aura_api_error.dart';
import 'package:aura_client/core/workspaces/active_workspace.dart';
import 'package:aura_client/features/auth/presentation/auth_provider.dart';
import 'package:aura_client/features/workspaces/data/workspace_models.dart';
import 'package:aura_client/features/workspaces/data/workspaces_client.dart';
import 'package:aura_client/l10n/l10n.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_riverpod/misc.dart' show Override;
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';

const personal = WorkspaceSummary(id: 'ws-me', kind: 'personal', name: 'Personal', role: 'owner');

WorkspaceSummary team({String role = 'owner'}) => WorkspaceSummary(id: 'ws-team', kind: 'team', name: 'Acme', role: role);

AuraApiError apiError(int status, String error, String message, [Map<String, dynamic> details = const {}]) =>
    AuraApiError(error: error, message: message, retryable: false, statusCode: status, details: details);

class FakeAuth extends AuthNotifier {
  FakeAuth(this.uid);
  final String uid;

  @override
  Future<UserProfile?> build() async => UserProfile(id: uid, email: '$uid@example.com', displayName: uid);
}

/// Records every call; each answer can be replaced per test.
class FakeWorkspaces extends WorkspacesClient {
  FakeWorkspaces(super.ref);

  final calls = <String>[];
  List<WorkspaceSummary> workspaces = [personal, team()];
  List<Member> memberList = const [
    Member(userId: 'own', role: 'owner', email: 'own@example.com'),
    Member(userId: 'amy', role: 'member', email: 'amy@example.com'),
    Member(userId: 'ada', role: 'admin', email: 'ada@example.com'),
  ];
  List<InviteSummary> inviteList = const [InviteSummary(inviteId: 'i1', email: 'dan@example.com', role: 'member')];
  Object? failWith;
  PlanUsage usage = const PlanUsage(
    plan: 'free', seats: 1, messagesUsed: 12, messagesAllowed: 50, storageUsed: 1024 * 1024,
    storageAllowed: 10 * 1024 * 1024, engineer: false,
  );
  BillingStatus billingStatus = const BillingStatus();
  List<SharedChatSummary> sharedList = const [];

  Future<T> _answer<T>(String call, T value) async {
    calls.add(call);
    final error = failWith;
    if (error != null) throw error;
    return value;
  }

  @override
  Future<List<WorkspaceSummary>> list() async => workspaces;
  @override
  Future<WorkspaceSummary> createTeam(String name) => _answer('create:$name', WorkspaceSummary(id: 'ws-new', kind: 'team', name: name, role: 'owner'));
  @override
  Future<void> deleteTeam(String id) => _answer('delete:$id', null);
  @override
  Future<List<Member>> members(String id) async => memberList;
  @override
  Future<List<InviteSummary>> invites(String id) async => inviteList;
  @override
  Future<CreatedInvite> invite(String id, String email, String role) => _answer('invite:$email:$role',
      CreatedInvite(invite: InviteSummary(inviteId: 'i2', email: email, role: role), token: 'auraiv_tok'));
  @override
  Future<void> changeRole(String id, String userId, String role) => _answer('role:$userId:$role', null);
  @override
  Future<void> remove(String id, String userId) => _answer('remove:$userId', null);
  @override
  Future<void> transfer(String id, String userId) => _answer('transfer:$userId', null);
  @override
  Future<void> revokeInvite(String id, String inviteId) => _answer('revoke:$inviteId', null);
  @override
  Future<PlanUsage> plan(String id) => _answer('plan:$id', usage);
  @override
  Future<BillingStatus> billing(String id) => _answer('billing:$id', billingStatus);
  @override
  Future<Checkout> checkout(String id, String plan, int seats) => _answer('checkout:$plan:$seats',
      Checkout(paymentId: 'p1', amountPiastres: 15000, referenceNumber: '912345678', expiresAt: DateTime.utc(2026, 11, 21)));
  @override
  Future<void> cancel(String id) => _answer('cancel:$id', null);
  @override
  Future<WorkspaceSummary?> accept(String token) => _answer('accept', team(role: 'member'));
  @override
  Future<List<SharedChatSummary>> shared(String id) async => sharedList;
  @override
  Future<SharedChat> sharedChat(String id, String sharedId) => _answer('shared:$sharedId', SharedChat(
      summary: sharedList.firstWhere((s) => s.id == sharedId),
      messages: const [SharedChatMessage(role: 'user', text: 'How do we deploy?')]));
  @override
  Future<SharedChatSummary> share(String id, String sourceId, String title, List<SharedChatMessage> messages) =>
      _answer('share:$sourceId:${messages.length}', SharedChatSummary(id: 's-new', title: title, sharedBy: 'own'));
  @override
  Future<void> unshare(String id, String sharedId) => _answer('unshare:$sharedId', null);
}

late FakeWorkspaces fake;

/// Pumps [child] at '/' inside a router that also knows [routes].
Future<void> pumpScreen(WidgetTester tester, Widget child, {String uid = 'own', WorkspaceSummary? active,
    List<GoRoute> routes = const [], List<Override> overrides = const [], void Function(FakeWorkspaces)? setup}) async {
  final router = GoRouter(routes: [
    GoRoute(path: '/', builder: (_, _) => child),
    ...routes,
  ]);
  await tester.pumpWidget(ProviderScope(
    overrides: [
      workspacesClientProvider.overrideWith((ref) {
        fake = FakeWorkspaces(ref);
        setup?.call(fake);
        return fake;
      }),
      authProvider.overrideWith(() => FakeAuth(uid)),
      if (active != null) activeWorkspaceProvider.overrideWith(() => _FixedActive(active)),
      ...overrides,
    ],
    child: MaterialApp.router(
      theme: AuraThemeV2.light,
      localizationsDelegates: AppLocalizations.localizationsDelegates,
      supportedLocales: AppLocalizations.supportedLocales,
      routerConfig: router,
    ),
  ));
  await tester.pumpAndSettle();
}

class _FixedActive extends ActiveWorkspace {
  _FixedActive(this.initial);
  final WorkspaceSummary initial;

  @override
  WorkspaceSummary? build() => initial;

  @override
  Future<void> select(WorkspaceSummary workspace) async => state = workspace;

  @override
  Future<void> clear() async => state = null;
}
