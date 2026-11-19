# SaaS Phase 1 · Plan D2 — Apple and Google in-app subscriptions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A subscription bought in the iOS or Android app reaches the same AURA subscription record
as a Fawry payment, and the store's own notifications keep it current:
- **Subscribe:** start or renew the subscription, set its period end to the store's expiry, and
  set the workspace's plan.
- **Billing retry or grace:** the subscription goes to `grace`.
- **Expired, revoked or refunded:** the workspace goes back to Free.
- **Auto-renew turned off:** the subscription is `canceled`, keeping access to period end.

Purchases are bound to a workspace by a token AURA issues before the purchase, so a store event can
never be applied to the wrong workspace.

**Not in D2:**
- The app's purchase screens (Plan F). Plan F calls the binding route below and passes the token
  to StoreKit / Play Billing.
- Promotional offers.
- Price changes, which are configured in the stores.

**Architecture:**
- **Binding.** `POST /api/v3/workspaces/{id}/billing/store-token` (Owner) returns a UUID stored in
  `aura_store_bindings` (migration `0023`). The client sets it as Apple's `appAccountToken`, or
  Google's `obfuscatedExternalAccountId`. Each store event names that UUID, and that is how AURA
  finds the workspace.
- **Apple** (`adapters/apple_store.py`): verifies App Store Server Notifications V2.
  - The `signedPayload` is a JWS (ES256) whose `x5c` chain must end in the **pinned Apple Root CA
    G3**. The adapter checks the chain's signatures and validity, and Apple's OIDs on the leaf
    (`1.2.840.113635.100.6.11.1`) and the intermediate (`1.2.840.113635.100.6.2.1`).
  - The same check runs on the nested `signedTransactionInfo`.
  - It also checks the bundle id against `AURA_APPLE_BUNDLE_ID`.
- **Google** (`adapters/google_play.py`): verifies Real-Time Developer Notifications delivered by
  Pub/Sub push.
  - The push request carries a Google-signed OIDC token, whose audience and service-account email
    are checked.
  - Then `purchases.subscriptionsv2.get` with the Play service account gives the authoritative
    state, expiry and `externalAccountIdentifiers.obfuscatedExternalAccountId`. The notification
    body itself is never trusted for state.
- **`StoreBillingService`** (`billing/stores.py`) maps a verified store event onto
  `Subscription` and `PlanService.set_entitlement(source="subscription")`, reusing D1's store and
  audit.
- `BillingService.run_cycle` issues no renewals for `apple`/`google` subscriptions (the store
  renews). It still moves them on if no store event arrives within the period plus grace, as a
  safety net.

