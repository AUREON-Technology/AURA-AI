# SaaS Phase 1 · Plan F1 — Backend for the customer surfaces Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

Plan F (spec §5.5, §5.4 "Owner tooling", §5.1 "Account deletion", §5.2 "Team chats") is split in
three:
- **F1, this plan:** the backend.
- **F2:** the Flutter screens. They are a workspace switcher, team settings, plan and usage,
  upgrade (Fawry on web, IAP on mobile), limit prompts, invite accept, shared chats, account
  linking, and legal links at sign-up.
- **F3:** the console's read-only Customers page and the "extend grace" action.

F2 and F3 call the routes this plan adds.

**Goal:** The server side of every customer surface:
- transactional email for invites, receipts, renewal reminders and past-due notices;
- team chats a member can share and unshare;
- account deletion that refuses while the person owns a team;
- an operator Customers API with an audited "extend grace" action.

**Architecture:**
- **Email.** An `EmailSender` port with in-memory, log and SMTP adapters. SMTP is stdlib
  `smtplib` run in a thread, so it works with whichever provider the owner picks (§8 #4: Resend,
  SES, Mailgun and Brevo all speak SMTP). A `Notifier` engine renders the messages and is given
  to `TenancyService` and `BillingService` as an optional dependency. A failed email never fails
  the operation that sent it.
- **Billing contact.** The Owner's verified address is stored per workspace (`aura_billing_contacts`,
  migration `0024`) at checkout and at store-token time, because billing email needs an address
  and workspaces store none.
- **Shared chats.** A workspace-scoped store (`aura_shared_conversations`, migration `0025`). The
  client posts a copy of its own chat. The server's episodic transcript is a 12-turn window, not
  the conversation, so it cannot be the source. Re-sharing replaces; unsharing deletes.

**Tech stack:** FastAPI, `aura_core` ports and adapters with in-memory and Postgres stores,
Alembic, stdlib `smtplib` and `email.message`.

**Spec:** `docs/superpowers/specs/2026-11-16-saas-phase1-design.md` §5.1, §5.2, §5.4, §5.5.

## Global Constraints

- "Transactional email for verification, invites, receipts, renewal reminders and past-due
  notices … Firebase covers only its own auth emails." Verification stays Firebase's.
- "A member can **share** a conversation with the team … every member can read. Unsharing removes
  it. The member's own `messages` sync rows never become team-readable."
- "Account deletion … sole Owner of a Team workspace: refuse until ownership is transferred, or the
  workspace is deleted."
- "The console gets a read-only Customers page: workspaces, plan, status, last payment … one
  operator action, 'extend grace period', which is audited."
- Email is **off** (`AURA_EMAIL_ENABLED`) until configured. With it off, nothing is sent, nothing
  fails, and the invite route still returns its token.
- A secret is a property read from an env var named by a field, never a field itself:
  `smtp_password_env`, default `AURA_SMTP_PASSWORD`.
- An invite token never appears in a log line, an audit record or an error, only in the email
  body.
- Alembic only, with a working `downgrade()`, and the round-trip test covers `0024` and `0025`.
  The in-memory and Postgres adapters share one contract suite.
- Add every new route to the reachability triage, IDOR `COVERED`, or `PUBLIC` lists.

## Review Focus

1. **Header injection.** An invitee address or workspace name containing CR/LF must not inject
   headers. Addresses are validated and the subject is single-line. Tests in Tasks 1 and 2.
2. **SMTP down.** The invite is still created and its token returned, a payment still applies, and
   the failure is logged and audited `email.failed`. Test in Task 2.
3. **A shared chat that is too big,** or has a role other than `user`/`assistant`: 422 before
   anything is stored. Test in Task 3.
4. **A removed member's shares stay** (they are the team's now), but the removed person can no
   longer read or delete them. Test in Task 3.
5. **Extend grace on a Fawry subscription that already expired** restores its plan, and on a store
   subscription it is refused (the store owns that one). Test in Task 5.

---

### Task 1: Email port, adapters, settings, `Notifier`

**Files:**
- Create: `aura_core/ports/email.py`, `aura_core/adapters/email.py`, `aura_core/notifications/__init__.py`, `aura_core/notifications/service.py`
- Modify: `aura_core/config/settings.py` (`EmailSettings`, `AuraSettings.email`, `validate`), `aura_core/container.py` (`self.email`, `self.notifier`)
- Test: `tests/unit/test_email_and_notifier.py`

