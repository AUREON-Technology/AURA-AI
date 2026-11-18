// In-app purchase through the App Store and Google Play (SaaS P1-F2b).
//
// The app never sends a receipt to AURA. It hands the store a binding token AURA issued
// (`POST …/billing/store-token`). The plugin passes `PurchaseParam.applicationUserName` to
// StoreKit 2 as `appAccountToken` and to Play Billing as `obfuscatedAccountId`. The store then
// notifies AURA, which activates the plan (docs/deployment/billing-stores.md). Everything
// store-specific stays behind [StoreGateway], so the screens are tested against a fake.

import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:in_app_purchase/in_app_purchase.dart';
import 'package:in_app_purchase_android/in_app_purchase_android.dart';
import 'package:in_app_purchase_storekit/in_app_purchase_storekit.dart';
import 'package:url_launcher/url_launcher.dart';

enum StoreKind {
  apple,
  google;

  /// The key AURA uses for this store (`store_products`, a subscription's `provider`).
  String get key => name;
}

@immutable
class StoreProduct {
  const StoreProduct({required this.id, required this.price, this.raw});
  final String id;

  /// The store's own localized price, such as `EGP 149.99`.
  final String price;

  /// The plugin's `ProductDetails`, which [StoreGateway.buy] needs back.
  final Object? raw;
}

enum StoreUpdateKind { pending, purchased, restored, canceled, error }

@immutable
class StoreUpdate {
  const StoreUpdate({required this.kind, required this.productId, this.error, this.needsCompletion = false, this.raw});
  final StoreUpdateKind kind;
  final String productId;
  final String? error;

  /// The store waits for the app to finish this transaction (StoreKit), or acknowledge it (Play).
  final bool needsCompletion;

  /// The plugin's `PurchaseDetails`, which [StoreGateway.complete] needs back.
  final Object? raw;
}

abstract class StoreGateway {
  /// Null on a platform with no in-app purchase: the web, desktop.
  StoreKind? get kind;
  Future<bool> available();

  /// Null when the store does not know the product.
  Future<StoreProduct?> product(String id);

  /// Opens the store's purchase sheet. The result arrives on [updates].
  Future<void> buy(StoreProduct product, String bindingToken);
  Stream<List<StoreUpdate>> get updates;
  Future<void> complete(StoreUpdate update);

  /// The store's own subscription management (cancel, change payment).
  Future<void> manage(String productId);
}

/// The web and the desktop builds: no store.
class NoStoreGateway implements StoreGateway {
  const NoStoreGateway();
  @override
  StoreKind? get kind => null;
  @override
  Future<bool> available() async => false;
  @override
  Future<StoreProduct?> product(String id) async => null;
  @override
  Future<void> buy(StoreProduct product, String bindingToken) async {}
  @override
  Stream<List<StoreUpdate>> get updates => const Stream.empty();
  @override
  Future<void> complete(StoreUpdate update) async {}
  @override
  Future<void> manage(String productId) async {}
}

class InAppPurchaseGateway implements StoreGateway {
  InAppPurchaseGateway(this.kind);

  @override
  final StoreKind kind;

  InAppPurchase get _iap => InAppPurchase.instance;

  @override
  Future<bool> available() => _iap.isAvailable();

  @override
  Future<StoreProduct?> product(String id) async {
    final response = await _iap.queryProductDetails({id});
    final found = [for (final d in response.productDetails) if (d.id == id) d];
    if (found.isEmpty) return null;
    // Play lists one entry per offer of a subscription. Sell the base plan, the one with no offer
    // id, and fall back to the first.
    final chosen = found.firstWhere(
      (d) => d is! GooglePlayProductDetails || _isBasePlan(d),
      orElse: () => found.first,
    );
    return StoreProduct(id: chosen.id, price: chosen.price, raw: chosen);
  }

  static bool _isBasePlan(GooglePlayProductDetails details) {
    final offers = details.productDetails.subscriptionOfferDetails;
    final index = details.subscriptionIndex;
    if (offers == null || index == null || index >= offers.length) return true;
    return offers[index].offerId == null;
  }

  @override
  Future<void> buy(StoreProduct product, String bindingToken) async {
    final details = product.raw;
    if (details is! ProductDetails) throw StateError('not a store product');
    // A subscription is a non-consumable in this plugin's terms.
    await _iap.buyNonConsumable(purchaseParam: PurchaseParam(productDetails: details, applicationUserName: bindingToken));
  }

  @override
  Stream<List<StoreUpdate>> get updates => _iap.purchaseStream.map((list) => [for (final p in list) _update(p)]);

  static StoreUpdate _update(PurchaseDetails p) => StoreUpdate(
        kind: switch (p.status) {
          PurchaseStatus.pending => StoreUpdateKind.pending,
          PurchaseStatus.purchased => StoreUpdateKind.purchased,
          PurchaseStatus.restored => StoreUpdateKind.restored,
          PurchaseStatus.canceled => StoreUpdateKind.canceled,
          PurchaseStatus.error => StoreUpdateKind.error,
        },
        productId: p.productID,
        error: p.error?.message,
        needsCompletion: p.pendingCompletePurchase,
        raw: p,
      );

  @override
  Future<void> complete(StoreUpdate update) async {
    final details = update.raw;
    if (details is PurchaseDetails && details.pendingCompletePurchase) {
      await _iap.completePurchase(details);
    }
  }

  @override
  Future<void> manage(String productId) async {
    if (kind == StoreKind.apple) {
      await InAppPurchaseStoreKitPlatformAddition().showManageSubscriptions();
      return;
    }
    await launchUrl(Uri.parse('https://play.google.com/store/account/subscriptions'), mode: LaunchMode.externalApplication);
  }
}

/// The store this build sells through: the App Store on iOS, Google Play on Android, none elsewhere.
/// (The Mac App Store build sells nothing in phase 1.)
final storeGatewayProvider = Provider<StoreGateway>((ref) {
  if (kIsWeb) return const NoStoreGateway();
  return switch (defaultTargetPlatform) {
    TargetPlatform.iOS => InAppPurchaseGateway(StoreKind.apple),
    TargetPlatform.android => InAppPurchaseGateway(StoreKind.google),
    _ => const NoStoreGateway(),
  };
});
