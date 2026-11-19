# SaaS Phase 1 · Plan B — Sign-in providers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Individuals can sign up and sign in with Google, Apple, or email + password, on the phone
apps and on the web. Each person keeps one account and one set of data whichever method they use.
A web customer's session can never reach the operator console.

**Architecture:**
- **One identity layer.** All three methods go through Firebase Authentication. The client sends
  the Firebase ID token to the backend's existing exchange: `/api/v3/auth/token` with device
  attestation on mobile, or a new `/api/v3/auth/consumer/token` without it on the web.
- **Existing accounts.** Today's users are keyed by their Google `sub`. A one-time import script
  creates each of them in Firebase with `uid = <their current subject>`, so Firebase sign-in
  returns the subject their data is already stored under. No stored data is rewritten.
- **Customer web sessions.** They carry `channel: consumer_web`. Role resolution caps every such
  identity at `User`, at issue, on refresh and in the gateway's per-request resolution.
- **Verified email.** It travels on the access token, so team actions can require it.

**Tech Stack:** Python 3.11 / FastAPI (backend); Flutter 3.44.6 / Dart 3.12.2 with
`firebase_auth`, `sign_in_with_apple`, Riverpod (client).

**Spec:** `docs/superpowers/specs/2026-11-16-saas-phase1-design.md` (§5.1; §3 app-store rules; §5.2
for verified email on teams).

## Global Constraints

- **Platform roles never come from a customer sign-in.**
  - A `consumer_web` identity resolves to exactly `User` in `services/rbac.get_user_role`,
    whatever the email lists, `roles.json`, Access-page assignment or claimed role say.
  - Console sign-in (`/web/token`) and desktop sign-in are unchanged and stay operator-only.
- **Email grants stay SEC-08-safe.** `email_grant_eligible` still requires `email_verified` and
  `sign_in_provider == "google.com"`. Apple and password sign-ins never match an email list.
- **Subjects never change.** An existing person's `auth_subject` is their Firebase uid after
  import. A new person's subject is the Firebase uid Firebase assigns.
- **The desktop (Linux/Windows) loopback flow and the operator console are not touched.**
- **Client checks.** `flutter analyze` (info-level lints fail it) and `flutter test` must be green.
  Quote the counts.
- **Backend checks.** `pytest` and the legacy `unittest` must be green.

## Owner actions (no code can do these; listed in the runbook, Task 6)

1. Railway `AURA-AI`: set `FIREBASE_CREDENTIALS_JSON`. The service account JSON for the Firebase
   project behind the app's `google-services.json`. Without it the backend cannot verify any
   Firebase token.
2. Firebase console → Authentication → Sign-in method:
   - enable **Google**, **Apple**, **Email/Password**;
   - Settings → User account linking: **"Link accounts that use the same email"**.
3. Apple Developer:
   - turn on the Sign in with Apple capability for the iOS and macOS bundle IDs;
   - create a Services ID and key, and enter them in Firebase's Apple provider (needed for web and
     Android).
4. Run `scripts/import_users_to_firebase.py --apply` once, **before** shipping the client build
   from this plan. Until then, an existing Google user who signs in through the new client gets a
   new Firebase uid, which means an empty account.

## Review Focus

1. **An operator's address on the consumer route.** An operator whose email is in
   `SUPERADMIN_EMAILS` signs in through `/consumer/token`. Expected: role `User`, and still `User`
   after refresh and on every v3 request.
2. **An unverified email/password account.** It must not be able to create a team or accept an
   invite (403 with a "verify your email" reason). Personal use works.
3. **A Firebase token for an Apple user with a private relay email.** Sign-in works. The email
   claim is the relay address and never matches an operator list.
4. **The import is run twice, or a subject already exists in Firebase.** No duplicate users and no
   change to an existing Firebase user; the run reports what it skipped.
5. **The client after sign-out.** Signing out of Firebase, then signing in with a different
   method or account, clears the previous account's local data (`_forgetDataOfOtherAccount`) and
   never reuses the old Firebase ID token.

---

### Task 1: Consumer web sign-in route with a `User` ceiling

**Files:**
- Modify: `aura_backend/api/v2/auth.py` (new route after `/web/token`)
- Modify: `aura_backend/services/rbac.py` (`get_user_role`)
- Modify: `aura_backend/services/jwt_auth.py` (`create_access_token` claims;
  `verify_access_token` return)
- Test: `aura_backend/tests/unit/test_consumer_web_sign_in.py`

**Interfaces:**
- Produces:
  - `POST /api/v3/auth/consumer/token`. Bearer is a Google or Firebase ID token, verified by
    `verify_identity_token`; no attestation. It returns the same `TokenResponse` as `/token`.
  - It sets `user_info["channel"] = "consumer_web"` before `stamp_role` and `issue_token_pair`.
