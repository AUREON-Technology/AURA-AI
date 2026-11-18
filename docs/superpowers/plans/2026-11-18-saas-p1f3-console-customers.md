# SaaS P1-F3: Console Customers Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the operator console the spec's "Owner tooling": a read-only Customers page and one audited action, "Extend grace".

**Architecture:** One new client page under `app/(console)/customers/`, built like `approvals/page.tsx`. It reads `GET /admin/customers` page by page and posts `POST /admin/customers/{id}/extend-grace`. The mock gateway gains both routes, with the same gates and refusals as `aura_core/billing/service.py`, so the e2e suite fails on a console that only works against a permissive mock.

**Tech Stack:** Next.js 15 / React 19, Tailwind, Playwright e2e against `tests/e2e/mock-gateway.mjs`.

**Spec:** `docs/superpowers/specs/2026-11-16-saas-phase1-design.md` §5.4 "Owner tooling". The backend is from Plan F1 (`aura_core/api/billing.py:161-170`, `BillingService.customers` / `extend_grace`).

## Global Constraints

- Both routes are SuperAdmin-only (`require_super_admin`). The console must not call either one for anyone else, so a non-SuperAdmin sees an explanation instead of a 403 banner.
- Amounts are piastres. Display them as EGP with two decimals (`amount_piastres / 100`).
- `days` is an integer from 1 to 30. The gateway answers 422 outside that range and 409 for any subscription that is not Fawry in `past_due`, `grace` or `expired`, or whose last payment was refunded.
- Every user-visible string is a dictionary key in both `en.ts` and `ar.ts` (Egyptian Arabic, the same register as the rest of `ar.ts`). `tsc` fails on a missing key.
- `npm run lint` (warnings fail it), `npm run build` and `npm run test:e2e` must all pass.

## Review Focus

1. A 409 from extend-grace, for example a race where the subscription was paid meanwhile, must show the gateway's message inside the dialog and leave the row unchanged. Test: the mock's refunded-payment customer stays in `past_due` and shows the message.
2. Store subscriptions (`apple`/`google`) and `active` ones get no action button. Test: there is no "Extend grace" button in those rows.
3. "Load more" appends the next page; it must not replace it or repeat rows. Test: with a page size of 2 in the mock, the 3rd customer appears after "Load more" and the 1st is still there.
4. A non-SuperAdmin (Admin) opening `/customers` sees the SuperAdmin-only note and the page makes no request to `/admin/customers`. Test: the mock records list calls, and an Admin's visit adds none.
5. A days value outside 1–30, or not a whole number, is refused in the console before posting. The gateway check stays authoritative. Test: typing 31 shows the range message and the row is unchanged.

---

### Task 1: Mock gateway routes

**Files:**
- Modify: `aura_admin/tests/e2e/mock-gateway.mjs`

**Interfaces:**
- Produces:
  - `state.customers` (4 rows):
    - `ws-ali`: Fawry, `past_due`, last payment failed;
    - `ws-team`: Team, Fawry, `active`;
    - `ws-ios`: Apple, `grace`;
    - `ws-refund`: Fawry, `past_due`, last payment refunded.
  - `GET /api/v3/admin/customers?limit&after` (`coreGate` SuperAdmin; it pages in `workspace_id` order and returns `next` when the page is full).
  - `POST /api/v3/admin/customers/{id}/extend-grace` (SuperAdmin; 422 / 409 as in the Constraints).
  - `GET /__test/customer-lists` returns `{calls}`.

- [ ] Add the state, the routes and the hook; restart the mock and check it with `curl` using `super-access`.

### Task 2: Page, nav entry, strings, and dialog body slot

**Files:**
- Create: `aura_admin/app/(console)/customers/page.tsx`
- Modify:
  - `aura_admin/src/lib/types.ts` (`Customer`, `CustomersPage`);
  - `aura_admin/src/lib/nav.ts` (a `nav.customers` entry in governance, with a `CreditCard` icon);
  - `aura_admin/src/lib/i18n/{en,ar}.ts` (`nav.customers`, `customers.*`);
  - `aura_admin/src/components/confirm-dialog.tsx` (a new optional `body?: ReactNode` prop, rendered above the confirm-word field; it is the place for the days input).
- Test: `aura_admin/tests/e2e/customers.spec.ts`

**The page:**
- It calls `fetchMe()` first. If `!is_super_admin`, it renders `customers.superAdminOnly` and never lists.
- Otherwise it lists with `/admin/customers?limit=50` and adds `&after=<next>` for "Load more".
- Columns: workspace (name, or the id when there is no name), kind, plan × seats, status (Badge: `active`/`trialing` ok, `past_due`/`grace` warn, `expired`/`canceled` bad), provider, period end or grace until, and last payment (EGP amount, status and date, or "—").
- The action is shown when `provider === "fawry"` and the status is `past_due`, `grace` or `expired`. It opens a `ConfirmDialog` whose `body` is a number input labelled "Days of grace" (default 7). The confirm word is `extend`.
- `onConfirm` checks that the days value is an integer from 1 to 30, and throws an `Error(t("customers.daysRange"))` otherwise. It then POSTs `{days}` and patches that row with `{status: "grace", grace_until}`.

**Steps:**
- [ ] Write `customers.spec.ts`, with the tests for Review Focus items 1–5 plus the happy path: SuperAdmin extends `ws-ali` by 5 days, after which the row shows `grace` and the grace-until date. Each item gets its own test.
- [ ] Run `npx playwright test tests/e2e/customers.spec.ts --trace off`. The tests must fail because there is no page yet.
- [ ] Implement.
- [ ] Run lint, build, and the whole e2e suite with `--trace off`; everything must pass, including `arabic.spec.ts` and the palette.
- [ ] Commit.

### Task 3: Reachability and docs

- [ ] Run `python scripts/api_reachability.py` from `aura_backend/`. Both admin customer routes now have a console caller; if the TRIAGE list in that script has entries for them, remove the entries.
- [ ] Update the Customers line in `docs/status/` (the web console file), and the console page count in `CLAUDE.md` (10 → 11 pages).
- [ ] Commit.
