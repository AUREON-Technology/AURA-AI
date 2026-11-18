# SaaS Phase 1 · Plan F2 — Customer screens in the app Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** The Flutter app gives customers every phase-1 surface of spec §5.5 except in-app
purchase:
- a workspace switcher;
- team settings (members, invites, roles, transfer, leave, delete);
- plan and usage;
- the web upgrade (Fawry);
- limit-reached prompts;
- accepting an invite;
- shared team chats;
- account linking;
- legal links at sign-up;
- account deletion that explains the team-owner refusal.

**Not in F2:** in-app purchase (F2b). It needs the `in_app_purchase` plugin, StoreKit and Play
configuration, and store products the owner creates. Until F2b ships, the mobile builds show the
plan and say "Upgrades are on the web for now", **without a link**, because store rules forbid
steering to outside payment.

**Architecture:**
- **One feature folder,** `lib/features/workspaces/`, with a `data/` client and `presentation/`
  pages. It follows the existing `core/*` client shape: `AuraApiClient`, JWT, attestation,
  Riverpod.
- **The active workspace** is a persisted Riverpod `Notifier` (`shared_preferences`). Every
  client that calls a workspace-scoped route sends `X-Aura-Workspace` when a **team** workspace
  is active. A personal workspace sends nothing, which is the server's default.
- **One backend change.** Members carry the email they joined with (migration `0026`), so the
  members list shows people rather than opaque IDs.
- **All user-facing text** goes in `lib/l10n/app_en.arb` (the hard-coded-text ratchet test).

**Tech stack:** Flutter 3.44.6 / Dart 3.12.2, Riverpod, go_router, `package:http`, `url_launcher`,
`firebase_auth` (linking). Backend: FastAPI + Alembic.

**Spec:** `docs/superpowers/specs/2026-11-16-saas-phase1-design.md` §5.1 (linking, deletion), §5.2
(team chats), §5.4 (Fawry on web, "Team plans are sold on the web only"), §5.5.

## Global Constraints

- "Team plans are sold on the web only. The app links out where store rules allow; otherwise it
  shows 'manage your team on the web.'" F2's mobile builds show text and no link.
- "Legal pages … The owner supplies the text; the app links to them at sign-up." URLs come from
  `--dart-define=TERMS_URL / PRIVACY_URL / REFUND_URL`. With no URL set, no link is shown (never a
  dead link).
- "Team chats … private by default. A member can **share** a conversation with the team."
- Every new string is in `app_en.arb`, and `hardcoded_text_ratchet_test.dart`'s ceiling may only
  go down.
- `flutter analyze` must be clean, including infos, and `flutter test` must pass. Both are run
  with the CI-pinned SDK before any claim. The backend suites stay green.
- **Invite tokens:** never logged or persisted. An invite link opened while signed out is held in
  memory only until sign-in.

## Review Focus

1. **The active workspace disappears** (the person was removed, or the team was deleted). The app
   falls back to personal on the next 403 from a workspace route, instead of failing every
   request. Test in Task 1.
2. **Opening an invite link signed out** leads to sign-in, then the accept page with the token
   still there, without the token being written to disk. Test in Task 4.
3. **A 402 `plan_limit` anywhere** shows the plain sentence plus a "See plans" action that opens
   the plan page for the *active* workspace. Test in Task 3.
4. **A Member** (not Owner or Admin) sees team settings read-only: no invite, role, remove or
   delete controls. Test in Task 2.
5. **Deleting an account that owns a team** shows which teams block it and how to resolve that,
   not a generic error. Test in Task 5.

---

### Task 0: Members show their email (backend, migration 0026)

**Files:**
- Create: `aura_backend/alembic/versions/0026_member_email.py`
- Modify:
  - `domain/tenancy.py` (`Membership.email: str | None = None`);
  - both tenancy adapters;
  - `tenancy/service.py` (`create_team(..., owner_email=None)`; `accept` stores the invite's
    email; `ensure_personal` leaves email empty);
  - `api/workspaces.py` (`_member` adds `email`; create passes `caller.email`);
  - the schema head pins.
