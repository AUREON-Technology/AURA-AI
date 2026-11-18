// Buying Pro inside the iOS and Android apps (SaaS P1-F2b).
//
// AURA, not the app, decides the plan: the store notifies the backend, which activates it (Plan
// D2). The app only asks for a binding token, opens the store's sheet with it, finishes the
// transaction, and waits for `GET …/billing` to show the store's subscription.

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/design_system/design_system.dart';
import '../../../core/network/aura_failure.dart';
import '../../../core/workspaces/active_workspace.dart';
import '../../../l10n/l10n.dart';
import '../data/store_gateway.dart';
import '../data/workspace_models.dart';
import '../data/workspaces_client.dart';
import 'plan_page.dart';
import 'workspace_providers.dart';

/// How long to wait for the store's notification to reach AURA: [attempts] checks, [interval] apart.
typedef StorePoll = ({Duration interval, int attempts});
final storePollProvider = Provider<StorePoll>((ref) => (interval: const Duration(seconds: 3), attempts: 20));

/// Finishes every transaction the store hands the app, from app start on, even with no plan page
/// open: StoreKit redelivers an unfinished one on each launch, and Play refunds a purchase nobody
/// acknowledged within three days. AURA acknowledges Play purchases too; finishing twice is harmless.
final storePurchaseListenerProvider = Provider<void>((ref) {
  final gateway = ref.watch(storeGatewayProvider);
  if (gateway.kind == null) return;
  final subscription = gateway.updates.listen((updates) async {
    for (final update in updates) {
      if (update.needsCompletion) {
        try {
          await gateway.complete(update);
        } catch (_) {
          // The store redelivers it on the next launch.
        }
      }
    }
  });
  ref.onDispose(subscription.cancel);
});

/// The store's product that sells Pro on this platform, or null: no store here, the store is
/// unavailable, AURA lists no product for it, or the store does not know the product.
final storeProProductProvider = FutureProvider.autoDispose.family<StoreProduct?, String>((ref, workspaceId) async {
  final gateway = ref.watch(storeGatewayProvider);
  final kind = gateway.kind;
  if (kind == null) return null;
  final status = await ref.watch(billingStatusProvider(workspaceId).future);
  final ids = status?.storeProducts[kind.key] ?? const <String>[];
  if (ids.isEmpty || !await gateway.available()) return null;
  return gateway.product(ids.first);
});

enum StorePhase { idle, buying, waiting, pending, failed }

@immutable
class StorePurchaseState {
  const StorePurchaseState(this.phase, [this.error]);
  final StorePhase phase;
  final Object? error;

  bool get busy => phase == StorePhase.buying || phase == StorePhase.waiting;
}

class StorePurchaseController extends Notifier<StorePurchaseState> {
  StorePurchaseController(this.workspaceId);
  final String workspaceId;

  StreamSubscription<List<StoreUpdate>>? _updates;
  String? _productId;
  bool _disposed = false;

  @override
  StorePurchaseState build() {
    ref.watch(storePurchaseListenerProvider);
    ref.onDispose(() {
      _disposed = true;
      _updates?.cancel();
    });
    return const StorePurchaseState(StorePhase.idle);
  }

  Future<void> buy(StoreProduct product) async {
    if (state.busy) return;
    final gateway = ref.read(storeGatewayProvider);
    state = const StorePurchaseState(StorePhase.buying);
    _productId = product.id;
    _updates ??= gateway.updates.listen(_onUpdates);
    try {
      final token = await ref.read(workspacesClientProvider).storeToken(workspaceId);
      await gateway.buy(product, token);
    } catch (error) {
      if (!_disposed) state = StorePurchaseState(StorePhase.failed, error);
    }
  }

  void _onUpdates(List<StoreUpdate> updates) {
    for (final update in updates) {
      if (update.productId != _productId || _disposed) continue;
      switch (update.kind) {
        case StoreUpdateKind.purchased:
        case StoreUpdateKind.restored:
          if (state.phase == StorePhase.buying) unawaited(_awaitActivation());
        case StoreUpdateKind.pending:
          state = const StorePurchaseState(StorePhase.pending);
        case StoreUpdateKind.canceled:
          state = const StorePurchaseState(StorePhase.idle);
        case StoreUpdateKind.error:
          state = StorePurchaseState(StorePhase.failed, update.error);
      }
    }
  }

