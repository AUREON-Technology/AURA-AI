# ADR-060: push notification when an agent run waits for approval

Status: Proposed (server side built and tested with a recording sender; **no real push has been
sent**, and the app cannot yet obtain a token)

## Problem
An Engineer run that holds a call for approval (ADR-006/040) waits silently until the person opens
the app. On a phone that means a run can sit for hours.

## Decision
- A device registers its push token: `POST /api/v3/push/devices`, `DELETE /api/v3/push/devices/{id}`
  (owner-scoped, service keys refused). Table `aura_push_devices` (migration 0035). A token identifies
  one install: registering it under another account moves it, so a phone that changed hands never
  notifies the old account. At most 10 devices per user, newest kept.
- `PushNotifier` (`aura_core/push/`) sends one nudge per user per 20 s when `_hold_for_confirmation`
  holds a call (fire-and-forget; a failure never reaches the turn). Dead tokens (`UNREGISTERED`) are
  forgotten; other errors keep the token.
- **The message is generic** ("A step is waiting for your approval.") with ids only in the data
  payload. Tool arguments, paths and commands are never sent. `AURA_PUSH_INCLUDE_DETAIL=true` adds the
  one-line headline, off by default because it shows on a lock screen and passes through Google.
- Sending needs `AURA_PUSH_ENABLED=true` **and** `AURA_PUSH_FCM_CREDENTIALS_FILE` (a path to a Firebase
  service-account file; the key is never a setting). `firebase_admin` is imported lazily in
  `adapters/fcm_push.py`. Without both, devices still register and `delivering: false` says so.

## Not built (stated, not hidden)
- **The app has no token source.** `lib/core/notifications/push_registration.dart` is the tested
  contract; obtaining an FCM token needs the `firebase_messaging` plugin plus a Firebase project's
  platform files and an iOS APNs key, which cannot be set up or checked from a headless build host.
- No real FCM delivery has been run: the sender is covered by a recording sender, and
  `FcmPushSender`'s error classification is untested against Google.
- Only "approval needed" is pushed; run-finished and run-failed are not.
- Tapping a notification does not yet deep-link to the held call.
