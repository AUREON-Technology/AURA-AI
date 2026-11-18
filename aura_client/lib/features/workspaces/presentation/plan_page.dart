import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';

import '../../../core/design_system/design_system.dart';
import '../../../core/network/aura_api_error.dart';
import '../../../core/network/aura_failure.dart';
import '../../../core/network/aura_failure_view.dart';
import '../../../core/workspaces/active_workspace.dart';
import '../../../l10n/l10n.dart';
import '../data/store_gateway.dart';
import '../data/workspace_models.dart';
import '../data/workspaces_client.dart';
import 'store_purchase.dart';
import 'workspace_providers.dart';

/// A macOS build distributed outside the Mac App Store (`--dart-define=DIRECT_DISTRIBUTION=true`).
const _directDistribution = bool.fromEnvironment('DIRECT_DISTRIBUTION');

/// Whether this build may sell a plan with Fawry: the web, Windows and Linux builds, and a macOS
/// build only when distributed directly. App-store builds (iOS, Android, Mac App Store) may not
/// steer to outside payment: iOS and Android sell through their store instead (F2b), and the Mac
/// App Store build sells nothing in phase 1.
final canCheckoutHereProvider = Provider<bool>((ref) =>
    kIsWeb ||
    defaultTargetPlatform == TargetPlatform.windows ||
    defaultTargetPlatform == TargetPlatform.linux ||
    (defaultTargetPlatform == TargetPlatform.macOS && _directDistribution));

final planUsageProvider = FutureProvider.autoDispose.family<PlanUsage, String>(
  (ref, id) => ref.read(workspacesClientProvider).plan(id),
);

/// Null when billing is off on this server (503 `billing_unavailable`) or the caller may not see
/// it (a Member): the plan and usage still show.
final billingStatusProvider = FutureProvider.autoDispose.family<BillingStatus?, String>((ref, id) async {
  try {
    return await ref.read(workspacesClientProvider).billing(id);
  } on AuraApiError catch (e) {
    if (e.statusCode == 503 || e.statusCode == 403) return null;
    rethrow;
  }
});

String _date(BuildContext context, DateTime? at) =>
    at == null ? '-' : DateFormat.yMMMd(Localizations.localeOf(context).toString()).format(at.toLocal());

String _bytes(int n) {
  if (n >= 1024 * 1024 * 1024) return '${(n / (1024 * 1024 * 1024)).toStringAsFixed(1)} GB';
  if (n >= 1024 * 1024) return '${(n / (1024 * 1024)).toStringAsFixed(1)} MB';
  if (n >= 1024) return '${(n / 1024).toStringAsFixed(0)} KB';
  return '$n B';
}

class PlanPage extends ConsumerWidget {
  const PlanPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final current = ref.watch(currentWorkspaceProvider);
    return Scaffold(
      appBar: AppBar(title: Text(context.l10n.planTitle)),
      body: current.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (error, _) => AuraFailureView(failure: AuraFailure.from(error)),
        data: (ws) => ws == null ? const SizedBox.shrink() : _PlanBody(workspace: ws),
      ),
    );
  }
}

class _PlanBody extends ConsumerWidget {
  const _PlanBody({required this.workspace});

