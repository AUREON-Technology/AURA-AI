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
      void Function(FakeWorkspaces)? setup}) async {
    await pumpScreen(
      tester,
      const PlanPage(),
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
    expect(find.text('Plan: free'), findsOneWidget);
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