- Constant: `services/rbac.CONSUMER_WEB_CHANNEL = "consumer_web"`.
- `get_user_role` returns `"User"` whenever `user_info.get("channel") == CONSUMER_WEB_CHANNEL`.
- Access-token payload gains `"channel"` (omitted when absent), and `verify_access_token` returns
  it, so `aura_service._build_authenticator`'s `get_user_role(claims)` also caps.
- Rate limit: 10 requests per minute per client IP, using whatever limiter `api/v2/auth.py`'s
  siblings use. If none exists, use
  `aura_core.security.rate_limit.SlidingWindowRateLimiter.check_limit` over the in-process cache.

- [ ] **Step 1: Write the failing tests.**
  - `test_operator_email_capped_to_user`: stub `verify_identity_token` to return
    `{"uid":"op","email":"boss@example.com","email_verified":True,"sign_in_provider":"google.com"}`
    with `SUPERADMIN_EMAILS=boss@example.com`. The response's access token decodes to
    `role == "User"` and `channel == "consumer_web"`.
  - `test_cap_survives_refresh`: `POST /refresh` with that pair's refresh token returns a token
    whose role is still `User`.
  - `test_gateway_resolves_capped_role`: build the v3 app with `_build_authenticator` and present
    the consumer access token to `GET /api/v3/admin/service-keys`. Expected: 403.
  - `test_roles_json_uid_grant_also_capped`: an Access-page/`assigned_role` of `SuperAdmin` for
    that uid still yields `User`.
  - `test_mobile_token_route_unchanged`: `/token` for the same operator still yields `SuperAdmin`.
  - `test_consumer_route_rate_limited`: the 11th call within a minute from one IP gets 429.
- [ ] **Step 2:** Run `pytest tests/unit/test_consumer_web_sign_in.py -v`. Expected: FAIL (404 on
  the route).
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run the new tests plus
  `pytest tests/unit tests/security -k "auth or rbac or jwt or role or authenticator" -q`, and the
  legacy `TESTING=True python -m unittest test_jwt_auth test_role_assignment_integration`.
  Expected: PASS.
- [ ] **Step 5: Commit:** `feat(auth): consumer web sign-in capped at the User role`.

### Task 2: Verified email reaches the gateway; teams require it

**Files:**
- Modify: `aura_backend/services/jwt_auth.py`. The access-token payload gains
  `"email_verified": bool` from `user_info`; `verify_access_token` returns it.
- Modify: `aura_backend/aura_core/api/v3.py`. `Principal` gains `email_verified: bool = False`.
- Modify: `aura_backend/aura_service.py`. The authenticator sets
  `email_verified=claims.get("email_verified") is True`; mock auth sets `True`.
- Modify: `aura_backend/aura_core/api/workspaces.py`. `POST /workspaces` and
  `POST /workspaces/invites/accept` raise
  `AuthorizationError("verify your email address before creating or joining a team")` when
  `not caller.email_verified`.
- Test: `aura_backend/tests/integration/test_workspaces_api.py` (extend)

- [ ] **Step 1: Write the failing tests.**
  - `test_unverified_cannot_create_team`: 403.
  - `test_unverified_cannot_accept_invite`: 403.
  - `test_unverified_can_list_personal_workspace`: 200.

  The file's `_people` fixture gains an `email_verified` switch. Default `True`; the token text
  `unverified-*` yields `False`.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3: Implement.** Existing tests must stay green with verified defaults.
- [ ] **Step 4:** Run `pytest tests/integration/test_workspaces_api.py tests/integration/test_workspace_isolation.py tests/unit -k "principal or authenticator or workspace" -q`.
  Expected: PASS.
- [ ] **Step 5: Commit:** `feat(auth): verified email on the access token; teams require it`.

### Task 3: Subject-preserving import of existing users into Firebase

**Files:**
- Create: `aura_backend/scripts/import_users_to_firebase.py`
- Test: `aura_backend/tests/unit/test_import_users_to_firebase.py`

**Interfaces:**
- `plan_import(users: list[dict], existing: Callable[[str], bool]) -> ImportPlan`
  - Input rows: `{"auth_subject","email","display_name"}`.
  - `ImportPlan` holds `to_create: list[dict]` and
    `skipped: list[tuple[str, str]]` (subject, reason).
  - Reasons:
    - `"exists"`: a Firebase user with that uid already exists;
    - `"placeholder-email"`: an email ending `@unknown.local`, which gets created without an
      email;
    - `"duplicate-email"`: a second subject with the same email. It is created without an email
      and reported, never merged.
