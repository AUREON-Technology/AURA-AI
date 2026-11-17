# SaaS Phase 1 · Plan D1 — Subscriptions and Fawry billing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A workspace Owner can pay for Pro or Team in EGP through Fawry, and AURA keeps the
subscription itself:
- **Payment:** Fawry issues a reference code, paid with cash at any Fawry outlet, a wallet or
  myFawry. A saved card token is charged when one exists.
- **Lifecycle:**
  - Fawry's signed notification activates or renews the subscription and sets the workspace's
    plan.
  - A scheduled cycle issues renewal payments before the period ends, and moves unpaid
    subscriptions through `past_due` → `grace` → `expired` (back to Free; nothing deleted).
  - Reconciliation queries Fawry for any payment still pending, so a lost notification cannot
    lock a payer out.

**Not in D1:**
- Apple/Google in-app purchase (Plan D2).
- Fawry's hosted card-capture page: its signature spec is unconfirmed (see below). The adapter can
  already charge a saved card token.
- Client screens (Plan F).

**Architecture:**
- `domain/billing.py`: stdlib. Subscription, payment, prices and pure state transitions.
- Ports `SubscriptionStore`, `PaymentStore` and `PaymentProvider`, each with an in-memory adapter.
  The stores also get Postgres adapters (migration `0022`); the provider gets a Fawry adapter
  (`adapters/fawry.py`, `httpx` imported lazily).
- `BillingService` (`aura_core/billing/`) drives it all and writes entitlements through
  `PlanService` with `source="subscription"`.
- Routes:
  - Owner checkout, status and cancel under `/api/v3/workspaces/{id}/billing`;
  - a public, signature-verified Fawry notification route;
  - a SuperAdmin or service-key route that runs the cycle;
  - `scripts/run_billing_cycle.py` for a Railway cron service.

**Spec:** `docs/superpowers/specs/2026-11-16-saas-phase1-design.md` §5.4.

## Fawry wire facts

**Sources:** the official staging doc for Server Notification V2, and the open-source
`fawry-api/fawry` client.

- **Base URL:**
  - sandbox: `https://atfawry.fawrystaging.com/ECommerceWeb/Fawry/`;
  - production: `https://www.atfawry.com/ECommerceWeb/Fawry/`.
- **Charge:** `POST payments/charge` (JSON).
  - Fields: `merchantCode`, `merchantRefNum`, `customerProfileId`, `paymentMethod`
    (`PAYATFAWRY` | `CARD`), `cardToken`?, `customerEmail`?, `customerMobile`?, `amount` (EGP,
    two decimals), `currencyCode` `EGP`, `description`, `paymentExpiry` (epoch ms), `chargeItems`
    `[{itemId, description, price, quantity}]`, `signature`.
  - `signature = sha256(merchantCode + merchantRefNum + customerProfileId + paymentMethod + f"{amount:.2f}" + (cardToken or "") + secureKey)`.
  - The response carries `referenceNumber`, `statusCode` (200 = ok) and `statusDescription`.
- **Status:** `GET payments/status?merchantCode&merchantRefNumber&signature`.
  - `signature = sha256(merchantCode + merchantRefNumber + secureKey)`.
  - The response carries `paymentStatus` or `orderStatus` (`PAID`, `UNPAID`, `EXPIRED`,
    `CANCELED`, `REFUNDED`, `FAILED`), `paymentAmount` and `referenceNumber`.
