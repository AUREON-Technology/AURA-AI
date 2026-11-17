# Billing with Fawry: setup and operations (SaaS P1-D1)

AURA keeps each workspace's subscription itself; Fawry only collects payments.

1. **Checkout.** A workspace Owner chooses Pro or Team and gets a **Fawry reference code**. It can
   be paid in cash at any Fawry outlet, from a mobile wallet, or in the myFawry app.
2. **Activation.** Fawry's signed server notification activates the plan.
3. **Renewal.** Before each period ends, an hourly job issues the renewal: it charges a saved
   card when Fawry sent one, otherwise it issues a new reference code.
4. **Missed payment.** An unpaid subscription has **7 days of grace with full access**, then the
   workspace returns to Free. Nothing is deleted.

Billing is **off** until step 5 below. Every billing route answers 503 until then.

## 1. Fawry onboarding: confirm these with Fawry before going live

The adapter (`aura_core/adapters/fawry.py`) implements the formulas from Fawry's documentation and
the open-source `fawry-api` client. **Run one staging payment end to end** and confirm:

- **Charge** (`POST payments/charge`): the signature is
  `sha256(merchantCode + merchantRefNum + customerProfileId + paymentMethod + amount(2dp) + cardToken? + secureKey)`.
- **Status** (`GET payments/status`): the signature is
  `sha256(merchantCode + merchantRefNumber + secureKey)`.
- **Server Notification V2:** `messageSignature` is
  `sha256(fawryRefNumber + merchantRefNum + paymentAmount(2dp) + orderAmount(2dp) + orderStatus + paymentMethod + paymentRefrenceNumber? + secureKey)`.
- **Recurring or saved cards.** Ask Fawry how a customer's card is tokenised for renewals, and
  whether the notification carries `cardToken`. Until a card is saved, renewals use reference
  codes, which always work. **First-time card capture** (Fawry's hosted card page) is not built
  yet; its signature spec is the open item from Plan D1.

## 2. Notification URL

In the Fawry merchant portal, set the server notification (V2) URL to:

```
https://api.aureon-ai.tech/api/v3/billing/fawry/notify
```

The route:
- refuses anything without a valid signature (403);
- acknowledges every valid notification with 200, so Fawry stops retrying;
- applies each payment exactly once.

## 3. Railway variables (service `AURA-AI`)

| Variable | Value |
|---|---|
| `AURA_FAWRY_MERCHANT_CODE` | from Fawry |
| `AURA_FAWRY_SECURE_KEY` | from Fawry. **A secret**: add it as a sealed variable; it is never logged or shown on `/admin/status` |
| `AURA_FAWRY_BASE_URL` | staging (default) `https://atfawry.fawrystaging.com/ECommerceWeb/Fawry/`; production `https://www.atfawry.com/ECommerceWeb/Fawry/` |
| `AURA_PLAN_PRICES` | optional. JSON in piastres per month, e.g. `{"pro": 15000, "team": 12000}` (team is per seat). The defaults are placeholders |
| `AURA_BILLING_GRACE_DAYS` / `AURA_BILLING_RENEWAL_LEAD_DAYS` / `AURA_FAWRY_REFERENCE_EXPIRY_HOURS` | optional: 7 / 3 / 72 |

## 4. The hourly cycle (a Railway cron service)

Create a second service from the same repository:
- **Root:** `aura_backend`.
- **Variables:** the same as `AURA-AI`, or reference them.
- **Start command:** `python scripts/run_billing_cycle.py`.
- **Cron schedule:** `7 * * * *` (hourly).

The cycle does three things:
- issues renewals;
- moves unpaid subscriptions past due → grace → expired (Free);
- asks Fawry about any payment still pending after 10 minutes, so a lost notification cannot lock
  a payer out.

It prints counts as JSON and exits 1 when something must be retried, which the next hour does.

A SuperAdmin can also run it by hand: `POST /api/v3/admin/billing/run-cycle`.

## 5. Turn it on

Set `AURA_BILLING_ENABLED=true` **only with** the merchant code and secure key set. A production
deploy refuses to start with billing on and no credentials.

## What Owners and operators see

- **Owner:**
  - `POST /api/v3/workspaces/{id}/billing/checkout` with `{"plan": "pro"|"team", "seats": n}`.
    It returns a reference code, the amount in piastres and its expiry. The Owner needs a verified
    email. Limited to 5 per minute.
  - `GET /api/v3/workspaces/{id}/billing` (Owner or Admin) shows the subscription and the last 10
    payments. Card tokens are never shown.
  - `POST /api/v3/workspaces/{id}/billing/cancel` keeps access to the end of the paid period, and
    nothing renews.
- **A paid amount that differs from what was asked** activates nothing. The payment is marked
  `mismatch` and audited (`billing.mismatch`); look at it in Fawry's dashboard.
- **Refunds** are done in Fawry's dashboard. AURA does not issue them.
- **A plan set by hand** (`PUT /api/v3/admin/workspaces/{id}/plan`) is replaced by the next paid
  or expired subscription event for that workspace.

## Not built yet

- Apple and Google in-app purchase (Plan D2).
- Fawry's hosted first-time card page (see section 1).
- The app's upgrade screens (Plan F). Until then an Owner can only check out through the API.