  final WorkspaceSummary workspace;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final l10n = context.l10n;
    final usage = ref.watch(planUsageProvider(workspace.id));
    final billing = ref.watch(billingStatusProvider(workspace.id));
    return ListView(padding: const EdgeInsets.all(AuraSpacing.lg), children: [
      Text(workspaceName(context, workspace), style: Theme.of(context).textTheme.headlineSmall),
      const SizedBox(height: AuraSpacing.md),
      usage.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (error, _) => AuraFailureView(failure: AuraFailure.from(error), compact: true,
            onRetry: () => ref.invalidate(planUsageProvider(workspace.id))),
        data: (u) => AuraCard(
          key: const Key('plan-usage'),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(l10n.planCurrent(u.plan), style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: AuraSpacing.sm),
            Text(l10n.planMessages('${u.messagesUsed}', '${u.messagesAllowed}')),
            if (u.resetsAt != null) Text(l10n.planResets(_date(context, u.resetsAt))),
            Text(l10n.planStorage(_bytes(u.storageUsed), _bytes(u.storageAllowed))),
            Text(u.engineer ? l10n.planEngineerIncluded : l10n.planEngineerNotIncluded),
            if (workspace.isTeam) Text(l10n.planSeats('${u.seatLimit ?? u.seats}')),
          ]),
        ),
      ),
      const SizedBox(height: AuraSpacing.lg),
      billing.when(
        loading: () => const SizedBox.shrink(),
        error: (error, _) => AuraFailureView(failure: AuraFailure.from(error), compact: true),
        data: (status) => _BillingSection(workspace: workspace, status: status),
      ),
    ]);
  }
}

class _BillingSection extends ConsumerWidget {
  const _BillingSection({required this.workspace, required this.status});

  final WorkspaceSummary workspace;
  final BillingStatus? status;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final l10n = context.l10n;
    final sub = status?.subscription;
    final children = <Widget>[];
    if (sub != null) {
      children.add(AuraCard(
        key: const Key('plan-subscription'),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(l10n.planSubscriptionStatus(sub.status)),
          if (sub.periodEnd != null) Text(l10n.planPaidThrough(_date(context, sub.periodEnd))),
          if (sub.graceUntil != null) Text(l10n.planGraceUntil(_date(context, sub.graceUntil))),
        ]),
      ));
    }
    final paid = [for (final p in status?.payments ?? const <PaymentView>[]) if (p.status == 'paid') p];
    if (paid.isNotEmpty) {
      children.add(AuraSectionHeader(title: l10n.planPaymentsSection));
      for (final p in paid) {
        children.add(AuraListTile(
          icon: Icons.receipt_long_outlined,
          title: formatEgp(p.amountPiastres),
          subtitle: _date(context, p.paidAt ?? p.createdAt),
        ));
      }
    }
    children.add(const SizedBox(height: AuraSpacing.lg));
    children.add(AuraSectionHeader(title: l10n.planUpgradeSection));
    if (!workspace.isOwner) {
      children.add(Text(l10n.planOwnerOnly, key: const Key('plan-owner-only')));
    } else if (status == null) {
      children.add(Text(l10n.planBillingOff, key: const Key('plan-billing-off')));
    } else if (ref.watch(storeGatewayProvider).kind != null) {
      children.add(_storeArea(context, status!));
    } else if (!ref.watch(canCheckoutHereProvider)) {
      children.add(Text(l10n.planBillingOff, key: const Key('plan-mobile')));
    } else if (sub != null && sub.fromStore && sub.live) {
      // Bought in a store: only the store can change or cancel it, and a Fawry purchase on top
      // would charge twice.
      children.add(Text(l10n.planStoreElsewhere, key: const Key('plan-store-elsewhere')));
    } else {
      children.add(_Upgrade(workspace: workspace, current: sub));
    }
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: children);
  }

  /// iOS and Android: the store's purchase for a personal workspace, never a mention of another
  /// way to pay (store anti-steering rules).
  Widget _storeArea(BuildContext context, BillingStatus status) {
    final l10n = context.l10n;
    final sub = status.subscription;
    if (workspace.isTeam) return Text(l10n.planTeamNotInApp, key: const Key('plan-mobile'));
    if (sub != null && sub.live && sub.fromStore) {
      return StoreManage(subscription: sub, storeProducts: status.storeProducts);
    }
    if (sub != null && sub.provider == 'fawry' && const {'active', 'past_due', 'grace'}.contains(sub.status)) {
      return Text(l10n.planFawryActive, key: const Key('plan-fawry-active'));
    }
    return StoreUpgrade(workspace: workspace);
  }
}

