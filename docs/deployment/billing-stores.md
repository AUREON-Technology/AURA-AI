# In-app subscriptions: App Store and Google Play (SaaS P1-D2)

People who buy Pro inside the iOS or Android app pay the store, not Fawry. The store tells AURA
about every change, and AURA keeps the same subscription record a Fawry payment would.

| Store event | AURA's subscription | Plan |
|---|---|---|
| Subscribed, renewed, offer redeemed / Play `ACTIVE` | `active` until the store's expiry (never earlier than already held) | Pro |
| Renewal failed, in Apple's or Play's grace period | `grace` | Pro |
| Renewal failed, no grace / Play `ON_HOLD`, `PAUSED` | `past_due` (access suspended, as the store does) | Free until the store recovers it |
| Auto-renew turned off / Play `CANCELED` | `canceled`, access until the expiry | Pro until then |
| Expired | `expired` | Free |
| Refunded or revoked / Play voided purchase | `expired` at once | Free |

Only **personal** workspaces can buy in the apps. Team plans are sold on the web (Fawry).

Each store is **off** until its settings below are complete, and every store route answers 503
until then (and while `AURA_BILLING_ENABLED` is off).

## How a purchase is tied to a workspace

Before starting a purchase, the app asks AURA for a binding token:

```
POST /api/v3/workspaces/{workspace_id}/billing/store-token     (Owner, verified email)
→ 201 {"token": "<uuid>"}
```

The app must pass that token to the store (Plan F builds these screens):

- **iOS (StoreKit 2):** `Product.PurchaseOption.appAccountToken(UUID(uuidString: token))`.
- **Android (Play Billing):** `BillingFlowParams.newBuilder().setObfuscatedAccountId(token)`.
  The app should also acknowledge the purchase. AURA acknowledges it as well, because Play refunds
  a purchase nobody acknowledged within three days.

A store event whose token AURA did not issue, or was issued by someone who no longer owns the
workspace, changes nothing and is audited as `billing.store_ignored`.

## App Store Connect

1. Create the auto-renewable subscription products (for example `aura.pro.monthly`).
2. App Information → **App Store Server Notifications**: set **Version 2** and, for both
   Production and Sandbox, the URL:

   ```
   https://api.aureon-ai.tech/api/v3/billing/apple/notify
   ```

3. Note the app's **bundle id**.

AURA trusts a notification only if its certificate chain ends in **Apple Root CA - G3**, which is
pinned in the code (`aura_core/adapters/apple_store.py`, SHA-256
`63343abfb89a6a03ebb57e9b3f5fa7be7c4f5c756f3017b3a8c488c3653e9179`, valid until 2039). Use
**Request a Test Notification** in App Store Connect after deploying. The route answers
`{"ok": true, "result": "ignored"}` for it, because a test notification carries no transaction.

## Google Play Console and Google Cloud

1. Create the subscription products (for example `aura_pro`).
2. In Google Cloud (the project linked to Play):
   - Create a Pub/Sub **topic**. Grant `google-play-developer-notifications@system.gserviceaccount.com`
     the *Pub/Sub Publisher* role on it.
   - Create a **push subscription** to the URL below, with **authentication enabled**, a
     service account for the push (for example `rtdn-push@<project>.iam.gserviceaccount.com`),
     and the **audience** set to the same URL:

     ```
     https://api.aureon-ai.tech/api/v3/billing/google/notify
     ```

3. Play Console → Monetization setup → **Real-time developer notifications**: enter the topic,
   and send a test notification. AURA answers `ignored` for it.
4. Create a service account for the **Google Play Developer API**, download its JSON key, and in
   Play Console → Users and permissions invite it with **View financial data** (and *Manage orders
   and subscriptions*, so AURA can acknowledge).

A push is trusted only when Google's OIDC token verifies for that audience and comes from the
push service account. AURA then reads the purchase from the Play Developer API and acts only on
that answer. If the API does not answer, or acknowledging the purchase fails, the route returns
503, so Pub/Sub delivers the message again. A message that can never succeed (malformed, or a
purchase Play no longer knows) is answered 200 `ignored` and audited, so Pub/Sub stops retrying.

Paying with Fawry is refused while a store subscription is still renewing: the person must cancel
it in the store first, or they would be billed twice.