**Spec:** §5.4 ("In-app purchase … verified server-side … mapped onto the same subscriptions
record. A workspace has one active provider at a time. Team plans are sold on the web only.").

## Global Constraints

- **Team is web-only.** A store product may map only to plans for personal workspaces (Pro). Any
  other mapping is refused at configuration time.
- **Products are data.** `AURA_STORE_PRODUCTS`:
  `{"apple": {"<productId>": "pro"}, "google": {"<productId>": "pro"}}`. An unknown product is
  ignored and audited.
- **One active provider.** A store activation for a workspace that has a live Fawry subscription is
  applied, but audited as `billing.provider_switch`. The Fawry record's renewals stop because the
  provider field changes.
- **The pinned root** is the DER of Apple Root CA G3, SHA-256
  `63343abfb89a6a03ebb57e9b3f5fa7be7c4f5c756f3017b3a8c488c3653e9179`, embedded as base64. Tests
  inject their own root.
- **Errors.** A bad signature, a broken chain, a wrong bundle or package, an unknown binding, or a
  replayed `notificationUUID` → 4xx or "ignored", never a 500, and never a state change.
  Store notifications are acknowledged with 200 once processed or ignored, so stores stop retrying.
- **Secrets.** The Google service-account JSON is read from the env var named by
  `google_play_credentials_env` (a property, never a field). It is never logged.
- **Replays.** `notificationUUID` (Apple) and Pub/Sub `messageId` (Google) go into
  `aura_store_events` (PK `provider` + `event_id`). A replay is a no-op.
- Alembic only, with a working `downgrade()`. In-memory adapters. No network in the default
  `pytest`: Google's token verification and the Publisher API are injected.

## Review Focus

1. A forged Apple payload signed by a self-made chain: refused, because the root is pinned.
2. The same notification delivered twice, or Google's push replayed: applied once.
3. A purchase whose token is not bound, or is bound to a workspace the payer no longer owns:
   ignored and audited; nothing changes.
4. Renew, then expire, then a late `DID_RENEW` for an older transaction: the period never moves
   backwards (`max` of expiries).
5. Refund or revoke: Free immediately, whatever the expiry says.

---

### Task 1: Bindings, events, migration 0023

**Domain** (`domain/billing.py`):
- `StoreBinding(token, workspace_id, created_by, created_at)`.
- `StoreEvent(provider, event_id, received_at)`.
- `StoreProducts` loader: `load_store_products(raw, catalogue) -> {"apple": {...}, "google": {...}}`.
  It validates plan ids exist and are personal-only.

**Ports:**
- `StoreBindingStore` with `put` and `get(token)`.
- `StoreEventLog` with `record(provider, event_id, at) -> bool`. It returns False when the event
  was already seen; atomic.

**Adapters:** in-memory and Postgres. Migration `0023` adds `aura_store_bindings` (cascade with the
workspace) and `aura_store_events`. The head pins move to `0023`.

Contract tests run on in-memory and PG16, including concurrent `record` of the same id → exactly
one True.

### Task 2: Apple verifier

`adapters/apple_store.py`:

- **`AppleNotificationVerifier(root_der: bytes, bundle_id: str, environment: str | None)`**,
  method `verify(signed_payload: str) -> AppleEvent`. It:
  - decodes the JWS header;
  - builds the x5c certs and checks each signs the next;
  - checks every cert's validity at the JWS's `signedDate`, falling back to now;
  - checks the root equals the pinned DER, and the OIDs;
  - verifies the ES256 signature with the leaf's key (cryptography `ECDSA(SHA256)`, raw r‖s
    converted to DER);
  - then does the same for `data.signedTransactionInfo`.
- **`AppleEvent`** carries:
  - `event_id` (`notificationUUID`), `type` and `subtype`;
  - `bundle_id` and `environment`;
  - `transaction`: `app_account_token`, `product_id`, `original_transaction_id`,
    `expires_at`, `revoked`.
- **Tests** build a throwaway root → intermediate (Apple intermediate OID) → leaf (Apple leaf OID)
  with `cryptography` and sign payloads with it. Cases:
  - a good chain;
  - a chain under another root, refused;
  - a missing OID;
  - an expired leaf;
  - a tampered payload;
  - the wrong bundle;
  - a nested transaction with a bad signature.

  A separate test asserts the embedded Apple root's SHA-256 equals the pinned constant.

### Task 3: Google verifier and Play client

`adapters/google_play.py`:

- **`GooglePushVerifier(audience, service_account_email, verify_token=None)`** checks the
  `Authorization: Bearer` OIDC token. The default `verify_token` uses
  `google.oauth2.id_token.verify_oauth2_token` with `google.auth.transport.requests.Request()`,
  run in a thread. The token's `email` must equal the configured push service account, and it
  must be `email_verified`.
- **`GooglePlayClient(package_name, credentials_info, http=None, token_provider=None)`** provides
  `subscription(purchase_token) -> PlaySubscription`, using
  `GET https://androidpublisher.googleapis.com/androidpublisher/v3/applications/{package}/purchases/subscriptionsv2/tokens/{token}`
  with a service-account bearer. The token comes from `google.oauth2.service_account.Credentials`
  with scope `androidpublisher`, refreshed in a thread.
- **`PlaySubscription`** carries `state`, `product_id`, `expires_at`, `external_account_id`,
  `linked_purchase_token` and `acknowledged`.
