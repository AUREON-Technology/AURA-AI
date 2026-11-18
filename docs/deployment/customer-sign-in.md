# Customer sign-in: setup and cutover runbook (SaaS P1-B)

Plan B (`docs/superpowers/plans/2026-11-17-saas-p1b-sign-in-providers.md`) moves every sign-in to
Firebase Authentication:
- **Google, Apple and email/password** all sign in through Firebase.
- **The web** signs in through `POST /api/v3/auth/consumer/token`. No device attestation, and the
  session is capped at the `User` platform role.
- **Teams** require a verified email.

The code is built and tested; **none of it works for customers until the steps below are done.**
They are account settings only the owner can change.

Do them **in this order**. Step 5 must happen before the new client build reaches anyone.

## 1. Let the backend verify Firebase tokens

The backend verifies Firebase ID tokens with the Firebase Admin SDK (`services/security.py`). With
no credentials it logs "Firebase Auth verification is disabled" and refuses every Firebase sign-in.

1. Firebase console: Project settings → Service accounts → Generate new private key. Use the
   project that `aura_client/android/app/google-services.json` belongs to.
2. Railway → project `intuitive-adaptation` → service `AURA-AI` → Variables. Add
   `FIREBASE_CREDENTIALS_JSON` with the whole JSON file as its value. Railway redeploys.
3. Check: the deploy log no longer says "Firebase Auth verification is disabled".

## 2. Turn the sign-in methods on, and keep one account per person

Firebase console → Authentication → Sign-in method:

- Enable **Google** (already used), **Apple** and **Email/Password**. Leave "Email link" and
  **Anonymous** off: an anonymous Firebase token has no email, and `/consumer/token` would accept
  it.
- Settings → User account linking: choose **"Link accounts that use the same email"**. That keeps
  one Firebase account per address. It does **not** silently merge a *different* method into an
  existing account: an Apple or email/password sign-in for an address that already signs in with
  Google fails with "This email already uses another sign-in method". The person signs in with the
  original method. A "link another sign-in method" screen is planned (Plan F). No data is split
  either way.
- Settings → Authorized domains: add the web app's domain(s), for example `aureon-ai.tech` and
  `api.aureon-ai.tech`.

## 3. Apple

1. Apple Developer → Identifiers. For the iOS and macOS bundle IDs, turn on **Sign in with
   Apple**. Rebuild the apps with the capability: Xcode → Runner → Signing & Capabilities → +
   Sign in with Apple.
2. For the web and Android, which go through Firebase's Apple provider:
   - create a **Services ID** and a **Sign in with Apple key**;
   - enter the Services ID, Team ID, Key ID and the key in Firebase → Apple provider;
   - add Firebase's handler URL (shown there) as a return URL on the Services ID.

## 4. Client Firebase configuration per platform

- **Android:** `android/app/google-services.json` exists already.
- **iOS / macOS:** add `GoogleService-Info.plist` to `ios/Runner/` and `macos/Runner/`. It is not
  in the repository today.
- **Web:** there is no `firebase_options.dart`. Run `flutterfire configure` for the web app, or
  pass the web config to `Firebase.initializeApp`. Until then, web sign-in reports "This sign-in
  method isn't available right now".

## 5. Move existing users into Firebase under their current ids (before shipping the client)

Every existing person's data is stored under their Google `sub`. A Firebase sign-in would give
them a *new* uid, and with it an empty account, unless they already exist in Firebase with that
uid. The import script creates them that way. It never rewrites anything in AURA's database.

```bash
cd aura_backend
# Dry run, no Firebase credentials needed: shows who would be created and what is skipped.
railway run --service AURA-AI -- python scripts/import_users_to_firebase.py --no-firebase-lookup
# Dry run against Firebase: also skips anyone Firebase already has.
FIREBASE_CREDENTIALS_JSON="$(cat service-account.json)" \
  railway run --service AURA-AI -- python scripts/import_users_to_firebase.py
# The import itself.
FIREBASE_CREDENTIALS_JSON="$(cat service-account.json)" \
  railway run --service AURA-AI -- python scripts/import_users_to_firebase.py --apply
```

(`railway run` provides `AURA_DATABASE_URL`. If the database is only reachable on Railway's
private network, run the commands through `railway ssh` instead.)

**After the import**, people still on an old app version keep creating Google-subject accounts.
The backend covers them: a Firebase token whose Google identity is an existing subject signs in as
that subject, as long as the Firebase uid has no account of its own (`services/security.canonical_subject`).
Re-running the import before each release is still cheap and keeps Firebase tidy.

**Notes the script prints:**
- `exists`: already in Firebase, left untouched.
- `not-a-person`: service, project or workspace principals and malformed subjects; not imported.
- `placeholder-email`: an `@unknown.local` stand-in, imported without an email.
- `duplicate-email`: a second subject with the same address, imported without an email. It is
  **never merged**, because merging would join two people's data. Resolve these by hand.