## Railway variables (service `AURA-AI`)

| Variable | Value |
|---|---|
| `AURA_APPLE_BUNDLE_ID` | the iOS bundle id |
| `AURA_APPLE_ENVIRONMENT` | `Production` (the default when empty) or `Sandbox` for testing. Sandbox purchases are free, so never leave `Sandbox` on in production |
| `AURA_GOOGLE_PACKAGE_NAME` | the Android package name |
| `AURA_GOOGLE_PUSH_AUDIENCE` | the notify URL, exactly as set on the push subscription |
| `AURA_GOOGLE_PUSH_SERVICE_ACCOUNT` | the push service account's email |
| `AURA_GOOGLE_PLAY_CREDENTIALS_JSON` | the Play API service-account JSON (a **secret**; never logged) |
| `AURA_STORE_PRODUCTS` | `{"apple": {"aura.pro.monthly": "pro"}, "google": {"aura_pro": "pro"}}` |

Startup refuses a half Google configuration: a package name without the audience, push account and
credentials. `AURA_STORE_PRODUCTS` may map only to personal plans. A product that is not listed is
ignored and audited.

## The app (SaaS P1-F2b)

You → Plan, on iOS and Android, for the Owner of a **personal** workspace:

1. The app reads `store_products` from `GET …/billing`: the product ids that
   `AURA_STORE_PRODUCTS` maps to `pro`, per store. The app has no product id of its own, so
   changing the product needs only a variable, not a new build.
2. **Upgrade to Pro · <store price>** asks AURA for a binding token, then opens the store's sheet
   with it, passed through `in_app_purchase` 3.3.1:
   - on iOS, StoreKit 2 `appAccountToken`;
   - on Android, `obfuscatedAccountId`.
3. When the store confirms, the app finishes the transaction and checks `GET …/billing` every
   3 s, for one minute, until the subscription's `provider` is that store and it is `active`.
   If the notification is slower than that, the page says the plan will update in a few minutes;
   the purchase is not lost.
4. **Transactions are finished from app start.** A transaction left unfinished, for example
   because the app was killed mid-purchase, is completed on the next launch even with no page
   open.

What the app will not do:
- It sells only personal plans. Teams see "Team plans can't be bought in this app." In line with
  the stores' anti-steering rules, no iOS or Android screen mentions another way to pay.
- It offers no store purchase while a Fawry subscription is active, past due or in grace.
- A store subscription shows **Manage subscription**, which opens the store's own page. On the web
  and desktop, a store subscription shows no Fawry buy or cancel. The backend also refuses
  `billing/cancel` for a store subscription with 409, because only the store can stop its
  renewals.

Native setup:
- **Android:** nothing to add by hand. The plugin brings Play Billing Library 9.1.0, whose
  manifest declares `com.android.vending.BILLING`. This has not yet been checked in a built APK.
- **iOS:** add **In-App Purchase** under Xcode → Runner → Signing & Capabilities. The deployment
  target, iOS 15, already allows StoreKit 2. This has not been checked here, because there is no
  macOS host.

## Test purchase, one per store, before announcing

| Step | App Store (Sandbox) | Google Play (license tester) |
|---|---|---|
| Server | `AURA_APPLE_ENVIRONMENT=Sandbox` on a **non-production** deployment | Production settings are fine: a license tester's purchase is free |
| Tester | A Sandbox Apple ID, signed in under Settings → App Store → Sandbox Account | The tester's Google account on the license-tester list, with the app installed from an internal testing track |
| Buy | Plan → Upgrade to Pro; approve the sheet | The same |
| Expect in AURA's audit log | `billing.store_token`, then `billing.store_active` | The same |
| Expect in the app | Within a minute, **Manage subscription** replaces the button, and the plan reads `pro` | The same |
| Expire | Sandbox renews every few minutes and stops after a few renewals. Expect `billing.expired` and plan `free` | Cancel in Play. Expect `billing.store_cancel`, then expiry at the period end |

## What is not built yet

- Promotional offers and offer codes beyond `OFFER_REDEEMED` activating the plan.
- Price changes, which are configured in the stores.
- The Mac App Store build sells nothing in phase 1.
- Neither store has been tried against its sandbox from this deployment. Do the test purchase above
  for each store before announcing in-app purchase.