- Test: extend `tests/unit/test_tenancy_service.py` and the tenancy contract test (memory and
  PG).

- [ ] **Step 1: Failing tests.**
  - `test_member_email_from_invite_and_owner`: after accept, `list_members` shows the invitee's
    invited address, and the creator shows `owner_email`.
  - The contract round-trips `email`.
- [ ] **Step 2: Implement.** `ALTER TABLE aura_workspace_members ADD COLUMN email TEXT`, with a
  working downgrade.
- [ ] **Step 3:** Run the tenancy suites, the PG contract and round trip, and `tests/security`.
- [ ] **Step 4:** Commit: `feat(teams): members carry the email they joined with`.

### Task 1: Workspaces client, the active workspace, the header

**Files:**
- Create:
  - `lib/features/workspaces/data/workspace_models.dart`;
  - `lib/features/workspaces/data/workspaces_client.dart`;
  - `lib/features/workspaces/data/active_workspace.dart`.
- Modify:
  - `lib/core/network/aura_api_client.dart` (adds `PATCH`, and the workspace header in
    `_headers`);
  - `lib/core/ai/online_agents/sse_agent_client.dart`;
  - `lib/core/ai/knowledge/knowledge_center.dart`;
  - `lib/core/ai/vision/chat_images.dart`.
- Test: `test/workspaces/workspaces_client_test.dart`, `test/workspaces/active_workspace_test.dart`

**Interfaces:**
- **Models:**
  - `WorkspaceSummary(id, kind: 'personal'|'team', name, role)`;
  - `Member(userId, role, email?, joinedAt)`;
  - `InviteSummary(inviteId, email, role, expiresAt)`;
  - `PlanUsage(plan, seats, messagesUsed, messagesAllowed, storageUsed, storageAllowed, engineer, resetsAt, seatLimit)`;
  - `BillingStatus(subscription?: SubscriptionView, payments: List<PaymentView>)`;
  - `Checkout(paymentId, referenceNumber?, amountPiastres, expiresAt?)`;
  - `SharedChatSummary(id, title, sharedBy, createdAt)`;
  - `SharedChat(summary, messages: List<(role, text)>)`.
- **`WorkspacesClient(Ref)`.** Each method maps to one route; a non-2xx response throws
  `AuraApiError`:
  - workspaces: `list()`, `createTeam(name)`, `deleteTeam(id)`;
  - members: `members(id)`, `changeRole(id, userId, role)`, `remove(id, userId)`,
    `transfer(id, userId)`, `leave(id)`;
  - invites: `invites(id)`, `invite(id, email, role) -> String token`, `revokeInvite(id, inviteId)`,
    `accept(token)`;
  - plan and billing: `plan(id)`, `billing(id)`, `checkout(id, plan, seats)`, `cancel(id)`;
  - shared chats: `shared(id)`, `sharedChat(id, sid)`,
    `share(id, sourceId, title, messages)`, `unshare(id, sid)`.
  - `leave` is `DELETE /members/{me}` (the server's leave path; confirm it in `api/workspaces.py`
    first).
- **`activeWorkspaceProvider`:** a `NotifierProvider<ActiveWorkspace, WorkspaceSummary?>` with
  `select(WorkspaceSummary)`, `clear()` and `restore()` from `shared_preferences` key
  `aura.active_workspace`.
- **`workspaceHeaders(Ref) -> Map<String,String>`:** `{'X-Aura-Workspace': id}` only when the
  active workspace is a team.
- **Self-healing.** `AuraApiClient` calls `activeWorkspaceProvider.notifier.clear()` when a
  request that carried the header gets 403 with error `authorization` on a `/workspaces/` path or
  a turn.