- `firebase_records(plan) -> list[firebase_admin.auth.ImportUserRecord]`. Each record has
  `uid = auth_subject` and `email` (unless dropped above), with `email_verified=True` and
  `provider_data=[UserProvider(uid=auth_subject, provider_id="google.com", email=email)]`. Every
  pre-existing subject came from Google sign-in; other subjects (service or mock) are filtered out
  by `is_reserved_principal` and a `^[A-Za-z0-9]{6,128}$` check.
- CLI:
  - `python scripts/import_users_to_firebase.py [--apply] [--database-url URL]`.
  - It reads `users` through asyncpg; the dry run is the default and prints the plan.
  - `--apply` calls `firebase_admin.auth.import_users` in batches of 1000 and prints each batch's
    `success_count`/`failure_count` and the error per index.
  - Credentials come from `FIREBASE_CREDENTIALS_JSON` or `GOOGLE_APPLICATION_CREDENTIALS`.

- [ ] **Step 1: Write the failing tests** (pure functions; `firebase_admin` is installed, so build
  records for real):
  - `test_existing_firebase_users_skipped`;
  - `test_rerun_is_a_no_op` (every subject exists);
  - `test_placeholder_and_duplicate_emails`;
  - `test_records_keep_subject_as_uid_with_google_provider`;
  - `test_reserved_and_malformed_subjects_filtered`.