- **Notification V2:** a JSON POST to our URL.
  - Fields: `fawryRefNumber`, `merchantRefNum`, `paymentAmount`, `orderAmount`, `orderStatus`,
    `paymentMethod`, `paymentRefrenceNumber` (Fawry's spelling), `messageSignature`.
  - `messageSignature = sha256(fawryRefNumber + merchantRefNum + f"{paymentAmount:.2f}" + f"{orderAmount:.2f}" + orderStatus + paymentMethod + (paymentRefrenceNumber or "") + secureKey)`.
- **Must be confirmed in the Fawry staging account** before go-live (runbook): these formulas,
  the hosted card page, and the server-notification URL setting.

## Global Constraints

- Amounts are integer **piastres** (1 EGP = 100). Fawry gets `piastres / 100` formatted to two
  decimals.
- `merchantRefNum` is our payment id, unique, and the key for idempotent notification handling.
- **Signatures.** Comparison uses `hmac.compare_digest`. A bad signature gets 403 and changes
  nothing. A notification for an unknown `merchantRefNum` is acknowledged (200) and ignored, so
  Fawry stops retrying, and it is logged.
- **The paid amount must equal the payment's amount**, or the payment is flagged `mismatch` and
  does not activate anything.
- **The secret key** is `AURA_FAWRY_SECURE_KEY` (a Railway secret). It is never logged, never
  returned and never in an audit record.
- **Billing is off by default.** `AURA_BILLING_ENABLED=false`, and every billing route answers
  503 `billing_unavailable` until the owner turns it on.
- **Prices are data.** `AURA_PLAN_PRICES` holds JSON
  `{"pro": piastres_per_month, "team": piastres_per_seat_per_month}`. The placeholders are pro
  15000 and team 12000.
- **Periods.**
  - A subscription period is one calendar month from activation (same day of month, clamped to
    the month's end).
  - The renewal lead is 3 days. Grace is 7 days, with full access. Reference codes expire after 72
    hours.
- **One subscription per workspace** (the row is keyed by `workspace_id`). The checkout plan must
  fit the workspace kind (`PlanService.set_plan` validates).
- Alembic only, with a working `downgrade()`. Every port has an in-memory adapter. The default
  `pytest` needs no network: the Fawry adapter is tested with `httpx.MockTransport`.

## Review Focus

1. The same notification delivered twice, or out of order (`PAID` after `EXPIRED`): the period is
   extended once; a stale status never undoes a payment.
2. A notification whose amount differs from what we asked: no activation; payment marked
   `mismatch`; audited.
3. Fawry is down while the cycle runs: nothing changes state that depends on Fawry's answer, and
   the next run retries.
4. An Owner cancels mid-period: access lasts to the period end, then the plan becomes Free; no
   renewal payment is issued.
5. A webhook body that is not JSON, missing fields or with a bad signature: 403 or 400, never
   500, never a state change.

---

### Task 1: Billing domain

`aura_core/domain/billing.py`. Test: `tests/unit/test_billing_domain.py`.

- **`SubscriptionStatus`:** `pending`, `active`, `past_due`, `grace`, `canceled` (paid to period
  end, will not renew), `expired`.
- **`Subscription`:** `workspace_id`, `plan_id`, `seats`, `status`, `provider`, `period_start`,
  `period_end`, `grace_until`, `card_token`, `customer_profile_id`, `updated_at`.
- **`PaymentStatus`:** `pending`, `paid`, `expired`, `failed`, `mismatch`, `refunded`.
- **`Payment`:** `id`, `workspace_id`, `plan_id`, `seats`, `amount_piastres`, `method`,
  `purpose` (`initial` | `renewal`), `status`, `provider_ref`, `reference_number`, `created_at`,
  `expires_at`, `paid_at`.
- **Prices:**
  - `load_prices(raw) -> Mapping[str, int]`;
  - `price_for(prices, plan, seats) -> int`. Team is per seat, Pro is flat, and Free has no price
    (`ValidationError`).
- **Periods:** `add_month(dt) -> datetime`, with day clamping.
- **Pure transitions:**
  - `apply_payment(sub|None, payment, now) -> Subscription`. It activates, or extends from
    `max(period_end, now)`. It is idempotent per payment: the caller ensures one apply per
    payment.
  - `advance(sub, now, grace) -> Subscription`. Active/canceled past `period_end` →
    `past_due`/`expired` (canceled → expired); `past_due` → `grace` (until `period_end + grace`);
    `grace` past `grace_until` → `expired`.
- Tests: prices; month clamping (Jan 31 → Feb 28/29); activation; renewal extends from the period
  end, not from now, when paid early; each transition; canceled never renews.

### Task 2: Stores (ports, in-memory, migration 0022, Postgres)

**Ports:**
- `SubscriptionStore` with `get(workspace_id)`, `put(sub)` and `due(before) -> list[Subscription]`
  (status in active/past_due/grace/canceled and (period_end or grace_until) < before).
- `PaymentStore` with `put(p)`, `get(id)`, `pending(before|None)`, and
  `mark(id, status, *, provider_ref, paid_at, expected_status="pending") -> Payment|None`. The
  last is conditional: only from `pending`. That is the idempotency lock.

**Tables:**
- `aura_subscriptions`, with PK `workspace_id` cascading from `aura_workspaces`;
- `aura_payments`, with PK `id`, an index on `(status, created_at)`, and cascade.

Head pins move to `0022`. The contract tests cover in-memory and Postgres (`load`), including
"mark from pending twice → second returns None".

### Task 3: Payment port, fake provider, `BillingService`

**`PaymentProvider` port:**
- `create_reference(payment, *, customer_profile_id, email) -> ProviderCharge(reference_number, provider_ref)`;
- `charge_card(payment, *, customer_profile_id, card_token) -> ProviderCharge`;
- `status(payment_id) -> ProviderStatus(status, amount_piastres, provider_ref)`;
- `parse_notification(body: bytes) -> ProviderNotification(payment_id, status, amount_piastres, provider_ref)`.
  It raises `AuthenticationError` for a bad signature and `ValidationError` for a malformed body.

**`FakePaymentProvider`** is in-memory. It can mark payments paid and build signed-equivalent
notifications.

**`BillingService(subscriptions, payments, provider, plans, tenancy, clock, audit, settings, prices)`:**
- **`checkout(actor, workspace_id, plan_id, seats, email)`:**
  - Owner only (team: `can(role,"delete")` i.e. Owner; personal: its owner).
  - It validates kind and seats through the plan catalogue, refuses when an active subscription
    exists with the same plan and seats, creates the payment and calls `create_reference`.
  - It returns `{payment_id, reference_number, amount_piastres, expires_at}`.
- **`handle_notification(body)`:**
  - It parses the body. An unknown payment id gets an audit record and returns.
  - Mismatched amounts are marked `mismatch`.
  - `PAID` goes through `mark(pending→paid)`. Only when that returns a payment does it call
    `apply_payment`, `subscriptions.put` and
    `plans.set_entitlement(source="subscription")`.
  - `EXPIRED`/`FAILED`/`CANCELED` are marked accordingly.
- **`cancel(actor, workspace_id)`:** the Owner sets `canceled`.
- **`run_cycle(now)`:**
  1. For `active` subscriptions within the renewal lead and with no pending renewal payment, it
     creates a renewal payment: `charge_card` when a token exists, else `create_reference`.
  2. It runs `advance` on due subscriptions. On `expired` it sets the entitlement back to `free`
     (`source="subscription"`).
  3. It reconciles pending payments older than 10 minutes through `provider.status`, applying
     like a notification. Provider errors are logged per payment and the cycle continues.
  4. It returns counts.
- **`status(workspace_id)`:** the subscription, plus the last 10 payments with no tokens.

**`PlanService`** gains `set_entitlement(actor, workspace_id, plan_id, seats, source)` (the
validation shared with `set_plan`).

Tests: `tests/unit/test_billing_service.py`, one per Review Focus line plus the happy paths.

### Task 4: Fawry adapter

`aura_core/adapters/fawry.py`, `FawryPaymentProvider(base_url, merchant_code, secure_key, http=None)`.

- Lazy `httpx.AsyncClient`, 20 s timeout.
- The signatures are exactly those listed above.
- A `statusCode` other than 200 raises `DependencyUnavailableError` (retryable) with Fawry's
  `statusDescription` in details. The secret never appears in details.
- Status mapping: `PAID` → paid; `UNPAID`/`NEW` → pending; `EXPIRED` → expired; `CANCELED` or
  `CANCELLED`/`FAILED` → failed; `REFUNDED` → refunded.
- Tests (`tests/unit/test_fawry_adapter.py`, `httpx.MockTransport`):
  - the request body and signature for the reference and card variants (signatures computed in
    the test from the formula);
  - the status query signature;
  - a notification good signature, a bad signature, two-decimal formatting of `"150"` vs
    `"150.00"`, and a missing `paymentRefrenceNumber`;
  - an error status code becomes `DependencyUnavailableError`.

### Task 5: Settings, wiring, routes, cycle script

**`BillingSettings`:**
- `enabled` from `AURA_BILLING_ENABLED`;
- `fawry_base_url`, defaulting to sandbox;
- `fawry_merchant_code` and `fawry_secure_key`, which must both be set when enabled in production
  (`validate()`);
- `prices_json`;
- `renewal_lead_days = 3`, `grace_days = 7`, `reference_expiry_hours = 72`.

**Container:** `self.billing`, using the Fawry provider when enabled with credentials, else the
fake (local/test).

**Routes:**
- `GET /workspaces/{id}/billing`: Owner/Admin.
- `POST /workspaces/{id}/billing/checkout` `{plan, seats}`: Owner and a verified email. It is
  rate-limited through the invite budget pattern (5 per minute).
- `POST /workspaces/{id}/billing/cancel`: Owner.
- `POST /billing/fawry/notify`: public, body ≤ 16 KiB. It answers 200 `{"ok":true}` when handled
  or ignored, 403 for a bad signature, 400 for a malformed body. Add it to `PUBLIC` in
  `test_route_auth.py` with its reason.
- `POST /admin/billing/run-cycle`: SuperAdmin.
- The IDOR scenarios and reachability triage must be updated.

**`scripts/run_billing_cycle.py`:** builds the container, calls `billing.run_cycle(now)` and
prints the counts. This is for a Railway cron service.

Tests: `tests/integration/test_billing_api.py` covers:
- checkout → fake paid → notify → plan is `pro`;
- a non-owner gets 403;
- billing disabled gets 503;
- a bad signature gets 403;
- a duplicate notification is a no-op;
- the cycle expires, which drops the workspace to Free.

### Task 6: Docs

- `docs/deployment/billing-fawry.md`:
  - Fawry onboarding checklist: confirm the three formulas in staging, the notification URL
    `https://api.aureon-ai.tech/api/v3/billing/fawry/notify`, and the hosted card page for first
    card capture;
  - env vars and secrets;
  - the cron service (`python scripts/run_billing_cycle.py` hourly);
  - turning billing on;
  - prices;
  - refunds (in Fawry's dashboard);
  - what Owners see.
- ADR-034 section. Status row.
