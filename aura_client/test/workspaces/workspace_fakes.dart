// Shared fakes for the workspace screens' tests (SaaS P1-F2).

import 'dart:async';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/network/aura_api_error.dart';
import 'package:aura_client/core/workspaces/active_workspace.dart';
import 'package:aura_client/features/auth/presentation/auth_provider.dart';
import 'package:aura_client/features/workspaces/data/store_gateway.dart';
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
  Object? billingFailure;
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
  Future<BillingStatus> billing(String id) async {
    if (billingFailure != null) throw billingFailure!;
    return _answer('billing:$id', billingStatus);
  }
  @override
  Future<Checkout> checkout(String id, String plan, int seats) => _answer('checkout:$plan:$seats',
      Checkout(paymentId: 'p1', amountPiastres: 15000, referenceNumber: '912345678', expiresAt: DateTime.utc(2026, 11, 21)));
  @override
  Future<void> cancel(String id) => _answer('cancel:$id', null);
  @override
  Future<String> storeToken(String id) => _answer('store-token:$id', 'tok-1');
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

/// A store whose purchase sheet the test answers by calling [emit].
class FakeStore implements StoreGateway {
  FakeStore({this.kind = StoreKind.apple, this.price = 'EGP 149.99', this.known = true, this.opens = true});

  @override
  final StoreKind? kind;
  final String price;
  final bool known;

  /// False: the store refuses to open its sheet, as Play's `launchBillingFlow` can.
  final bool opens;
  final calls = <String>[];
  final completed = <String>[];
  final _updates = StreamController<List<StoreUpdate>>.broadcast();

  void emit(StoreUpdateKind status, {String product = 'aura.pro.monthly', bool needsCompletion = true}) =>
      _updates.add([StoreUpdate(kind: status, productId: product, needsCompletion: needsCompletion)]);

  @override
  Future<bool> available() async => true;
  @override
  Future<StoreProduct?> product(String id) async => known ? StoreProduct(id: id, price: price) : null;
  @override
  Future<void> buy(StoreProduct product, String bindingToken) async {
    calls.add('buy:${product.id}:$bindingToken');
    if (!opens) throw const StoreSheetRefused();
  }
  @override
  Stream<List<StoreUpdate>> get updates => _updates.stream;
  @override
  Future<void> complete(StoreUpdate update) async => completed.add(update.productId);
  @override
  Future<void> manage(String productId) async => calls.add('manage:$productId');
}

/// Pumps [child] at '/' inside a router that also knows [routes].
Future<void> pumpScreen(WidgetTester tester, Widget child, {String uid = 'own', WorkspaceSummary? active,
    List<GoRoute> routes = const [], List<Override> overrides = const [], void Function(FakeWorkspaces)? setup,
    StoreGateway store = const NoStoreGateway(), Locale? locale}) async {
  final router = GoRouter(routes: [
    GoRoute(path: '/', builder: (_, _) => child),
    ...routes,
  ]);
  await tester.pumpWidget(ProviderScope(
    key: UniqueKey(), // a fresh container per pump: tests vary the overrides
    overrides: [
      workspacesClientProvider.overrideWith((ref) {
        fake = FakeWorkspaces(ref);
        setup?.call(fake);
        return fake;
      }),
      authProvider.overrideWith(() => FakeAuth(uid)),
      // Tests run as Android by default: no real store unless a test passes one.
      storeGatewayProvider.overrideWithValue(store),
      if (active != null) activeWorkspaceProvider.overrideWith(() => _FixedActive(active)),
      ...overrides,
    ],
    child: MaterialApp.router(
      theme: AuraThemeV2.light,
      locale: locale,
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


extension WorkspaceSummaryCopy on WorkspaceSummary {
  static WorkspaceSummary personalAs(String role) =>
      WorkspaceSummary(id: personal.id, kind: 'personal', name: personal.name, role: role);
}
