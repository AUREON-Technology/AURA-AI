// SaaS P1-F2 Task 3: plan and usage, the web upgrade, and plan-limit prompts.

import 'package:aura_client/features/chat/data/chat_connection_error.dart';
import 'package:aura_client/features/workspaces/data/workspace_models.dart';
import 'package:aura_client/features/workspaces/presentation/plan_limit_card.dart';
import 'package:aura_client/features/workspaces/presentation/plan_page.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'workspace_fakes.dart';

void main() {
  setUp(() => SharedPreferences.setMockInitialValues({}));

  Future<void> pumpPlan(WidgetTester tester, {bool web = true, String role = 'owner', bool teamActive = false,
      void Function(FakeWorkspaces)? setup, Locale? locale}) async {
    await pumpScreen(
      tester,
      const PlanPage(),
      locale: locale,
      active: teamActive ? team(role: role) : null,
      overrides: [canCheckoutHereProvider.overrideWithValue(web)],
      setup: (f) {
        if (!teamActive) f.workspaces = [WorkspaceSummaryCopy.personalAs(role), team()];
        setup?.call(f);
      },
    );
  }

  testWidgets('shows usage and when it resets', (tester) async {
    await pumpPlan(tester, setup: (f) => f.usage = PlanUsage(plan: 'free', seats: 1, messagesUsed: 12,
        messagesAllowed: 50, storageUsed: 0, storageAllowed: 1024, engineer: false, resetsAt: DateTime.utc(2026, 12, 1)));
    expect(find.text('Plan: Free'), findsOneWidget, reason: 'a name, not the server slug (F55)');
    expect(find.text('Messages this month: 12 of 50'), findsOneWidget);
    expect(find.textContaining('Resets on'), findsOneWidget);
    expect(find.text('Engineer agent: Pro and Team plans'), findsOneWidget);
  });

  testWidgets('an owner on the web checks out and sees the Fawry reference', (tester) async {
    await pumpPlan(tester);
    await tester.tap(find.byKey(const Key('plan-buy')));
    await tester.pumpAndSettle();
    expect(fake.calls, contains('checkout:pro:1'));
    expect(find.text('Reference number: 912345678'), findsOneWidget);
    expect(find.text('Amount: EGP 150.00'), findsOneWidget);
  });

  testWidgets('a team owner buys seats', (tester) async {
    await pumpPlan(tester, teamActive: true);
    await tester.enterText(find.byKey(const Key('plan-seats')), '4');
    await tester.tap(find.byKey(const Key('plan-buy')));
    await tester.pumpAndSettle();
    expect(fake.calls, contains('checkout:team:4'));
  });

  testWidgets('the store builds show no purchase and no link out', (tester) async {
    await pumpPlan(tester, web: false);
    expect(find.byKey(const Key('plan-mobile')), findsOneWidget);
    expect(find.byKey(const Key('plan-buy')), findsNothing);
  });

  testWidgets('a member cannot upgrade', (tester) async {
    await pumpPlan(tester, teamActive: true, role: 'member');
    expect(find.byKey(const Key('plan-owner-only')), findsOneWidget);
    expect(find.byKey(const Key('plan-buy')), findsNothing);
  });

  testWidgets('billing off keeps the usage and hides the upgrade', (tester) async {
    await pumpPlan(tester, setup: (f) => f.billingFailure = apiError(503, 'billing_unavailable', 'billing is not enabled'));
    expect(find.byKey(const Key('plan-usage')), findsOneWidget);
    expect(find.byKey(const Key('plan-billing-off')), findsOneWidget);
    expect(find.byKey(const Key('plan-buy')), findsNothing);
  });

  testWidgets('receipts list paid payments', (tester) async {
    await pumpPlan(tester, setup: (f) => f.billingStatus = BillingStatus(
          subscription: SubscriptionView(plan: 'pro', seats: 1, status: 'active', periodEnd: DateTime.utc(2026, 12, 18)),
          payments: [PaymentView(id: 'p1', plan: 'pro', amountPiastres: 15000, status: 'paid', paidAt: DateTime.utc(2026, 11, 18))],
        ));
    expect(find.text('EGP 150.00'), findsOneWidget);
    expect(find.byKey(const Key('plan-cancel')), findsOneWidget);
  });

  // F55: the plan and the subscription status were the server's slugs ("free", "past_due"),
  // in English words inside the Arabic UI too.
  testWidgets('plan and subscription status are named in the UI language', (tester) async {
    await pumpPlan(
      tester,
      locale: const Locale('ar'),
      setup: (f) => f
        ..usage = PlanUsage(plan: 'free', seats: 1, messagesUsed: 1, messagesAllowed: 50, storageUsed: 0,
            storageAllowed: 1024, engineer: false)
        ..billingStatus = BillingStatus(
          subscription: SubscriptionView(plan: 'pro', seats: 1, status: 'past_due', periodEnd: DateTime.utc(2026, 12, 18)),
          payments: const [],
        ),
    );
    expect(find.text('الباقة: المجانية'), findsOneWidget);
    expect(find.text('الاشتراك: الدفع متأخر'), findsOneWidget);
    expect(find.textContaining('past_due'), findsNothing);
    expect(find.textContaining('free'), findsNothing);
  });

  testWidgets('an English subscription status reads as words', (tester) async {
    await pumpPlan(tester, setup: (f) => f.billingStatus = BillingStatus(
          subscription: SubscriptionView(plan: 'pro', seats: 1, status: 'grace', periodEnd: DateTime.utc(2026, 12, 18)),
          payments: const [],
        ));
    expect(find.text('Subscription: In grace period'), findsOneWidget);
  });

  testWidgets('seats that are not a whole number are refused, not bought as one', (tester) async {
    await pumpPlan(tester, teamActive: true);
    for (final typed in ['abc', '0', '2.5']) {
      await tester.enterText(find.byKey(const Key('plan-seats')), typed);
      await tester.tap(find.byKey(const Key('plan-buy')));
      await tester.pumpAndSettle();
      expect(find.text('Enter a whole number of seats, 1 or more.'), findsOneWidget, reason: typed);
    }
    expect(fake.calls.where((c) => c.startsWith('checkout')), isEmpty);

    await tester.enterText(find.byKey(const Key('plan-seats')), '3');
    await tester.tap(find.byKey(const Key('plan-buy')));
    await tester.pumpAndSettle();
    expect(fake.calls, contains('checkout:team:3'));
    expect(find.text('Enter a whole number of seats, 1 or more.'), findsNothing);
  });

  test('a 402 plan_limit becomes a plan-limit row, not a connection error', () {
    final text = chatPlanLimitText(apiError(402, 'plan_limit', "this month's messages are used",
        {'limit': 'messages', 'plan': 'free', 'used': 50, 'allowed': 50}));
    expect(isChatPlanLimitText(text), isTrue);
    expect(isChatConnectionErrorText(text), isFalse);
  });

  testWidgets('the plan-limit card offers See plans', (tester) async {
    await pumpScreen(tester, const Scaffold(body: PlanLimitCard(text: "You've used this month's messages.")),
        routes: [GoRoute(path: '/plan', builder: (_, _) => const Scaffold(body: Text('PLAN PAGE')))]);
    await tester.tap(find.text('See plans'));
    await tester.pumpAndSettle();
    expect(find.text('PLAN PAGE'), findsOneWidget);
  });
}