- `email-taken`: Firebase already has *another* account with that address (someone registered it,
  or signed in through the new client first). The person is skipped, because `import_users` does
  not check email uniqueness. Resolve by hand.

**Verify:**
1. Sign in with Google through the new client using an existing account.
2. `GET /api/v3/workspaces` must return that person's existing personal workspace, and their
   knowledge and memory must still be there.
3. A brand-new email/password account gets its own new id. Signing in with Apple or a password
   for an address that already uses Google is refused with "This email already uses another
   sign-in method", and no second account is created.

**Roll back:** the import only adds Firebase users. Delete them with
`firebase_admin.auth.delete_users([...uids])`. Nothing in AURA changes.

## What the backend now enforces

- **The customer web route** is `/api/v3/auth/consumer/token`:
  - it accepts a verified Google or Firebase ID token with no attestation;
  - it allows 10 sign-ins per IP per minute per worker;
  - its sessions carry `channel=consumer_web` and resolve to platform role `User` at issue, on
    refresh and on every request, whatever `SUPERADMIN_EMAILS`, `roles.json` or the Access page
    say. Operators keep using `/api/v3/auth/web/token` (the console).
- **Email-list role grants** still need a verified Google sign-in (SEC-08). An Apple or password
  sign-in never matches `SUPERADMIN_EMAILS`, `ADMIN_EMAILS` or `DEVELOPER_EMAILS`.
- **Teams:** `POST /api/v3/workspaces` and invite acceptance return 403 with "verify your email
  address…" until the access token says `email_verified`. A refresh does **not** pick up a later
  verification, because it re-issues the identity it was signed in with. The app's "I've
  verified" action on the profile page reloads the Firebase user and starts a new session with a
  fresh token.
- **Rate limit:** the consumer sign-in limit keys on the address the trusted proxy appended to
  `X-Forwarded-For` (`AURA_TRUSTED_PROXY_COUNT`), not the caller-written leftmost entry.


## Customer screens in the app (SaaS P1-F2)

Build flags for the customer builds:

| `--dart-define` | Value |
|---|---|
| `APP_BASE_URL` | where the web app is served, for example `https://app.aureon-ai.tech`. Invite links point at `{APP_BASE_URL}/invite?token=…`, and the backend's `AURA_APP_BASE_URL` must be the same. |
| `TERMS_URL`, `PRIVACY_URL`, `REFUND_URL` | the owner's legal pages, `https://` only. A page that is not set is not linked. |
| `DIRECT_DISTRIBUTION` | `true` only for a macOS build distributed outside the Mac App Store. It allows Fawry checkout there. Never set it for a Mac App Store build: Apple rejects outside payment. |

The web build must serve `index.html` for `/invite`, so an invite link opened in a browser reaches
the app's router. That is the normal single-page-app fallback on any static host.

Sign-in methods (You → Sign-in methods) adds Google, Apple or a password to an existing account.
It needs those providers enabled in Firebase, as in the steps above.

## Two-step verification (TOTP, SaaS P1-I)

A person can add a code from an authenticator app (Google Authenticator, Microsoft Authenticator,
1Password and others) as a second sign-in step: **You → Sign-in methods → Two-step verification**.
- **It is optional and enforced nowhere** (spec §5.1). Every sign-in method then asks for the code:
  Google, Apple and email/password.
- **The backend needs nothing new.** Firebase issues no ID token until the second step is done, and
  the backend accepts only Firebase ID tokens.

**Turn it on for the project (owner, once).** TOTP multi-factor needs **Firebase Authentication
with Identity Platform**, the upgraded tier. Its pricing is per monthly active user beyond the free
tier, so check the Firebase pricing page.
1. Firebase Console → Authentication → Settings → upgrade to Identity Platform, if it is not
   already.
2. Enable TOTP. The Console path is Authentication → Sign-in method → Advanced →
   **Multi-factor authentication** → enable **TOTP**. The equivalent REST call is
   `projects.updateConfig` with
   `mfa: {providerConfigs: [{state: "ENABLED", totpProviderConfig: {adjacentIntervals: 5}}]}`.

Until this is done, "Set up" fails with the generic "sign-in is unavailable" message.

**What people see:**
- Setting up shows a key once, with "Copy key" and "Open in authenticator app" (an `otpauth://`
  link). The app neither stores nor logs the key.
- A code confirms the set-up. Firebase requires a verified email first, and the screen says so.
- Firebase also requires a recent sign-in to add or remove the step. Otherwise the screen says to
  sign out and in again.
- At sign-in, a code dialog follows the first step. A wrong code can be retried, and Cancel leaves
  the person signed out.
- An account whose only second step is SMS is refused with a clear message. SMS is out of phase 1.

**Lost authenticator (recovery).** There are no backup codes in phase 1. An operator removes the
factor in Firebase Console → Authentication → Users → the user → Multi-factor authentication,
after confirming who is asking by the account's email. Record each removal: it is the weakest
point of this feature.

