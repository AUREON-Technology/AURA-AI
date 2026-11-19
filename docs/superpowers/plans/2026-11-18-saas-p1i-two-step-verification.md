# SaaS P1-I: Optional Two-Step Verification (TOTP), Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A person can turn on an authenticator-app code (TOTP) as a second sign-in step, and every sign-in method then asks for it. This is the one phase-1 spec feature no earlier plan built.

**Architecture:** This is all client-side, on Firebase multi-factor (`firebase_auth` 6.7.1: `TotpMultiFactorGenerator`, `MultiFactorResolver`).
- **`IdentityService`** gains enrollment methods.
- **A second-factor sign-in raises `SecondFactorRequired`**, which carries a challenge that resolves with a code. `_guard` maps Firebase's `FirebaseAuthMultiFactorException` to it, so every sign-in method (Google, Apple, email) gets the second step for free.
- **The login page catches it** and shows a code dialog. The resolved `IdentityUser` then goes through the normal backend-session exchange (`AuthNotifier.completeSecondFactor`).
- **The backend changes nothing.** Firebase will not issue an ID token without the second factor, and the backend already only accepts Firebase ID tokens.

**Tech Stack:** Flutter 3.44.6, `firebase_auth` 6.7.1, Riverpod 3.

**Spec:** `docs/superpowers/specs/2026-11-16-saas-phase1-design.md` §5.1, "Owner-only two-factor: optional TOTP, through Firebase multi-factor, for workspace Owners and Admins. It is not required at launch."

## As built (after review)

