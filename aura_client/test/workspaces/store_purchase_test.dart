// SaaS P1-F2b: buying Pro in the iOS and Android apps through the store.

import 'package:aura_client/features/workspaces/data/store_gateway.dart';
import 'package:aura_client/features/workspaces/data/workspace_models.dart';
import 'package:aura_client/features/workspaces/presentation/plan_page.dart';
import 'package:aura_client/features/workspaces/presentation/store_purchase.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'workspace_fakes.dart';

const _apple = {'apple': ['aura.pro.monthly']};

SubscriptionView _sub(String provider, String status) =>
    SubscriptionView(plan: 'pro', seats: 1, status: status, provider: provider, periodEnd: DateTime.utc(2026, 12, 18));

/// Store anti-steering: no iOS or Android screen mentions another way to pay.
void _expectNoOtherWayToPay() {
  for (final word in const ['web', 'Web', 'Fawry', 'fawry', 'browser', 'website']) {
    expect(find.textContaining(word), findsNothing, reason: 'mentions "$word"');
  }
}

void main() {
  late FakeStore store;

  Future<void> pumpPlan(WidgetTester tester, {BillingStatus billing = const BillingStatus(storeProducts: _apple),
      bool teamActive = false, FakeStore? using, bool web = false}) async {
    store = using ?? FakeStore();
    await pumpScreen(
      tester,
      const PlanPage(),
      active: teamActive ? team() : null,
      store: web ? const NoStoreGateway() : store,
      overrides: [
        canCheckoutHereProvider.overrideWithValue(web),
        storePollProvider.overrideWithValue((interval: const Duration(seconds: 1), attempts: 3)),
      ],
      setup: (f) {
        if (!teamActive) f.workspaces = [personal, team()];
        f.billingStatus = billing;
      },
    );
  }

  testWidgets('an owner buys Pro in the app with the binding token', (tester) async {
    await pumpPlan(tester);
    expect(find.text('Upgrade to Pro · EGP 149.99'), findsOneWidget);
    await tester.tap(find.byKey(const Key('plan-store-buy')));
    await tester.pump();
    expect(fake.calls, contains('store-token:ws-me'));
    expect(store.calls, ['buy:aura.pro.monthly:tok-1']);

    // The store's notification has reached AURA by the first check.
    fake.billingStatus = BillingStatus(subscription: _sub('apple', 'active'), storeProducts: _apple);
    store.emit(StoreUpdateKind.purchased);
    await tester.pump();
    expect(find.byKey(const Key('plan-store-waiting')), findsOneWidget);
    expect(store.completed, ['aura.pro.monthly']);
    await tester.pump(const Duration(seconds: 1));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('plan-store-manage')), findsOneWidget);
    expect(find.byKey(const Key('plan-store-buy')), findsNothing);
  });

  testWidgets('a purchase whose notification is late says so and offers no second buy', (tester) async {
    await pumpPlan(tester);
    final loads = fake.calls.where((c) => c == 'billing:ws-me').length;
    await tester.tap(find.byKey(const Key('plan-store-buy')));
    await tester.pump();
    store.emit(StoreUpdateKind.purchased);
    await tester.pump();
    for (var i = 0; i < 4; i++) {
      await tester.pump(const Duration(seconds: 1));
    }
    await tester.pumpAndSettle();
    expect(find.text('Purchase received. Your plan will update in a few minutes.'), findsOneWidget);
    expect(find.byKey(const Key('plan-store-buy')), findsNothing);
    expect(find.textContaining("didn't go through"), findsNothing);
    expect(fake.calls.where((c) => c == 'billing:ws-me').length - loads, 3); // every configured check
  });

  testWidgets('cancelling the store sheet returns to the buy button', (tester) async {
    await pumpPlan(tester);
    await tester.tap(find.byKey(const Key('plan-store-buy')));
    await tester.pump();
    store.emit(StoreUpdateKind.canceled, needsCompletion: false);
    await tester.pumpAndSettle();
    final button = find.byKey(const Key('plan-store-buy'));
    expect(button, findsOneWidget);
    expect(find.byKey(const Key('plan-store-failed')), findsNothing);
    await tester.tap(button);
    await tester.pump();
    expect(store.calls.length, 2); // enabled again: a second sheet opens
  });

  testWidgets('a redelivered transaction is completed with no page open', (tester) async {
    final redelivered = FakeStore();
    final container = ProviderContainer(overrides: [storeGatewayProvider.overrideWithValue(redelivered)]);
    addTearDown(container.dispose);
    container.read(storePurchaseListenerProvider);
    redelivered.emit(StoreUpdateKind.purchased);
    redelivered.emit(StoreUpdateKind.canceled, needsCompletion: false);
    await tester.pump();
    expect(redelivered.completed, ['aura.pro.monthly']);
  });

  testWidgets('store-token refused: the failure shows and the store is never opened', (tester) async {
    await pumpPlan(tester);
    fake.failWith = apiError(403, 'authorization_error', 'verify your email address before buying a plan');
    await tester.tap(find.byKey(const Key('plan-store-buy')));
    await tester.pumpAndSettle();
    expect(store.calls, isEmpty);
    expect(find.byKey(const Key('plan-store-failed')), findsOneWidget);
    expect(find.byKey(const Key('plan-store-buy')), findsOneWidget);
  });

  testWidgets('a live Fawry subscription offers no store purchase', (tester) async {
    await pumpPlan(tester, billing: BillingStatus(subscription: _sub('fawry', 'grace'), storeProducts: _apple));
    expect(find.byKey(const Key('plan-fawry-active')), findsOneWidget);
    expect(find.byKey(const Key('plan-store-buy')), findsNothing);
    expect(find.byKey(const Key('plan-cancel')), findsNothing);
  });

  testWidgets('a store subscription shows Manage on its own store', (tester) async {
    await pumpPlan(tester, billing: BillingStatus(subscription: _sub('apple', 'canceled'), storeProducts: _apple));
    await tester.tap(find.byKey(const Key('plan-store-manage')));
    await tester.pump();
    expect(store.calls, ['manage:aura.pro.monthly']);
    expect(find.byKey(const Key('plan-store-buy')), findsNothing);
  });

  testWidgets('a Play subscription on an iPhone is managed on the other device', (tester) async {
    await pumpPlan(tester, billing: BillingStatus(subscription: _sub('google', 'active'), storeProducts: _apple));
    expect(find.byKey(const Key('plan-store-other')), findsOneWidget);
    expect(find.byKey(const Key('plan-store-manage')), findsNothing);
  });

  testWidgets('a store subscription on the web offers no Fawry buy or cancel', (tester) async {
    await pumpPlan(tester, web: true, billing: BillingStatus(subscription: _sub('google', 'active'), storeProducts: _apple));
    expect(find.byKey(const Key('plan-store-elsewhere')), findsOneWidget);
    expect(find.byKey(const Key('plan-buy')), findsNothing);
    expect(find.byKey(const Key('plan-cancel')), findsNothing);
  });

  testWidgets('a team on mobile cannot buy in the app, and nothing points elsewhere', (tester) async {
    await pumpPlan(tester, teamActive: true);
    expect(find.text("Team plans can't be bought in this app."), findsOneWidget);
    expect(find.byKey(const Key('plan-store-buy')), findsNothing);
    _expectNoOtherWayToPay();
  });

  testWidgets('no store product configured, or unknown to the store: no purchase', (tester) async {
    await pumpPlan(tester, billing: const BillingStatus());
    expect(find.byKey(const Key('plan-billing-off')), findsOneWidget);
    await pumpPlan(tester, using: FakeStore(known: false));
    expect(find.byKey(const Key('plan-billing-off')), findsOneWidget);
    expect(find.byKey(const Key('plan-store-buy')), findsNothing);
  });

  testWidgets('Android: a cancelled or failed sheet arrives with no product id and is still handled', (tester) async {
    await pumpPlan(tester, using: FakeStore(kind: StoreKind.google),
        billing: const BillingStatus(storeProducts: {'google': ['aura_pro']}));
    await tester.tap(find.byKey(const Key('plan-store-buy')));
    await tester.pump();
    store.emit(StoreUpdateKind.canceled, product: '', needsCompletion: false);
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('plan-store-buy')));
    await tester.pump();
    expect(store.calls.length, 2, reason: 'the button works again after the cancel');
    store.emit(StoreUpdateKind.error, product: '', needsCompletion: false);
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('plan-store-failed')), findsOneWidget);
  });

  testWidgets('the store refusing to open the sheet is a failure, not a stuck spinner', (tester) async {
    await pumpPlan(tester, using: FakeStore(opens: false));
    await tester.tap(find.byKey(const Key('plan-store-buy')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('plan-store-failed')), findsOneWidget);
    expect(find.text("The purchase didn't go through."), findsOneWidget);
  });

  testWidgets('an approval that arrives later (Ask to Buy) still activates', (tester) async {
    await pumpPlan(tester);
    await tester.tap(find.byKey(const Key('plan-store-buy')));
    await tester.pump();
    store.emit(StoreUpdateKind.pending, needsCompletion: false);
    await tester.pump();
    expect(find.byKey(const Key('plan-store-pending')), findsOneWidget);
    fake.billingStatus = BillingStatus(subscription: _sub('apple', 'active'), storeProducts: _apple);
    store.emit(StoreUpdateKind.purchased);
    await tester.pump();
    await tester.pump(const Duration(seconds: 1));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('plan-store-manage')), findsOneWidget);
  });

  testWidgets('a Fawry reference still payable blocks a store purchase', (tester) async {
    await pumpPlan(tester, billing: BillingStatus(storeProducts: _apple, payments: [
      PaymentView(id: 'p1', plan: 'pro', amountPiastres: 15000, status: 'pending',
          expiresAt: DateTime.now().toUtc().add(const Duration(days: 1))),
    ]));
    expect(find.byKey(const Key('plan-fawry-active')), findsOneWidget);
    expect(find.byKey(const Key('plan-store-buy')), findsNothing);
    _expectNoOtherWayToPay();
  });

  testWidgets('an expired Fawry reference does not block a store purchase', (tester) async {
    await pumpPlan(tester, billing: BillingStatus(storeProducts: _apple, payments: [
      PaymentView(id: 'p1', plan: 'pro', amountPiastres: 15000, status: 'pending',
          expiresAt: DateTime.utc(2020)),
    ]));
    expect(find.byKey(const Key('plan-store-buy')), findsOneWidget);
  });
}