- [ ] **Step 2:** Run `pytest tests/unit/test_import_users_to_firebase.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run it. Expected: PASS. Also run
  `python scripts/import_users_to_firebase.py --database-url <local migrated PG with 3 seeded users>`
  (dry run) and quote its output.
- [ ] **Step 5: Commit:** `feat(auth): subject-preserving Firebase import for existing users`.

### Task 4: Client — one Firebase identity layer

**Files:**
- Modify: `aura_client/pubspec.yaml`. Add `firebase_auth` and `sign_in_with_apple`, versions
  compatible with `firebase_core ^4.10.0` (resolve with `flutter pub add`).
- Create: `aura_client/lib/core/auth/identity_service.dart`
- Modify: `aura_client/lib/features/auth/presentation/auth_provider.dart`
- Modify: `aura_client/lib/core/auth/token_manager.dart`
- Test: `aura_client/test/identity_service_test.dart`, `aura_client/test/sign_in_session_test.dart`
  (extend)

**Interfaces:**
- `abstract class IdentityService` with these members:
  - `Future<IdentityUser> signInWithGoogle()`
  - `Future<IdentityUser> signInWithApple()`
  - `Future<IdentityUser> signInWithEmail(String email, String password)`
  - `Future<IdentityUser> registerWithEmail(String email, String password, {String? displayName})`
    (sends the verification email)
  - `Future<void> sendPasswordReset(String email)`
  - `Future<void> resendVerification()`
  - `Future<String?> idToken({bool forceRefresh = false})`
  - `Future<void> signOut()`
  - `IdentityUser? get current`
- `class IdentityUser { final String uid, email, displayName; final String? photoUrl; final bool emailVerified; final String provider; }`
- `class IdentityError implements Exception { final IdentityErrorKind kind; final String message; }`.
  The kinds are `cancelled`, `wrongCredentials`, `emailInUse`, `weakPassword`, `invalidEmail`,
  `network`, `accountExistsWithDifferentMethod` and `unavailable`. They map from
  `FirebaseAuthException.code`.
- `FirebaseIdentityService implements IdentityService`:
  - Google goes through `GoogleSignIn.instance.authenticate()`, then
    `GoogleAuthProvider.credential(idToken:)`, then `signInWithCredential`. On web it uses
    `signInWithPopup(GoogleAuthProvider())`.
  - Apple goes through `AppleAuthProvider` (`signInWithProvider` on iOS/macOS/Android,
    `signInWithPopup` on web).
- `identityServiceProvider` is a Riverpod `Provider<IdentityService>` that tests override.
- `AuthNotifier` changes:
  - `signInWithGoogle` uses `identityService`.
  - New methods: `signInWithApple()`, `signInWithEmail(email, password)`,
    `registerWithEmail(email, password, displayName)`, `sendPasswordReset(email)`.
  - All of them share one `_completeSignIn(IdentityUser)`. It calls
    `tokenManager.startSession(idToken)`, then `_forgetDataOfOtherAccount(user.uid)`, writes the
    same storage keys as today, and sets the state.
  - `getIdToken()` returns `identityService.idToken()`.
  - On Linux/Windows `supportsNativeGoogleSignIn()` stays false and the desktop flow is unchanged.
- `TokenManager._tryExchange` posts to `"$_authUrl/consumer/token"` without attestation headers
  when `kIsWeb`, and to `"$_authUrl/token"` with attestation otherwise. `_tryDebugOperatorExchange`
  is unchanged.

- [ ] **Step 1: Write the failing tests.** Use a `FakeIdentityService`, a fake HTTP client (the
  pattern in `sign_in_session_test.dart`) and a `ProviderContainer` override.
  - `signInWithEmail_starts_backend_session_with_firebase_token`: the exchange body/header
    carries the fake's token.
  - `apple_sign_in_uses_same_completion`.
  - `refused_exchange_signs_out_of_identity`.
  - `switching_account_forgets_previous_data`.
  - `identity_error_codes_map` (table test over `FirebaseAuthException` codes).
  - `web_exchange_uses_consumer_route_without_attestation`: exercise the URL builder through a
    seam `TokenManager.exchangePathFor({required bool isWeb})`.
- [ ] **Step 2:** Run `flutter test test/identity_service_test.dart test/sign_in_session_test.dart`.
  Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run `flutter analyze` and the full `flutter test`. Expected: analyze clean, all
  pass (quote the counts).
- [ ] **Step 5: Commit:** `feat(client): Firebase identity layer for Google, Apple and email sign-in`.

### Task 5: Client — sign-in screen

**Files:**
- Modify: `aura_client/lib/features/auth/presentation/login_page.dart`
- Create: `aura_client/lib/features/auth/presentation/email_sign_in_form.dart`
- Test: `aura_client/test/login_page_test.dart`

**Behavior:**
- **Buttons**, built with `core/design_system` components:
  - **Continue with Google**;
  - **Continue with Apple**, shown on iOS, macOS and web (Android users can use Google or email;
    App Store rule 4.8 only binds iOS);
  - **Continue with email**.
- **Desktop.** Linux and Windows keep today's desktop button only.
- **Email form**, with three modes:
  - **Sign in**: email and password.
  - **Create account**: name, email, password and confirmation; at least 8 characters, matching.
  - **Forgot password**: email only, then the message "If an account exists, we've sent a reset
    link".
- **Errors.** Each `IdentityErrorKind` maps to a sentence. `accountExistsWithDifferentMethod`
  says "This email already uses another sign-in method — use it, then add this one from your
  profile". `cancelled` shows nothing.
- **After registration**, a banner reads "Check your inbox to verify your email. You'll need it
  to create or join a team", with a "Resend" action.
- **Text** goes through the existing localisation/strings pattern the page already uses.
  RTL-safe.

- [ ] **Step 1: Write the failing widget tests** (`identityServiceProvider` overridden by the fake):
  - `shows_apple_on_ios_and_web_not_android`: use `debugDefaultTargetPlatformOverride` and the
    page's `isWeb` seam.
  - `email_register_validates_and_calls_service`.
  - `forgot_password_never_reveals_existence`.
  - `wrong_password_shows_message`.
  - `cancelled_shows_nothing`.
  - `registration_shows_verify_banner`.
- [ ] **Step 2:** Run `flutter test test/login_page_test.dart`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run `flutter analyze` and the full `flutter test`. Expected: PASS (quote the
  counts). Re-run `test/ux/rtl_layout_test.dart` and
  `test/ux/navigation_and_destructive_actions_test.dart` explicitly.
- [ ] **Step 5: Commit:** `feat(client): sign-in screen with Google, Apple and email`.

### Task 6: Runbook, docs and status

**Files:**
- Create: `docs/deployment/customer-sign-in.md`. It covers the owner actions above, in order:
  how to run the import dry run and then `--apply` against Railway Postgres
  (`railway connect`/`railway run`), how to verify it (sign in with Google through the new
  client; `/health` and `GET /api/v3/workspaces` return the old workspace), and how to roll back
  (the import only adds Firebase users; delete them with
  `firebase_admin.auth.delete_users(uids)`).
- Modify: `docs/architecture/034-workspaces-and-tenancy.md` (a short "Identity" note:
  consumer-web cap, verified email for teams), `docs/status/BACKEND_STATUS.md`,
  `docs/status/MOBILE_APP_STATUS.md` (rows: BUILT, tested locally, not deployed/proven).
- Modify: `aura_backend/scripts/api_reachability.py` TRIAGE, if the reachability test flags
  `/auth/consumer/token`. The client calls it, so it may resolve as called.

- [ ] **Step 1:** Write the docs.
- [ ] **Step 2:** Run `pytest tests/architecture -q` (reachability matrix) and
  `python scripts/api_reachability.py`, if the matrix changed. Expected: PASS.
- [ ] **Step 3: Commit:** `docs: customer sign-in runbook; status rows`.