**Interfaces:**
- **Port (stdlib):**
  - `EmailMessage(to: str, subject: str, text: str, tag: str)`, a frozen dataclass;
  - `EmailSender.send(message) -> None` (async).
- **Adapters:**
  - `InMemoryEmailSender`, whose `.outbox: list[EmailMessage]` is for tests;
  - `LogEmailSender`, which logs `tag` and the recipient's domain only;
  - `SmtpEmailSender(host, port, username, password, from_address, starttls=True, timeout_s=15)`.
- **`EmailSettings`** fields: `enabled=False`, `from_address=""`, `smtp_host=""`, `smtp_port=587`,
  `smtp_username=""`, `smtp_password_env="AURA_SMTP_PASSWORD"`, `smtp_starttls=True`,
  `app_base_url=""`. Property `smtp_password`. Env names are `AURA_EMAIL_ENABLED`,
  `AURA_EMAIL_FROM`, `AURA_SMTP_HOST`, `AURA_SMTP_PORT`, `AURA_SMTP_USERNAME`,
  `AURA_SMTP_STARTTLS` and `AURA_APP_BASE_URL`.
- **`Notifier(sender, settings, audit, clock)`**. Each method is async, returns `None` and never
  raises:
  - `invite(to, workspace_name, inviter_email, role, token)`: link
    `{app_base_url}/invite?token={token}`;
  - `receipt(to, workspace_name, plan_id, amount_piastres, paid_at, period_end, payment_id)`;
  - `renewal_due(to, workspace_name, amount_piastres, reference_number, expires_at)`;
  - `past_due(to, workspace_name, grace_until)`.
- **Container:**
  - `self.email` is `SmtpEmailSender` when enabled, `LogEmailSender` otherwise;
  - `self.notifier` is `Notifier`, or `None` when email is off.

- [ ] **Step 1: Failing tests.**
  - `test_invite_email_carries_the_link_and_no_token_in_the_audit`: the outbox has one message.
    Its text contains `https://app.example/invite?token=T0K`, its subject names the workspace,
    and no audit detail contains `T0K`.
  - `test_receipt_formats_egp`: 15000 piastres renders as `EGP 150.00`.
  - `test_sender_failure_is_swallowed_and_audited`: a sender that raises leaves `invite()` not
    raising, and the audit gains `email.failed` with the `tag`.
  - `test_header_injection_refused`: a recipient `"a@b.c\r\nBcc: x@y.z"` and a workspace name
    with `\n` mean nothing is sent and `email.failed` is audited. The subject never contains a
    newline.
  - `test_smtp_sender_builds_a_starttls_message`: run against a fake `smtplib.SMTP` class injected
    through `smtp_factory`. Assert starttls, login with the password, and the From/To/Subject
    headers.
  - `test_email_on_without_host_or_from_is_refused_at_startup`.
  - `test_smtp_password_is_not_a_field`.
