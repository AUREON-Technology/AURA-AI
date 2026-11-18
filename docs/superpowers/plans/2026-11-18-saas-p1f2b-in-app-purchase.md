# SaaS P1-F2b: In-App Purchase in the App, Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** People can buy Pro inside the iOS and Android apps with the store's own purchase sheet, and the purchase reaches the subscription record that Plan D2 keeps.

**Architecture:**
- The app never sends a receipt. It gets a binding token from `POST /workspaces/{id}/billing/store-token` (Plan D2) and hands it to the store:
  - on iOS as StoreKit 2's `appAccountToken`;
  - on Android as Play's `obfuscatedAccountId`.

  The store then notifies AURA (D2's verified notify routes), and AURA activates the plan. The app finishes the store transaction and waits for `GET …/billing` to show the store subscription.
- All store calls sit behind one small interface, `StoreGateway`. The real gateway wraps `package:in_app_purchase`, and widget tests use a fake.
- The backend gains three small things the app needs:
  - the subscription's `provider`;
  - the configured store product ids;
  - a refusal to "cancel" a store subscription from AURA, since only the store can stop the charges.

**Tech Stack:** Flutter 3.44.6 with Riverpod 3; `in_app_purchase` 3.3.1, which uses StoreKit 2 by default and depends on `in_app_purchase_storekit` 0.4.x and `in_app_purchase_android` 0.5.x; FastAPI.

**Spec:** `docs/superpowers/specs/2026-11-16-saas-phase1-design.md`: §5.4 (stores, "IAP on mobile") and §5.5 (upgrade flow). See also `docs/deployment/billing-stores.md` (how a purchase is tied to a workspace).

## Global Constraints

- **Where purchase appears.** In-app purchase is offered only on iOS and Android, only for a **personal** workspace, and only to its **Owner**. It also needs billing to be on and the store to know the product.
  - Team plans are not sold in the apps.
  - On iOS and Android, no screen mentions or links to another way to pay (store anti-steering rules). The old copy "Upgrades are on the web for now." becomes "Team plans can't be bought in this app.", shown for a team.
- **The binding token** is passed exactly as `PurchaseParam.applicationUserName`. The plugin maps it to `appAccountToken` on iOS (StoreKit 2; the token is a UUID) and to `obfuscatedAccountId` on Android, as checked in the plugin's source (`in_app_purchase_storekit_platform.dart:164`, `in_app_purchase_android_platform.dart:171`).
- **Every purchase update with `pendingCompletePurchase`** is completed, including ones redelivered at app start. The purchase stream is listened to from app start on iOS and Android, as the plugin requires.
- **No double billing.**
  - The app never offers a store purchase while a Fawry subscription is live (`active`, `past_due` or `grace`).
  - The Fawry "Cancel subscription" button is never shown for a store subscription.
  - The backend refuses `billing/cancel` for a non-Fawry subscription with 409.
- **Product ids** come from the server, `AURA_STORE_PRODUCTS` via `GET …/billing` → `store_products`. Only ids mapped to `pro` are offered. There are no hard-coded product ids in the app.
- **Strings.** Every new string goes in `app_en.arb` plus an Egyptian Arabic `app_ar.arb` translation, never an English copy.
- **Checks.** `flutter analyze` must be clean, `flutter test` green, and backend `pytest` green.

## Review Focus

1. **The store notification arrives late or never.** After a successful purchase the page says the purchase was received and that the plan updates shortly. It must not show an error, and must not offer to buy again while waiting. Test: the fake store reports `purchased`, but the billing status never changes. After the polling stops, the page shows `planStorePending` and no buy button.
2. **The person cancels the store sheet.** The page returns to the buy button with no error. Test: the fake reports `canceled`, and `plan-store-buy` is enabled again.
3. **An unfinished transaction is redelivered at app start**, for example after the app was killed mid-purchase. It is completed even though no plan page is open. Test: `StorePurchaseListener` completes an update that arrives with no page.
4. **`store-token` fails** (403 email not verified, or 429), or the store is unavailable. The failure shows, and no store sheet opens. Test: the fake client throws a 403, so `buy` is never called on the fake store and the failure text shows.
5. **A Fawry subscription is live on mobile, or a store subscription is live on the web.** There is no purchase and no Fawry cancel for a store subscription, and the store subscription shows "Manage subscription" on its own platform. Test: both cases.

## As built (after review)

- `planStoreDone` was dropped. A finished purchase shows **Manage subscription** in place of the buy button.
- Android's **Manage** opens Play's general subscriptions page. The `sku` / `package` form needs the package name, and the app has no package-info dependency.
- **Review fixes:**
  - Android reports a cancelled or failed sheet with an empty product id. It is handled while the sheet is open.
  - A sheet the store refuses to open is a failure, not a stuck spinner.
  - Ask to Buy, or a slow payment approved later, still activates.
  - A Fawry reference that can still be paid blocks a store purchase. The app hides the button, and `store-token` answers 409.

---

### Task 1: Backend — `provider`, `store_products`, refusing a store cancel

**Files:**
- Modify: `aura_backend/aura_core/billing/service.py` (`status()` adds `"provider": sub.provider`; `cancel()` raises `ConflictError("this subscription is managed in the App Store or Google Play; cancel it there")` when `sub.provider != "fawry"`, checked after the existing no-subscription check);
- Modify: `aura_backend/aura_core/api/billing.py` (`billing_status` adds `"store_products": {store: sorted ids whose plan is "pro"}` from `stores.products`);
- Test: `aura_backend/tests/unit/test_store_billing_service.py`, `aura_backend/tests/unit/test_billing_service.py`, and the route test that covers `GET …/billing`. Find it with `grep -rn "billing\"" tests/unit/test_billing*` and extend that file.

**Interfaces:**
- Produces:
  - `GET /api/v3/workspaces/{id}/billing` →
    `{subscription: {…, provider: "fawry"|"apple"|"google"} | null, payments: […], store_products: {"apple": ["aura.pro.monthly"], "google": ["aura_pro"]}}`. A store with no products is absent.
  - `POST …/billing/cancel` on a store subscription → 409 `conflict`.

- [ ] **Write the tests.**
  - `test_status_names_the_provider`: after a Fawry activation, `status()["subscription"]["provider"] == "fawry"`.
  - `test_cancel_refuses_a_store_subscription`: a subscription put with `provider="apple"` causes `cancel` to raise `ConflictError`, and the stored status is unchanged.
  - Route test: with `AURA_STORE_PRODUCTS` = `{"apple": {"aura.pro.monthly": "pro"}}`, `GET …/billing` returns `store_products == {"apple": ["aura.pro.monthly"]}`.
- [ ] **Run the tests and confirm they fail.**
- [ ] **Implement.**
- [ ] **Run** `pytest tests/unit/test_billing_service.py tests/unit/test_store_billing_service.py <route test> -q`, then the full `pytest` and `pytest tests/architecture -q`.
- [ ] **Commit.**

### Task 2: Client — the store port, the purchase flow and the plan page

**Files:**
- Modify: `aura_client/pubspec.yaml` (`in_app_purchase: ^3.3.1`; run `flutter pub get`, and commit `pubspec.lock`).
- Create:
  - `aura_client/lib/features/workspaces/data/store_gateway.dart`: `StoreGateway`, `StoreProduct`, `StoreUpdate`, and the `InAppPurchaseGateway` implementation;
  - `aura_client/lib/features/workspaces/presentation/store_purchase.dart`: `StorePurchaseController`, `StorePurchaseListener` and `_StoreUpgrade`.
- Modify:
  - `workspace_models.dart` (`SubscriptionView.provider`, `BillingStatus.storeProducts`);
  - `workspaces_client.dart` (`storeToken(id)`);
  - `plan_page.dart` (the gating below);
  - `lib/main.dart` (start the listener on iOS and Android);
  - `lib/l10n/app_en.arb` and `app_ar.arb`.
- Test: `aura_client/test/workspaces/store_purchase_test.dart`, plus a fake gateway in `workspace_fakes.dart`.

**Interfaces:**

```dart
// store_gateway.dart
enum StoreKind { apple, google }
class StoreProduct { final String id; final String price; /* raw: the plugin's ProductDetails */ }
enum StoreUpdateKind { pending, purchased, restored, canceled, error }
class StoreUpdate { final StoreUpdateKind kind; final String productId; final String? error; /* raw PurchaseDetails */ }
abstract class StoreGateway {
  StoreKind? get kind;                       // null: this platform has no store purchase
  Future<bool> available();
  Future<StoreProduct?> product(String id);  // null when the store does not know it
  Future<void> buy(StoreProduct product, String bindingToken);   // applicationUserName = token
  Stream<List<StoreUpdate>> get updates;
  Future<void> complete(StoreUpdate update); // completePurchase when pendingCompletePurchase
  Future<void> manage(String productId);     // iOS: showManageSubscriptions(); Android: Play URL
}
final storeGatewayProvider = Provider<StoreGateway>(...); // InAppPurchaseGateway on iOS/Android, else NoStoreGateway

// workspaces_client.dart
Future<String> storeToken(String id); // POST …/billing/store-token → body['token']
```

**`InAppPurchaseGateway` details:**
- `kind` is decided by `defaultTargetPlatform`, and is never set on the web.
- On Android, `product` picks the `GooglePlayProductDetails` whose offer has no `offerId` (the base plan), and falls back to the first.
- On Android, `manage` launches `https://play.google.com/store/account/subscriptions?sku=<id>&package=<packageName>` with `url_launcher`.

**`StorePurchaseController`** is an `AutoDisposeFamilyNotifier<StorePurchaseState, String workspaceId>`, with states `idle | buying | waiting | pending | done | failed(error)`. `buy(product)`:
1. Call `storeToken`; on failure, go to `failed`.
2. Call `gateway.buy(product, token)`, and enter `buying`.
3. For an update about `product.id`:
   - `purchased` or `restored`: `complete(update)`, enter `waiting`, then poll `billing(id)` every 3 s, up to 20 times. It reaches `done` when the subscription's `provider` is this store and its status is `active`, and then invalidates `billingStatusProvider` and `planUsageProvider`. If polling ends without that, it enters `pending`.
   - `pending`: enter `pending`.
   - `canceled`: back to `idle`.
   - `error`: `failed`.

**`StorePurchaseListener`** is started from `main.dart` when `gateway.kind != null`. It completes every update that has `pendingCompletePurchase`, so a redelivered transaction is finished even with no page open.

**Plan page gating, in `_BillingSection`,** for the upgrade area on a store platform (`gateway.kind != null`):
- a team → `planTeamNotInApp` (key `plan-mobile`);
- a live store subscription (provider apple/google, status `active`, `grace`, `past_due` or `canceled`) → the `planStoreManage` button (key `plan-store-manage`), shown only when the provider is this platform's store; otherwise `planStoreOtherDevice`;
- a live Fawry subscription → `planFawryActive` text, with no buy button;
- no product id for this store in `store_products`, or the store unavailable → `planBillingOff`;
- otherwise `_StoreUpgrade`, a button with key `plan-store-buy` and label `planStoreBuy(price)`.

On the web and desktop, the existing Fawry `_Upgrade` stays. When the subscription's provider is not `fawry`, it shows the `planStoreElsewhere` text instead of the buy and cancel buttons.

**Strings** (English; the Arabic goes in `app_ar.arb`):
- `planStoreBuy`: "Upgrade to Pro · {price}"
- `planStoreWaiting`: "Purchase received. Activating your plan…"
- `planStorePending`: "Purchase received. Your plan will update in a few minutes."
- `planStoreDone`: "You're on Pro."
- `planStoreManage`: "Manage subscription"
- `planStoreOtherDevice`: "Your subscription is managed in your other device's app store."
- `planStoreElsewhere`: "This subscription is managed in the App Store or Google Play."
- `planFawryActive`: "You already have a subscription."
- `planTeamNotInApp`: "Team plans can't be bought in this app."
- `planStoreFailed`: "The purchase didn't go through."

`planUpgradeMobile` is removed.

**Steps:**
- [ ] **Write `store_purchase_test.dart`.** It needs these tests, Review Focus 1–5 among them:
  - `'an owner buys Pro in the app with the binding token'`: the fake store is called with `buy('aura.pro.monthly', 'tok-1')`. After `purchased`, the fake billing shows provider `apple` and status `active`. The update is completed, and the page shows `You're on Pro.`
  - `'a purchase whose notification is late says so and offers no second buy'`.
  - `'cancelling the store sheet returns to the buy button'`.
  - `'a redelivered transaction is completed with no page open'`.
  - `'store-token refused: the failure shows and the store is never opened'`.
  - `'a live Fawry subscription offers no store purchase'`.
  - `'a store subscription shows Manage on its own store and no Fawry cancel on the web'`.
  - `'a team on mobile cannot buy in the app'`.

  Update the existing `'the store builds show no purchase and no link out'` test to the new key and copy.
- [ ] **Run** `flutter test test/workspaces/store_purchase_test.dart` and confirm it fails.
- [ ] **Implement.** Keep `canCheckoutHereProvider` for Fawry. The new `storeGatewayProvider` decides the store path.
- [ ] **Run** `flutter analyze` (clean) and the full `flutter test`, including the Arabic completeness test and the hard-coded `Text()` ratchet.
- [ ] **Commit.**

### Task 3: Native setup and docs

- [ ] **Android.** Confirm that the merged manifest carries `com.android.vending.BILLING`, which comes from the plugin: run `flutter build apk --debug` and `aapt dump permissions`, or grep `build/app/intermediates/merged_manifests`. If no Android SDK is available here, say so instead.
- [ ] **iOS.** In-app purchase needs no entitlement file. The iOS 15 deployment target already allows StoreKit 2. Record the Xcode step: the In-App Purchase capability in Signing & Capabilities, which was not verified here because there is no macOS host.
- [ ] **`docs/deployment/billing-stores.md`.** Replace "What is not built yet: the app's purchase screens" with how the app buys, and add a test-purchase checklist for each store (a sandbox tester / license tester, the expected audit events, and what the plan page shows).
- [ ] **Status files.** Update the Plan rows in `docs/status/MOBILE_APP_STATUS.md`.
- [ ] **Commit.**