class _Upgrade extends ConsumerStatefulWidget {
  const _Upgrade({required this.workspace, required this.current});

  final WorkspaceSummary workspace;
  final SubscriptionView? current;

  @override
  ConsumerState<_Upgrade> createState() => _UpgradeState();
}

class _UpgradeState extends ConsumerState<_Upgrade> {
  late final _seats = TextEditingController(text: '${widget.current?.seats ?? 2}');
  bool _busy = false;

  @override
  void dispose() {
    _seats.dispose();
    super.dispose();
  }

  Future<void> _checkout(String plan, int seats) async {
    setState(() => _busy = true);
    Checkout? result;
    try {
      result = await ref.read(workspacesClientProvider).checkout(widget.workspace.id, plan, seats);
    } catch (error) {
      if (mounted) showFailure(context, error);
    } finally {
      // Stop the spinner before the result opens: the reference stays on screen as long as the
      // person needs it.
      if (mounted) setState(() => _busy = false);
    }
    if (result == null || !mounted) return;
    ref.invalidate(billingStatusProvider(widget.workspace.id));
    await showCheckoutResult(context, result);
  }

  Future<void> _cancel() async {
    final l10n = context.l10n;
    if (!await confirm(context, title: l10n.planCancel, body: l10n.planCancelConfirm, action: l10n.planCancel, destructive: true)) {
      return;
    }
    try {
      await ref.read(workspacesClientProvider).cancel(widget.workspace.id);
      ref.invalidate(billingStatusProvider(widget.workspace.id));
    } catch (error) {
      if (mounted) showFailure(context, error);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final live = widget.current != null && const {'active', 'past_due', 'grace'}.contains(widget.current!.status);
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      if (widget.workspace.isTeam) ...[
        AuraTextField(
          key: const Key('plan-seats'),
          controller: _seats,
          label: l10n.planSeatsLabel,
          keyboardType: TextInputType.number,
        ),
        const SizedBox(height: AuraSpacing.sm),
        AuraButton(
          key: const Key('plan-buy'),
          label: l10n.planUpgradeTeam,
          loading: _busy,
          onPressed: () => _checkout('team', int.tryParse(_seats.text.trim()) ?? 1),
        ),
      ] else
        AuraButton(key: const Key('plan-buy'), label: l10n.planUpgradePro, loading: _busy, onPressed: () => _checkout('pro', 1)),
      if (live) ...[
        const SizedBox(height: AuraSpacing.sm),
        AuraButton(key: const Key('plan-cancel'), label: l10n.planCancel, variant: AuraButtonVariant.ghost, onPressed: _cancel),
      ],
    ]);
  }
}

/// The Fawry reference to pay, its amount and deadline, and how to pay it.
Future<void> showCheckoutResult(BuildContext context, Checkout checkout) {
  final l10n = context.l10n;
  final reference = checkout.referenceNumber;
  return showDialog<void>(
    context: context,
    builder: (dialogContext) => AuraDialog(
      title: l10n.checkoutTitle,
      cancelLabel: MaterialLocalizations.of(dialogContext).closeButtonLabel,
      child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
        if (reference != null)
          Row(children: [
            Expanded(child: SelectableText(l10n.checkoutReference(reference), key: const Key('checkout-reference'))),
            IconButton(
              icon: const Icon(Icons.copy),
              tooltip: MaterialLocalizations.of(dialogContext).copyButtonLabel,
              onPressed: () => Clipboard.setData(ClipboardData(text: reference)),
            ),
          ]),
        Text(l10n.checkoutAmount(formatEgp(checkout.amountPiastres))),
        if (checkout.expiresAt != null) Text(l10n.checkoutExpires(_date(dialogContext, checkout.expiresAt))),
        const SizedBox(height: AuraSpacing.sm),
        Text(l10n.checkoutHowTo),
      ]),
    ),
  );
}