- **`parse_push(body) -> (message_id, purchase_token, package_name, notification_type)`.** Pub/Sub's
  envelope is `{message:{data(base64), messageId}}`, and `data` holds
  `subscriptionNotification`. Test notifications and one-time products are ignored.
- **Tests:**
  - push parsing: good, malformed, test notification;
  - the verifier with an injected `verify_token`: wrong email, wrong audience error;
  - the Play client against `httpx.MockTransport`: the request path and bearer, and mapping of
    `SUBSCRIPTION_STATE_ACTIVE` / `IN_GRACE_PERIOD` / `ON_HOLD` / `CANCELED` (active until
    expiry) / `EXPIRED` / `PAUSED`.

### Task 4: `StoreBillingService`, cycle change, routes

**`StoreBillingService(bindings, events, subscriptions, plans, tenancy, clock, audit, products, apple, google_push, google_play)`:**

- **`issue_token(actor, workspace_id) -> str`.** Owner of a **personal** workspace only, because
  Team is web-only. It stores the binding.
- **`handle_apple(signed_payload) -> str`:**
  1. Verify the payload.
  2. Record `notificationUUID`; if it is already seen, return `"duplicate"`.
  3. Look up the binding by `app_account_token` → workspace, and check the binder is still the
     Owner. Look up the product → plan.
  4. Then by type:
     - `SUBSCRIBED`, `DID_RENEW`, `OFFER_REDEEMED`: active, `period_end = max(existing, expires_at)`.
     - `DID_FAIL_TO_RENEW` with subtype `GRACE_PERIOD`: grace until `expires_at`.
     - `DID_FAIL_TO_RENEW` without it: past due.
     - `DID_CHANGE_RENEWAL_STATUS` with `AUTO_RENEW_DISABLED`: canceled; with
       `AUTO_RENEW_ENABLED`: active.
     - `EXPIRED`, `GRACE_PERIOD_EXPIRED`, `REVOKE`, `REFUND`: expired → Free.
     - Anything else: ignored.
- **`handle_google(authorization, body) -> str`:**
  1. Verify the push token and parse the push.
  2. Record `messageId`.
  3. Fetch the subscription from the Play API (authoritative).
  4. Look up the binding by `external_account_id`, and the product.
  5. Map the state:
     - `ACTIVE` → active;
     - `IN_GRACE_PERIOD` → grace;
     - `ON_HOLD` or `PAUSED` → past due;
     - `CANCELED` → canceled (access until expiry);
     - `EXPIRED` → expired → Free.
     - A `linked_purchase_token` (upgrade) applies to the same binding.

**`BillingService.run_cycle`:** no renewals for `provider in ("apple","google")`.

**Routes:**
- `POST /workspaces/{id}/billing/store-token` (Owner; verified email);
- `POST /billing/apple/notify` with body `{"signedPayload": ...}`;
- `POST /billing/google/notify` (Pub/Sub push).

The two notification routes are public: 200 `{ok, result}`; 401/403 for a bad signature or token;
400 for a malformed body; 503 when billing is disabled or the store is not configured. Add both to
`PUBLIC` with reasons, the IDOR entry for store-token, and reachability triage.

**Settings** (`BillingSettings`): `apple_bundle_id`, `apple_environment` (`Production` |
`Sandbox`), `google_package_name`, `google_push_audience`, `google_push_service_account`,
`google_play_credentials_env` (default `AURA_GOOGLE_PLAY_CREDENTIALS_JSON`; the secret is read
through a property), `store_products_json`.

**Tests:** `tests/unit/test_store_billing_service.py` (each Review Focus case, using fake verifiers
that return events) and `tests/integration/test_store_billing_api.py`.

### Task 5: Docs

- `docs/deployment/billing-stores.md`:
  - App Store Connect: the products, the V2 server notification URL, and the bundle id;
  - Play Console: the products; a Pub/Sub topic and push subscription with OIDC auth to the URL;
    and a service account with "View financial data" for the Publisher API;
  - the env vars;
  - how Plan F's client must pass the token.
- ADR-034 section. Status row.