- [ ] **Step 1: Failing tests** (`MockClient`, overriding `tokenManagerProvider` as
  `account_deletion_test.dart` does):
  - each client call hits the right method and path with a bearer, and parses the response;
  - `invite` returns the token;
  - a non-2xx response throws `AuraApiError` carrying `kind` and `details`;
  - `test_team_active_sends_header_personal_does_not`;
  - `test_selection_survives_restart` (fake shared_preferences);
  - `test_403_on_a_workspace_route_falls_back_to_personal` (Review Focus #1).
- [ ] **Step 2:** Implement.
- [ ] **Step 3:** Run `flutter analyze && flutter test test/workspaces`.
- [ ] **Step 4:** Commit: `feat(app): workspaces client and active workspace header`.

### Task 2: Switcher and team settings

**Files:**
- Create:
  - `lib/features/workspaces/presentation/workspace_switcher.dart`, a sheet;
  - `lib/features/workspaces/presentation/team_settings_page.dart`;
  - `lib/features/workspaces/presentation/create_team_dialog.dart`.
- Modify:
  - `lib/core/routing/router.dart` (`/workspaces`, `/workspaces/:id`);
  - `lib/features/settings/presentation/settings_page.dart` (a "Workspace" tile showing the
    active one, which opens the switcher);
  - `lib/l10n/app_en.arb`.
- Test: `test/workspaces/team_settings_test.dart`, `test/workspaces/switcher_test.dart`

- [ ] **Step 1: Failing widget tests.**
  - `switcher_lists_workspaces_and_selects`.
  - `create_team_requires_verified_email_message_on_403`.
  - `owner_sees_invite_role_remove_transfer_delete`.
  - `member_sees_read_only` (Review Focus #4).
  - `invite_shows_the_token_once_with_copy_when_email_is_off`. The server emails the link when
    email is on, so the sheet says "We emailed an invite" when the response has no token.
  - `seat_limit_402_shows_plan_sentence`.
  - `leave_and_delete_confirm_first`.
- [ ] **Step 2:** Implement, using `core/design_system` components (`AuraButton`, `AuraCard`,
  `AuraTextField`, `AuraSnackbar`).
- [ ] **Step 3:** Run analyze and the tests.
- [ ] **Step 4:** Commit: `feat(app): workspace switcher and team settings`.

### Task 3: Plan and usage, web upgrade, limit prompts

**Files:**
- Create:
  - `lib/features/workspaces/presentation/plan_page.dart`;
  - `lib/features/workspaces/presentation/upgrade_sheet.dart`;
  - `lib/features/workspaces/presentation/checkout_result.dart`.
- Modify:
  - `lib/core/network/aura_failure_view.dart` (a `planLimit` failure offers "See plans" through
    an injectable `onSeePlans`);
  - the chat screen's failure rendering, so a 402 passes `onSeePlans: () => context.go('/plan')`;
  - router (`/plan`);
  - arb.
- Test: `test/workspaces/plan_page_test.dart`, `test/core/plan_limit_prompt_test.dart`

**Behaviour:**
- **The plan page** for the active workspace shows:
  - the plan, messages used of allowed (with the reset date), storage, Engineer, and seats for
    a team;
  - the subscription (status, paid through, grace end);
  - recent payments, as receipts.
- **Upgrade, on web and desktop** (`kIsWeb || isDesktop`), Owner only:
  - choose Pro (personal) or Team plus seats (team);
  - `checkout` shows the Fawry **reference number**, the amount `EGP x.yy`, the expiry and how to
    pay;
  - cancel, behind a confirmation.
- **Upgrade, on iOS and Android:** text only, "Upgrades are on the web for now." (F2b replaces
  this with IAP.)
- **When billing is off** (503 `billing_unavailable`), the plan and usage still show and the
  upgrade area says upgrades are not available yet.

- [ ] **Step 1: Failing tests.**
  - `shows_usage_and_resets`.
  - `owner_on_web_can_checkout_and_sees_reference`.
  - `mobile_shows_no_link_out`.
  - `member_cannot_upgrade`.
  - `billing_off_hides_upgrade`.
  - `plan_limit_failure_offers_see_plans` (Review Focus #3).
- [ ] **Step 2:** Implement. Amounts are integer piastres, shown as `EGP` with two decimals.
- [ ] **Step 3:** Run analyze and the tests.
- [ ] **Step 4:** Commit: `feat(app): plan and usage, Fawry upgrade on the web, limit prompts`.

### Task 4: Accepting an invite, shared team chats

**Files:**
- Create:
  - `lib/features/workspaces/presentation/invite_accept_page.dart`;
  - `lib/features/workspaces/presentation/shared_chats_page.dart`;
  - `lib/features/workspaces/presentation/shared_chat_view.dart`.
- Modify:
  - router: `/invite?token=`. The redirect while signed out keeps the token in an in-memory
    `pendingInviteProvider` and returns to `/invite` after sign-in. Add `/team-chats`.
  - The chat screen's overflow menu: "Share with team", only when a team workspace is active. It
    sends that conversation's messages from the local database.
  - arb.
- Test: `test/workspaces/invite_accept_test.dart`, `test/workspaces/shared_chats_test.dart`

- [ ] **Step 1: Failing tests.**
  - `signed_out_invite_survives_sign_in_in_memory_only` (Review Focus #2; asserts nothing is
    written to shared_preferences or secure storage).
  - `accept_success_selects_the_team`.
  - `accept_conflict_shows_expired`.
  - `unverified_email_403_explains`.
  - `share_sends_local_messages_and_confirms`.
  - `share_hidden_in_personal`.
  - `shared_list_and_view`.
  - `unshare_only_for_sharer_or_admin`.
  - `409_too_many_shares_message`.
- [ ] **Step 2:** Implement.
- [ ] **Step 3:** Run analyze and the tests.
- [ ] **Step 4:** Commit: `feat(app): accept team invites and share chats with the team`.

### Task 5: Account linking, legal links, deletion refusal

**Files:**
- Create:
  - `lib/features/auth/presentation/linked_accounts_page.dart`;
  - `lib/core/config/legal_links.dart`.
- Modify:
  - `lib/core/auth/identity_service.dart` (`linkedProviders()`, `linkGoogle()`, `linkApple()`,
    `linkEmailPassword(email, password)`, using `firebase_auth`'s `linkWithCredential` or
    `linkWithProvider`);
  - `login_page.dart` and `email_sign_in_form.dart` (legal links: "By continuing you agree to the
    Terms and Privacy Policy");
  - `account_client.dart` (409 → `AccountDeletionBlocked(workspaces)`);
  - the delete-account flow (explain and link to each team's settings);
  - settings (a "Sign-in methods" tile);
  - arb.
- Test: `test/auth/linked_accounts_test.dart`, `test/auth/legal_links_test.dart`, extend `test/account_deletion_test.dart`

**Interfaces:** `LegalLinks.fromEnvironment()` has nullable `terms`, `privacy` and `refund`. A
`null` link is not rendered.

- [ ] **Step 1: Failing tests.**
  - `lists_linked_providers_and_links_google` (with a fake `IdentityService`).
  - `credential_already_in_use_explains`.
  - `legal_links_render_only_when_configured`.
  - `delete_blocked_by_team_lists_them` (Review Focus #5).
- [ ] **Step 2:** Implement.
- [ ] **Step 3:** Run analyze and the tests.
- [ ] **Step 4:** Commit: `feat(app): sign-in methods, legal links, team-owner deletion notice`.

### Task 6: Docs and status

- `docs/status/MOBILE_APP_STATUS.md` and the desktop status rows.
- `docs/deployment/customer-sign-in.md`: the legal-link `--dart-define`s, and the web build
  serving `/invite`.
- The reachability triage for `/workspaces` now names its client callers.
- Commit: `docs: customer screens`.