  /// Waits for AURA to have the store's subscription. A late notification is not an error: the
  /// store has the money, and the plan follows when the notification arrives.
  Future<void> _awaitActivation() async {
    state = const StorePurchaseState(StorePhase.waiting);
    final store = ref.read(storeGatewayProvider).kind?.key;
    final poll = ref.read(storePollProvider);
    final client = ref.read(workspacesClientProvider);
    for (var i = 0; i < poll.attempts; i++) {
      await Future<void>.delayed(poll.interval);
      if (_disposed) return;
      try {
        final sub = (await client.billing(workspaceId)).subscription;
        if (sub != null && sub.provider == store && sub.status == 'active') {
          if (_disposed) return;
          ref.invalidate(billingStatusProvider(workspaceId));
          ref.invalidate(planUsageProvider(workspaceId));
          state = const StorePurchaseState(StorePhase.idle);
          return;
        }
      } catch (_) {
        // Keep waiting: a dropped connection says nothing about the purchase.
      }
    }
    if (!_disposed) state = const StorePurchaseState(StorePhase.pending);
  }
}

final storePurchaseProvider = NotifierProvider.autoDispose.family<StorePurchaseController, StorePurchaseState, String>(
  StorePurchaseController.new,
);

/// The upgrade area on iOS and Android for a personal workspace's Owner with no live Fawry
/// subscription. It never mentions another way to pay.
class StoreUpgrade extends ConsumerWidget {
  const StoreUpgrade({super.key, required this.workspace});

  final WorkspaceSummary workspace;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final l10n = context.l10n;
    final purchase = ref.watch(storePurchaseProvider(workspace.id));
    final product = ref.watch(storeProProductProvider(workspace.id));
    switch (purchase.phase) {
      case StorePhase.waiting:
        return Text(l10n.planStoreWaiting, key: const Key('plan-store-waiting'));
      case StorePhase.pending:
        return Text(l10n.planStorePending, key: const Key('plan-store-pending'));
      case StorePhase.idle:
      case StorePhase.buying:
      case StorePhase.failed:
        break;
    }
    return product.when(
      loading: () => const Center(child: CircularProgressIndicator()),
      error: (_, _) => Text(l10n.planBillingOff, key: const Key('plan-billing-off')),
      data: (p) {
        if (p == null) return Text(l10n.planBillingOff, key: const Key('plan-billing-off'));
        return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          if (purchase.phase == StorePhase.failed) ...[
            Text(
              _failureText(context, purchase.error),
              key: const Key('plan-store-failed'),
            ),
            const SizedBox(height: AuraSpacing.sm),
          ],
          AuraButton(
            key: const Key('plan-store-buy'),
            label: l10n.planStoreBuy(p.price),
            loading: purchase.phase == StorePhase.buying,
            onPressed: () => ref.read(storePurchaseProvider(workspace.id).notifier).buy(p),
          ),
        ]);
      },
    );
  }
}

/// The store's own error text is not shown (it may be English on an Arabic phone, or technical); a
/// refusal from AURA (unverified email, too many attempts) is, in AuraFailure's words.
String _failureText(BuildContext context, Object? error) {
  if (error == null || error is String) return context.l10n.planStoreFailed;
  final failure = AuraFailure.from(error);
  return '${failure.title}. ${failure.message}';
}

/// A live store subscription on a phone: the store's own management for its own store.
class StoreManage extends ConsumerWidget {
  const StoreManage({super.key, required this.subscription, required this.storeProducts});

  final SubscriptionView subscription;
  final Map<String, List<String>> storeProducts;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final l10n = context.l10n;
    final gateway = ref.watch(storeGatewayProvider);
    if (gateway.kind?.key != subscription.provider) {
      return Text(l10n.planStoreOtherDevice, key: const Key('plan-store-other'));
    }
    final ids = storeProducts[subscription.provider] ?? const <String>[];
    return AuraButton(
      key: const Key('plan-store-manage'),
      label: l10n.planStoreManage,
      variant: AuraButtonVariant.ghost,
      onPressed: () async {
        try {
          await gateway.manage(ids.isEmpty ? '' : ids.first);
        } catch (error) {
          if (context.mounted) showFailure(context, error);
        }
      },
    );
  }
}