- **The server enforces it (review I#1).** The plan said "the backend changes nothing". That was
  wrong: the backend also accepts raw Google ID tokens, which skip Firebase and so would skip the
  second step. `verify_identity_token` now refuses a raw Google token for an account with a second
  factor enrolled, found by subject and then by email, and refuses it if Firebase cannot answer.
  Consequence: the desktop apps' older Google button (raw Google token) cannot sign in to such an
  account. After merging #263, their "Continue in your browser" sign-in can: the web hand-off
  page asks for the code (`desktop_sign_in_page.dart`, tested in
  `test/auth/desktop_browser_sign_in_test.dart`).
- **Back is disabled while a code is being checked (I#4).** If no authenticator app opens the
  link, a snackbar says so. The enrolled factor is shown in the app's own words.
- **The login page's dialog wiring is tested (I#3).**

## Global Constraints

- **Optional, and offered to everyone signed in.** Every person owns their personal workspace, so "Owners" is everyone. Nothing enforces it, and no route refuses a session without it (per the spec).
- **TOTP only.** SMS second factors are out of phase 1 (spec §6.2). A sign-in whose only enrolled factor is not TOTP fails with `unavailable`, never with a silent bypass.
- **Firebase preconditions, worded for the user:**
  - Enrollment needs a verified email. If the email is not verified, the screen says so and offers nothing.
  - Enrollment and removal need a recent sign-in. `requires-recent-login` becomes `IdentityErrorKind.reauthRequired`, and the screen says to sign in again.
- **The secret is shown once and never stored or logged** by the app. It appears on the enroll screen only, as an `otpauth://` link and as text for manual entry.
- **Owner action.** TOTP must be enabled on the Firebase project (Identity Platform). Until then, enrollment fails with `unavailable`; the doc says how to enable it.
- **Strings.** Every string goes in `app_en.arb` plus an Egyptian Arabic `app_ar.arb` translation. `flutter analyze` must be clean and `flutter test` green.

## Review Focus

1. **A wrong code at sign-in keeps the dialog open** with an error, and the next code uses the same challenge. Test: the fake challenge rejects "000000" and accepts "123456", and the second attempt signs in.
2. **Cancelling the code dialog leaves the person signed out,** with no backend session started. Test: cancel; the auth state is `data(null)` and `startSession` was never called.
3. **A sign-in that needs a non-TOTP factor** is refused with a clear error, not an endless code dialog. Test: `identityErrorKindFor` / the challenge with `totp: false` yields `IdentityErrorKind.unavailable`.
4. **Enrolling while the email is unverified:** the screen shows the reason and no "Set up" button. Test.
5. **Turning it off** asks for confirmation first, then removes the factor. Test: confirm; the fake's `removeSecondFactor` is called with the factor's uid, and the list is empty.

---

### Task 1: The identity layer and the sign-in challenge

**Files:**
- Modify: `aura_client/lib/core/auth/identity_service.dart`
- Modify: the test fakes that implement `IdentityService`: `test/auth/identity_flow_test.dart`, `test/auth/verify_email_banner_test.dart`, `test/auth/linked_accounts_and_legal_test.dart`.

**Interfaces:**

```dart
enum IdentityErrorKind { ..., reauthRequired, wrongCode }

class SecondFactor { final String uid; final String displayName; final DateTime? enrolledAt; }

class TotpEnrollment {            // the secret, shown once
  final String secretKey;         // for manual entry
  final String otpauthUrl;        // generateQrCodeUrl(accountName: email, issuer: 'AURA')
  final Object raw;               // the plugin's TotpSecret
}

/// Thrown by every sign-in method when the account has a second factor.
class SecondFactorRequired implements Exception {
  final bool totp;                // false: only a factor phase 1 cannot answer
  final Future<IdentityUser> Function(String code) resolve;  // throws IdentityError(wrongCode)
}

// IdentityService additions
Future<List<SecondFactor>> secondFactors();
Future<TotpEnrollment> startTotpEnrollment();
Future<void> finishTotpEnrollment(TotpEnrollment enrollment, String code);
Future<void> removeSecondFactor(String uid);
```

- **The error mapping:** `invalid-verification-code` → `wrongCode`, `requires-recent-login` → `reauthRequired`, and `unverified-email` → `unavailable`, which the screen pre-empts (Review Focus 4).
- **`_guard`** catches `FirebaseAuthMultiFactorException`:
  - If the resolver's hints contain a `TotpMultiFactorInfo`, it throws `SecondFactorRequired(totp: true)`. Its `resolve` calls `resolver.resolveSignIn(TotpMultiFactorGenerator.getAssertionForSignIn(hint.uid, code))` and maps errors through `identityErrorKindFor`.
  - Otherwise it throws `SecondFactorRequired(totp: false)`.

- [ ] **Write the unit tests**, `test/auth/second_factor_test.dart`:
  - `identityErrorKindFor('invalid-verification-code') == wrongCode`;
  - `identityErrorKindFor('requires-recent-login') == reauthRequired`.
- [ ] **Implement.** The fakes get trivial overrides.
- [ ] **`flutter analyze`** is clean.

### Task 2: Sign-in with a second step

**Files:**
- Modify:
  - `aura_client/lib/features/auth/presentation/auth_provider.dart`: `_signInWith` lets `SecondFactorRequired` through as `AsyncValue.error`, and adds `completeSecondFactor(IdentityUser user)`, which runs `_signInWith(() async => user)`;
  - `login_page.dart`: `ref.listen` sees `SecondFactorRequired` and shows `showSecondFactorDialog`;
  - the arb files.
- Create: `aura_client/lib/features/auth/presentation/second_factor_dialog.dart`.
  - `Future<IdentityUser?> showSecondFactorDialog(BuildContext, SecondFactorRequired)`.
  - It has a 6-digit field, Verify and Cancel. A wrong code shows an inline error and keeps the dialog open. A `totp == false` challenge shows `twoStepUnsupported` and only Close.
- Test: `aura_client/test/auth/second_factor_test.dart`, for Review Focus 1, 2 and 3, through `AuthNotifier` and the dialog with a fake identity.

- [ ] Write the tests; they fail.
- [ ] Implement.
- [ ] Run the tests and `flutter analyze`.

### Task 3: The Two-step verification screen

**Files:**
- Create: `aura_client/lib/features/auth/presentation/two_step_page.dart`, at route `/two-step`, linked from the Sign-in methods page.
  - **Unverified email:** shows the reason only.
  - **No factor:** a "Set up" button runs `startTotpEnrollment` and shows the secret:
    - a "copy key" action and an "open in authenticator" link that launches `otpauthUrl` with `url_launcher`;
    - a code field, then Verify (`finishTotpEnrollment`).
  - **Enrolled:** lists the factor with a "Turn off" button (`confirm()`, then `removeSecondFactor`).
  - **`reauthRequired`:** says to sign out and in again.
- Modify:
  - `router.dart`: the route, also in `_desktopScreens`;
  - `linked_accounts_page.dart`: an entry tile;
  - the arb files.
- Test: `aura_client/test/auth/two_step_page_test.dart`, for Review Focus 4 and 5 and a successful enrollment.

- [ ] Write the tests; they fail.
- [ ] Implement.
- [ ] Run the full `flutter analyze` and `flutter test`.
- [ ] Commit.

### Task 4: Docs

- [ ] `docs/deployment/customer-sign-in.md`: a new section, "Two-step verification (TOTP)". It covers:
  - enabling TOTP on the project. This needs Identity Platform; use Console → Authentication → Sign-in method → Advanced → Multi-factor authentication, or the `projects.updateConfig` REST call with `mfa.providerConfigs[].totpProviderConfig.adjacentIntervals`;
  - what users see;
  - recovery: an operator can remove a lost factor in the Firebase console.
- [ ] Add a Two-step row to `docs/status/MOBILE_APP_STATUS.md`.
- [ ] Commit, push, and open a draft PR.