- [ ] **Step 2:** Run them and watch them fail.
- [ ] **Step 3: Implement.**
  - Amounts render as `EGP {p//100}.{p%100:02d}`.
  - English text only for now (Arabic copy comes with F2's localisation).
  - Validate addresses with `email.utils.parseaddr`, refusing any CR/LF.
- [ ] **Step 4:** Run the tests and `tests/architecture`.
- [ ] **Step 5:** Commit: `feat(email): EmailSender port, SMTP adapter and Notifier`.

### Task 2: Send them: invites, receipts, renewal reminders, past-due (migration 0024)

**Files:**
- Create: `alembic/versions/0024_billing_contacts.py`
- Modify:
  - `domain/billing.py` (`BillingContact`);
  - `ports/billing.py` (`BillingContactStore`);
  - `adapters/billing.py`, `adapters/postgres_billing.py`;
  - `billing/service.py`, `billing/stores.py`;
  - `tenancy/service.py`, `api/workspaces.py`;
  - `container.py`;
  - `tests/unit/test_schema_readiness.py` (head pins to `0024`).
- Test: `tests/unit/test_billing_contacts_contract.py` (memory and PG), `tests/unit/test_billing_notifications.py`

**Interfaces:**
- `BillingContactStore.put(workspace_id, email, at)` and `.get(workspace_id) -> str | None`.
  The table is `aura_billing_contacts(workspace_id PK FK→aura_workspaces ON DELETE CASCADE, email, updated_at)`.
- `TenancyService.invite(..., inviter_email: str | None = None)`. With a `notifier`, it sends
  `Notifier.invite` after the invite is stored. `TenancyService.notifier: Any = None`.
- `BillingService.contacts: Any` and `BillingService.notifier: Any = None`.
- **`checkout(..., email=)`** stores the contact when an email is given. The `store-token` route
  does too, using the caller's verified email.
- **Sends:**
  - `_apply` sends `receipt` (Fawry only);
  - `_cycle_one` sends `renewal_due` after a **reference-code** renewal is issued (not a card
    charge);
  - `_cycle_one` sends `past_due` when the subscription moves to `PAST_DUE` or `GRACE`.

- [ ] **Step 1: Failing tests.**
  - `test_invite_route_emails_the_invitee`: with email enabled and an in-memory sender, `POST
    /workspaces/{id}/invites` still returns the token, and the outbox holds one message to the
    invitee.
  - `test_smtp_down_does_not_fail_the_invite_or_the_payment`.
  - `test_paid_checkout_emails_a_receipt_to_the_contact`.
  - `test_reference_renewal_emails_the_code_and_card_renewal_does_not`.
  - `test_past_due_and_grace_email_once_each`: running the cycle twice adds no second email.
  - The contract test covers the round trip, replacing an address, and cascade on workspace
    delete (PG).
- [ ] **Step 2:** Run them and watch them fail.
- [ ] **Step 3: Implement.**
  - The migration has a working `downgrade()`.
  - Email is idempotent per (workspace, tag, payment or period). Only send when the state
    transition happened in this call, so a repeated cycle sends nothing.
- [ ] **Step 4:** Run the tests, the billing and tenancy suites, and the PG round trip.
- [ ] **Step 5:** Commit: `feat(email): invite, receipt, renewal and past-due emails`.

### Task 3: Shared team conversations (migration 0025)

**Files:**
- Create:
  - `domain/sharing.py`, `ports/sharing.py`;
  - `adapters/sharing.py`, `adapters/postgres_sharing.py`;
  - `sharing/__init__.py`, `sharing/service.py`;
  - `api/shared_conversations.py`;
  - `alembic/versions/0025_shared_conversations.py`.
- Modify: `container.py`, `api/__init__.py` (register routes), `tests/unit/test_schema_readiness.py`, the security lists, `scripts/api_reachability.py`
- Test: `tests/unit/test_shared_conversations_contract.py`, `tests/unit/test_sharing_service.py`, `tests/integration/test_shared_conversations_api.py`

**Interfaces:**
- **`SharedConversation`:**
  - fields `id`, `workspace_id`, `shared_by`, `source_id`, `title`, `messages`, `created_at`;
  - `messages` is a tuple of `SharedMessage(role: "user"|"assistant", text: str)`.
- **Store port:**
  - `put(conv)`, replacing any row with the same `(workspace_id, shared_by, source_id)`;
  - `get(workspace_id, id)`;
  - `list(workspace_id, limit)`, metadata only;
  - `delete(workspace_id, id) -> bool`.
- **`SharingService(store, tenancy, clock, audit)`.** Every method resolves membership first:
  - `share(actor, workspace_id, source_id, title, messages)` works in team workspaces only;
  - `list(actor, workspace_id)`;
  - `get(actor, workspace_id, id)`;
  - `unshare(actor, workspace_id, id)` is allowed for the sharer, the Owner or an Admin.
- **Limits:** `MAX_SHARED_MESSAGES = 200`, `MAX_SHARED_CHARS = 200_000` total,
  `MAX_TITLE_CHARS = 120`.
- **Routes** under `/api/v3/workspaces/{id}/shared-conversations`:
  - `POST` returns 201;
  - `GET` lists;
  - `GET /{sid}` returns one;
  - `DELETE /{sid}` returns 204.

- [ ] **Step 1: Failing tests.**
  - `test_member_shares_and_every_member_reads`.
  - `test_resharing_replaces_not_duplicates`.
  - `test_personal_workspace_cannot_share` → 422.
  - `test_unshare_by_sharer_owner_or_admin_only`: another Member gets 403.
  - `test_too_big_or_bad_role_is_422_and_stores_nothing`.
  - `test_removed_member_loses_access_but_share_stays`.
  - `test_outsider_gets_403_same_as_missing`.
  - The contract covers the round trip, replacement, delete, and cascade on workspace delete
    (PG).
  - The IDOR entries are `bob_cannot_read/share/unshare_in_alices_team`.
- [ ] **Step 2:** Run them and watch them fail.
- [ ] **Step 3:** Implement. Store `messages` as `JSONB` in PG.
- [ ] **Step 4:** Run the tests, `tests/security`, `scripts/api_reachability.py`, and the PG
  round trip.
- [ ] **Step 5:** Commit: `feat(teams): share a chat with the team`.

### Task 4: Account deletion refuses while the person owns a team

**Files:**
- Modify: `api/account.py`
- Test: `tests/integration/test_account_delete_team_owner.py`

**Interfaces:** `TenancyService.sole_ownerships(user_id) -> list[Workspace]` exists. The route
reaches it through `request.app.state.aura.tenancy`.

- [ ] **Step 1: Failing tests.**
  - `test_owner_of_a_team_gets_409_naming_it`: the body is `{"error": "owns_team_workspaces",
    "workspaces": [{"id", "name"}]}` and nothing is erased.
  - `test_after_transfer_deletion_proceeds`.
  - `test_after_deleting_the_team_deletion_proceeds`.
  - `test_personal_only_user_unaffected`.
- [ ] **Step 2:** Run them and watch them fail.
- [ ] **Step 3: Implement.** Check before `erase_account`. With no container (legacy tests), skip
  the check.
- [ ] **Step 4:** Run the tests plus the legacy `python -m unittest` account tests.
- [ ] **Step 5:** Commit: `feat(account): refuse deletion while owning a team`.

### Task 5: Operator Customers API and "extend grace"

**Files:**
- Modify:
  - `ports/billing.py` (`SubscriptionStore.list_page`);
  - both subscription adapters;
  - `billing/service.py` (`customers`, `extend_grace`);
  - `api/billing.py` (admin routes);
  - the security lists and triage.
- Test: `tests/unit/test_billing_stores_contract.py` (`list_page`), `tests/integration/test_customers_admin_api.py`

**Interfaces:**
- **`SubscriptionStore.list_page(limit: int, after: str | None) -> list[Subscription]`,** ordered
  by `workspace_id`.
- **`BillingService.customers(limit, after) -> dict`.** Its rows hold `workspace_id`,
  `workspace_name`, `kind`, `plan`, `seats`, `status`, `provider`, `period_end` and
  `grace_until`, plus `last_payment {status, amount_piastres, at}` or `None`. It also returns
  `next`.
- **`BillingService.extend_grace(actor, workspace_id, days: int) -> dict`,** with `days` 1–30:
  - it applies to Fawry subscriptions in `past_due`, `grace` or `expired`;
  - it sets `status=grace` and `grace_until=max(grace_until or now, now)+days`;
  - from `expired`, it restores the entitlement to the subscription's plan;
  - it is audited `billing.grace_extended`, with the actor and days;
  - a store or active subscription → 409.
- **Routes** (SuperAdmin):
  - `GET /api/v3/admin/customers?limit=&after=`;
  - `POST /api/v3/admin/customers/{workspace_id}/extend-grace {"days": n}`.

- [ ] **Step 1: Failing tests.**
  - `test_customers_lists_paying_workspaces_with_last_payment`.
  - `test_pagination`.
  - `test_non_superadmin_403`.
  - `test_extend_grace_from_past_due_and_expired_restores_plan`.
  - `test_extend_grace_refused_for_store_or_active` → 409.
  - `test_days_out_of_range_422`.
  - `test_extend_grace_is_audited`.
- [ ] **Step 2:** Run them and watch them fail.
- [ ] **Step 3:** Implement, under the workspace lock.
- [ ] **Step 4:** Run the tests and the PG contract.
- [ ] **Step 5:** Commit: `feat(billing): operator Customers API and audited grace extension`.

### Task 6: Docs

- `docs/deployment/email.md`:
  - picking a provider's SMTP;
  - the env vars;
  - SPF, DKIM and DMARC on the sending domain;
  - what is sent and when;
  - `AURA_APP_BASE_URL` for invite links.
- ADR-034 sections for shared chats, deletion and the operator tools.
- Status rows and the spec pointers.
- Commit: `docs: email setup, shared chats, customers tooling`.
