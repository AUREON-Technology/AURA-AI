# AURA as a SaaS — Phase 1 design: individuals and small teams (Egypt)

**Status:** Draft for owner approval · **Date:** 2026-11-16 · **Branch:** `claude/saas-production-readiness-f5i61d`

## 1. Intent

The owner wants to sell AURA as a SaaS. Eventually it serves five audiences:

1. an individual on a phone;
2. a small business team;
3. a company IT department;
4. developers using the API;
5. a company or start-up that wants AURA as the agent or assistant inside its own system.

Each audience needs the right way to sign in and to pay. The system has to be fit for real paying
users, not just the owner.

The audiences are built in this order (owner's decision): **(1 + 2) → 4 → 3 → 5**. This document
covers **Phase 1 only: individuals and small teams, customers in Egypt, paying in EGP.**
International customers come later.

Why this order: individuals and teams need no sales effort, and they test sign-up, billing and
limits at volume. Developers come next because service keys (ADR-029) already exist. Enterprise IT
and "AURA for your system" (Track AP) need SSO-grade security in place first.

## 2. Goals, non-goals, anti-goals

**Goals (phase 1 is done when all of these hold, with the evidence named in §9):**

- Anyone in Egypt can sign up from the phone app or the web, and sign in with Google, Apple, or
  email + password.
- Every user has a personal workspace. A user can create a Team workspace, invite people by email,
  assign roles, and remove members.
- Three plans exist: **Free**, **Pro** and **Team**. Their limits are enforced on the server.
- A user can pay in EGP:
  - on the web, through Fawry (card, mobile wallet, or *Pay at Fawry* cash reference code);
  - inside the mobile apps, through Apple and Google in-app purchase.

  Renewals, grace periods and downgrades happen automatically.
- Production meets a stated readiness floor (§6.1) before the first paying user.

**Non-goals for phase 1:**

- International customers or currencies other than EGP.
- Enterprise SSO/SAML, SCIM and data residency (phase 3).
- Self-serve developer API keys and usage-based API billing (phase 2).
- Connecting customer systems (Track AP, phase 5).
- Phone-number + SMS sign-in. Deferred to **phase 1b** because of per-SMS cost and SMS fraud. The
  design must not block it.
- Per-workspace databases.

**Anti-goals (failure even if it technically works):**

- **Paying customers' prompts or documents reach a model provider that may train on them.** Today's
  `hosted1` slot may be a `:free`, training-permitted model. That is acceptable for the owner's
  testing and unacceptable for any paying customer.
- **One workspace can read another's data**, or a team admin can read a member's private
  life-tracking data (tasks, finance, health, calendar).
- **The owner's platform roles leak into customer roles.** A customer "Admin" must never gain
  console or operator powers.
- **A merge to `main` ships to paying users with no gate.** That is how it works today (`main`
  unprotected, Railway auto-deploys).
- **Billing state depends on the payment provider staying up.** A Fawry outage must not lock out
  someone who has paid.
- **Existing users lose data or access** in the migration to workspaces.

## 3. Constraints

- **Egypt / Fawry.** The owner chose Fawry; the business is registered in Egypt. Fawry charges in
  EGP. Its API supports card tokens, but recurring billing appears to be configured on Fawry's side
  rather than exposed as endpoints. That comes from a third-party source and must be confirmed in
  merchant onboarding. The design therefore does not rely on Fawry-side subscriptions (§5.4).
- **App-store rules.**
  - Selling a digital subscription inside the iOS and Android apps generally requires Apple and
    Google in-app purchase.
  - The App Store also requires Sign in with Apple once Google sign-in is offered.
- **Existing architecture rules hold** (CLAUDE.md):
  - hexagonal layering, fitness-tested;
  - Alembic is the only schema authority: new tables come from migrations with working
    `downgrade()`;
  - every storage port keeps a real in-memory adapter;
  - the default `pytest` run needs no network.
  - The self-hosting invariant and ADR-030/033 gates still govern which model serves which plan.
- **Two deploy targets** (Railway, which serves `aureon-ai.tech` today, and the VPS). Phase 1
  launches on **Railway only**. The VPS stays out of scope until its host-key question is settled
  by the owner.
- **Evidence rule.** Nothing is "done" without the evidence its exit criterion names. Built and
  tested are not proven.

## 4. Approaches considered

### 4.1 Identity

| | Approach | Trade-off | Decision |
|---|---|---|---|
| **A** | Extend today's flow: Firebase Auth proves identity (add Apple + email/password providers); the backend keeps issuing its own access/refresh JWTs, now carrying the active workspace and the workspace role | Least rework. Sign-in and pairing flows keep working. Phase-3 SSO comes from upgrading the same Firebase project to Google Cloud Identity Platform (SAML/OIDC, multi-tenant). Depends on Google; SMS costs money | **Chosen by the owner** |
| B | Replace with Auth0 / Clerk / self-hosted Keycloak | SSO/SCIM out of the box, but replaces working flows, migrates existing users, and Keycloak is another server for a solo operator | Rejected |
| C | Build passwords, verification and OAuth in-house | Riskiest code to own; buys nothing over A | Rejected |

### 4.2 Tenancy

| | Approach | Trade-off | Decision |
|---|---|---|---|
| **A** | Shared database. Every scoped row belongs to a workspace scope; the existing explicit `owner_id` port obligation becomes the scope key. Postgres row-level security is a later second lock | Scales to thousands of free users; one migration path | **Chosen by the owner** |
| B | Database or schema per customer | Strongest isolation; unworkable for a free tier; multiplies migrations | Rejected (may return for enterprise in phase 3) |

### 4.3 Payments

| | Approach | Decision |
|---|---|---|
| **Fawry** (web) + Apple/Google IAP (in-app), AURA owns the subscription state machine | **Chosen by the owner** (Fawry over Paymob; Egypt first) |
| Paddle (merchant of record) / Stripe | Deferred to the "international customers" step. The payment-provider port (§5.4) is what makes adding one a contained change |

## 5. Design

### 5.1 Identity and sign-in

- **Providers:** Google (exists), **Apple**, **email + password**.
  - Email accounts must verify their email before they can create or join a Team workspace, or
    before they get past Free limits.
  - Password reset uses Firebase's flow.
- **Client:** sign-in screens in `aura_client/lib/features/auth/` gain Apple and email/password,
  using the `core/design_system/` kit. The web console login (`aura_admin/app/login`) is unchanged:
  it stays operator-only.
- **Backend:**
  - `POST /api/v2/auth/token` keeps verifying the Firebase ID token plus device attestation on
    mobile. A **new consumer web sign-in route** issues tokens to any verified user without Play
    Integrity, because web customers have no attestation.
  - It must not grant console access. Today `/web/token` is operator-only and stays operator-only.
- **Tokens:** the access JWT carries `sub`, the **platform role**, the **active `workspace_id`**
  and the **workspace role**.
  - Switching workspace means a refresh call that names the target workspace. The server
    re-checks membership on every refresh.
  - When a member is removed, their refresh family for that workspace is revoked. This reuses the
    existing refresh-family revocation.
- **Two role systems, kept apart:**
  - Platform roles (`services/rbac.py`: User … SuperAdmin) stay the owner's, for the console only.
  - Workspace roles are a separate set: **Owner, Admin, Member**.
  - No code path may map one onto the other. A test pins this.
- **Owner-only two-factor:** optional TOTP, through Firebase multi-factor, for workspace Owners and
  Admins. It is not required at launch.
- **One account per email (builder's call, recorded here):**
  - Firebase is set to *one account per email address*. A second provider for the same address
    is refused until it is linked from the signed-in account (amended 2026-11-17 after review:
    Firebase does not merge on its own; linking UI is Plan F). Existing subjects are kept by the
    import script and `canonical_subject`, so nobody's data moves.
  - Apple "Hide My Email" relay addresses are treated as that account's email.
  - An invite is proven by its single-use emailed token, not by matching the signed-in email.
    Opening the link proves the mailbox, and accepting requires being signed in. So a relay-address
    user can still join a team.
- **Desktop Engineer for customers (owner decision, 2026-11-16):**
  - `/desktop/login`, desktop pairing and their refresh path stop requiring a console role. They
    require instead a workspace whose entitlement includes Engineer.
  - This is a security redesign, not a flag flip. The desktop route currently skips device
    attestation because only trusted operators reach it (F-09). Opening it to the public needs:
    - a threat-model update to ADR-011;
    - per-user pairing and rate limits;
    - the existing per-call confirmation for side-effecting host tools (ADR-006), kept mandatory;
    - revocation on plan downgrade.
  - It still must never grant console access.
- **Account deletion:** `api/account.py:/delete` exists. It must also handle the case where the
  user is the sole Owner of a Team workspace: refuse until ownership is transferred, or the
  workspace is deleted.

### 5.2 Workspaces and data scoping

- **New tables (one migration):**
  - `workspaces`: id, kind `personal|team`, name, created_by, created_at, plan state reference.
  - `workspace_members`: workspace_id, user subject, role, invited_by, joined_at.
  - `workspace_invites`: email, role, token hash, expiry, accepted_at.
- **Personal workspace:** created on first sign-in. Existing users get one in the migration.
- **Scope key.** The ports already take an explicit `owner_id` for user-scoped reads; it is a
  signature-level obligation (`aura_core/ports/storage.py`). Phase 1 keeps that obligation and
  changes what is passed:
  - In a **personal workspace** the scope is the user's own id, exactly as today. **Existing data
    needs no rewrite.**
  - In a **team workspace**, shareable data (knowledge documents and corpus, Engineer repositories
    and workspaces, team chat threads) is scoped to a workspace scope id such as
    `ws_<uuid>`. That scope is distinct from any user id by construction.
  - **Personal-life data never takes the workspace scope.** That covers Personal-OS tasks,
    calendar, finance and health records, memory, and ADR-032 data keys. It stays scoped to the
    user in every workspace, so no team admin can read it.
- **Two user ids exist today.** `aura_core` ports key on the Firebase uid (`sub`). The legacy
  `/api/v3/sync` tables (`tasks`, `calendar_events`, `finance_records`, `health_records`,
  `memories`, `messages`, `projects`, `goals`) key on `users.id` (a UUID) through SQLAlchemy.
  - The personal scope keeps both as they are.
  - The sync tables stay user-keyed: they are personal by definition.
  - Shared team content lives in `aura_core` stores under the workspace scope, never in sync rows.
- **Team chats (owner decision):** private by default. A member can **share** a conversation with
  the team. Sharing copies or links it into a workspace-scoped shared-conversation store that every
  member can read. Unsharing removes it. The member's own `messages` sync rows never become
  team-readable.
- **What a turn retrieves in a team workspace (builder's call):** only the workspace's scope. The
  user's personal documents and memory are not mixed in. The user's personal-life tools
  (Personal-OS) stay available, still under their own user scope. Ports keep taking one scope, and
  the isolation suite proves exactly that.
- **A central resolver** turns `(token, request)` into the scope used for each data category. It is
  the only place that decision is made, and it is fitness-tested: no route may build a scope from
  a raw request field.
- **Invites:**
  - Invites go by email, with a single-use token. Accepting requires a signed-in account with that
    verified email.
  - Seat count is enforced at accept time, against the Team plan's seats.
- **Roles inside a team:**
  - **Owner** handles billing, deletes the workspace, and transfers ownership.
  - **Admin** invites and removes members and manages shared knowledge.
  - **Member** uses the workspace.
- **Row-level security:** a Postgres RLS policy keyed on the scope is a planned second lock, phase
  1b. Phase 1's isolation evidence is the cross-workspace test suite (§9).

### 5.3 Plans and limits

| | **Free** | **Pro** (individual) | **Team** (per seat, min 2) |
|---|---|---|---|
| Chat messages / month | low cap | higher cap | Pro cap per seat, pooled across the workspace |
| Engineer agent: desktop, on the user's own computer | ✗ | ✓ | ✓ |
| Engineer agent: server sandbox workspaces | ✗ | phase 1b | phase 1b |
| Knowledge storage | small | larger | larger, shared |
| Voice, images | per owner's later decision | ✓ | ✓ |
| Model | cheapest non-training source | better source | same as Pro |

- **Numbers and prices are the owner's to set** before launch (§8). The system treats every limit
  as data, so changing one is configuration, not code.
- **Plans follow the workspace (owner decision).**
  - Each workspace has exactly one plan.
  - A Pro user inside someone's Team workspace gets that Team's limits there, and Pro in their
    personal workspace.
  - Personal-scoped usage inside a team, such as Personal-OS tools, meters against the workspace
    the turn runs in.
- **Plans as data:** a plan catalogue holds the limits and features per plan. Enforcement reads the
  workspace's current entitlement, never a hard-coded plan name.
- **Metering:** usage is counted per workspace per billing period.
  - Counted: messages, Engineer runs, storage bytes.
  - Messages count on accepted turns. A turn the server fails does not count.
  - The existing cross-process daily quota machinery (`aura_core/llm/quota.py`, shared through the
    Cache port) is a model for the counter, but per-customer limits are a separate concern from
    per-provider quotas.
- **Limit responses:**
  - A hit limit returns a typed error, 402/429 with a reason code and the reset time. The client
    shows an upgrade prompt.
  - The Engineer agent on Free is refused before any workspace container starts.
- **Model per plan:**
  - Paid plans route only to sources that do not train on prompts. Free plans do too, since the
    anti-goal covers every customer.
  - The plan's routing choice uses the existing `ProviderRouter` pools and data classification. No
    new vendor kind is introduced by this design.
- **The self-hosted GPU source (owner decision, 2026-11-16).**
  - Today, on the live target, every memory, document, tool, Engineer and image turn has no
    eligible provider. The only healthy slot (`hosted1`) has an `internal` ceiling, and the
    sensitive-capable `hfspace` Space is down (FSA-024, PP-01).
  - Phase 1 fixes this by running a **self-hosted GPU model that is always on**. Use the existing
    `hfspace` inference plane (`deploy/spaces/inference-vllm/`) or an equivalent rented GPU behind
    the `openai_compatible` provider.
  - It is allowed `sensitive` content under the current rules. **No ADR-030 rule changes.**
  - Sizing:
    - the model, GPU and monthly cost are settled in the build;
    - "always on" means no sleep or scale-to-zero for customer traffic;
    - throughput is sized against the launch capacity target (§8).
  - A hosted open-weight slot may still serve `internal`-only chat as overflow. It must be a
    non-training model.

### 5.4 Billing

- **AURA owns the subscription.** A `subscriptions` record per workspace holds:
  - plan, status (`trialing | active | past_due | grace | canceled | expired`);
  - period start/end, provider (`fawry | apple | google`), and provider reference.

  Invoices and payment attempts are stored alongside. Entitlements derive from this record only.
- **The status machine:**
  - active → (period ends) → renewal attempt → active, or → past_due.
  - past_due → **grace period** (owner sets the length; suggested 7 days, full access) → expired.
    Expired means the workspace drops to Free limits. Nothing is deleted.
  - canceled → access until the period ends → expired.
- **The payment-provider port** in `ports/` has a real in-memory adapter for tests, and a Fawry
  adapter in `adapters/`. It covers:
  - create a checkout;
  - charge a saved card token;
  - issue a cash reference code;
  - verify and parse a webhook;
  - query a payment's status.
- **Fawry flows (web):**
  - First purchase: a Fawry checkout (card, wallet or reference code).
  - Renewal with a saved card: AURA charges the token on the renewal date, from a scheduled job.
  - Renewal without a saved card: AURA issues a new reference code a few days before the period
    ends, and notifies the Owner by email and in the app.
  - Payment confirmation arrives by webhook. The webhook's signature is verified, its handling is
    idempotent on the provider reference, and a reconciliation job queries pending payments, so a
    lost webhook cannot leave a payer locked out.
- **In-app purchase (mobile):**
  - Apple and Google subscriptions are verified server-side through their server notifications and
    receipt validation, and mapped onto the same `subscriptions` record.
  - A workspace has one active provider at a time.
  - Team plans are sold on the web only. The app links out where store rules allow; otherwise it
    shows "manage your team on the web."
- **Money:** amounts in EGP piastres (integers). Store prices for IAP are set in each store's
  console. The owner must decide whether the EGP prices match across channels (§8).
- **Owner tooling:** the console gets a read-only Customers page: workspaces, plan, status, last
  payment. It also gets one operator action, "extend grace period", which is audited.
  Refunds happen in Fawry's or the stores' own dashboards in phase 1.
- **Invoices / receipts:** a simple EGP receipt per successful payment, emailed and listed in the
  app. Whether these must be Egyptian e-invoices (ETA) is an owner/accountant question (§8).

### 5.5 Customer-facing surfaces

- **Client app:**
  - sign-up/sign-in;
  - workspace switcher;
  - team settings (members, invites, roles);
  - plan and usage screen;
  - upgrade flow (IAP on mobile, Fawry on web);
  - limit-reached prompts.
- **Web:** the Flutter web build serves customers. The Next.js console stays operator-only.
- **Email:** transactional email for verification, invites, receipts, renewal reminders and
  past-due notices. It needs a sending provider (owner choice, §8). Firebase covers only its own
  auth emails.
- **Legal pages:** terms of service, privacy policy, refund policy. These are required by Fawry, the
  app stores, and Egyptian consumer and data-protection law (Law 151/2020). The owner supplies the
  text; the app links to them at sign-up.

## 6. Production readiness floor

### 6.1 Before the first paying user

Each item needs its named evidence, not just a merged PR.

1. **`main` protected; CI is required and actually runs** (CB-44). Evidence: a Backend CI run on a
   PR touching `aura_backend/`, executed and green; a branch-protection rule in place. Railway's
   auto-deploy must deploy only after CI, or be switched to deploy from the CI workflow.
2. **No training-permitted model serves customers.** Evidence: the live provider list shows no
   `:free`/`ALLOW_PROMPT_TRAINING=true` slot in any customer routing pool.
3. **`/metrics` and `/docs` not public** on the live target. Evidence: unauthenticated request
   returns 401/404.
4. **Backups and restore on the live database.** Evidence: an offsite restore drill on Railway's
   Postgres within a stated RPO/RTO (GAP-028/046), with a report in `docs/reports/`.
5. **Monitoring and alerting** for the live target: errors, latency, provider health, payment
   webhook failures. Evidence: an alert firing in a test (GAP-036/037).
6. **Capacity measured** for a stated launch target (owner sets it, §8), with a load run against
   the live topology (GAP-011/041).
7. **Secrets:** SEC-03 (committed OAuth secret) rotated and removed.
8. **Cross-workspace isolation suite green** (§9).
9. **The sensitive-capable GPU source is live and healthy.** Evidence:
   - a passing `deploy/scripts/verify-hosted-inference.sh` (or equivalent) report committed for
     it;
   - live `/health/ready` showing it healthy;
   - a document/RAG turn served end to end on production.
10. **Desktop Engineer opened safely.** Evidence: the ADR-011 threat-model amendment merged; tests
    showing a Free or downgraded workspace cannot pair or refresh a desktop session; tests showing
    a customer desktop session never reaches a console route.

### 6.2 Can follow launch

- Server-side Engineer sandbox workspaces for customers (ADR-013/014). These need a Docker host.
  Railway must keep `AURA_SANDBOX_ENABLED=false`.
- Postgres RLS (second lock).
- Phone + SMS sign-in.
- The VPS target.
- International payments.
- Postgres full-text search at 200k chunks.

## 7. Build order

1. **Readiness floor items 1–3 and 7.** These are cheap, and they unblock every safe merge after
   this.
2. **Workspaces + scope resolver + migration** (no UI change for personal users).
3. **Sign-in providers** (Apple, email/password, consumer web token) and client screens.
4. **Plan catalogue, entitlements, metering, limit responses.** Payments are not needed to test
   these: an operator can set a plan by hand.
5. **Billing:** subscriptions state machine, payment port + in-memory adapter, then Fawry, then
   Apple/Google IAP.
6. **Client and console surfaces**, transactional email, legal links.
7. **Desktop Engineer opened to entitled customers** (ADR-011 amendment first).
8. **Self-hosted GPU source live** (can start as early as step 1; it does not depend on the
   others).
9. **Readiness floor items 4–6, 8–10** on the live target; then launch.

**Start now, in parallel (owner actions with long lead times):**

- Fawry merchant onboarding (confirm: card tokenization, recurring support, webhook signing,
  settlement terms).
- An Apple Developer + Google Play merchant account set up for subscriptions in Egypt.
- Legal pages.
- Choosing the paid inference source.

## 8. Owner decisions still open (needed before launch, not before building)

1. Limits and prices for Free, Pro and Team; minimum seats; grace-period length; whether a Pro
   trial exists.
2. Which GPU and model for the self-hosted source, and its monthly cost. The direction (own GPU)
   is decided; the choice of model and hardware is still open.
3. Launch capacity target (concurrent users).
4. Transactional email provider.
5. Whether receipts must be ETA e-invoices; whether VAT applies to the plans.
6. Whether IAP prices match web prices.
7. Legal texts.

## 9. Evidence that proves phase 1

- **Cross-workspace isolation tests:** for every scoped port and every v3 route that reads scoped
  data, a member of workspace X gets nothing of Y. A team admin cannot read a member's Personal-OS
  records.
- **Role separation test:** no workspace role grants a console route; no platform role is
  derivable from a workspace role.
- **Migration on a real Postgres** (`alembic upgrade head` and a downgrade round-trip). Every
  existing user ends with exactly one personal workspace whose scope equals their old `owner_id`.
- **Billing state-machine tests** on the in-memory payment adapter: renewal, failure, grace,
  expiry, cancel, duplicate webhook, lost webhook recovered by reconciliation.
- **A Fawry staging run:** checkout → webhook → active; reference code → paid → active. Recorded
  in `docs/reports/`.
- **An IAP sandbox purchase** on each store mapped to an active subscription.
- **Client:** `flutter analyze` + `flutter test` counts quoted. **Backend:** `pytest` and the
  legacy `unittest` suites green, in CI.
- **Readiness floor items** with their named evidence (§6.1).

## 10. Left to the builder

- Exact table columns, indexes, and route names. Keep v3 for new customer routes; the
  `api/v2/auth` exception stays.
- How the scope resolver is wired into `container.py` and the kernel's per-turn context.
- Scheduler mechanism for renewals and reconciliation (Celery beat exists in the stack; Railway
  needs a cron or worker equivalent).
- Error codes and client copy for limit prompts.
- Splitting this into reviewable PRs along §7.
