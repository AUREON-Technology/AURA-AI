# Lens: UI/UX, accessibility, client-side user flows

Baseline: commit 699f27b, branch ccr-4760e151-1si7gz. Read-only static inspection. No Flutter SDK, so
nothing here was run. Any claim about what renders on screen is **code-inferred** (the colour values,
widget trees and control flow were read; the pixels were not seen). Every path below is relative to
`/home/user/AURA-AI/` unless it is absolute.

Prior UX audit in the repo: `docs/ux/UX_BASELINE_AUDIT.md` (F-01..F-12, 2026-09-28). It scoped itself
to the design system and the desktop shell, and says on its own lines 467-470 that mobile `lib/features/**`
and `aura_admin/` were not audited. Most findings below fall in that gap. Where a finding overlaps an
F-item or a backlog item (`docs/roadmaps/AURA_CRITICAL_BACKLOG.md`, CB-xx), the overlap is stated.

---

## Summary table

| ID | Title | Type | Sev | Evidence |
|---|---|---|---|---|
| UX-01 | A chat stream cannot be cancelled and has no idle timeout, so the composer can lock for good | defect | High | CODE-VERIFIED |
| UX-02 | Light theme (the default via `ThemeMode.system`) renders Life Hub task titles and the health card as near-white on white | defect | High | CODE-VERIFIED (colours) / visual inferred |
| UX-03 | Sign-out keeps the previous user's local data, outbox and sync cursor, and never revokes the refresh token | defect | High | CODE-VERIFIED path; cross-account effect PARTIALLY VERIFIED |
| UX-04 | Approving a tool call re-sends the *latest* user message, not the approved turn's; a mid-stream approval is silently lost | defect | High | CODE-VERIFIED |
| UX-05 | Live voice and chat share one global pending-confirmation list: voice shows chat's calls and clears them | defect | Medium | CODE-VERIFIED |
| UX-06 | An expired backend session ends in a "Sign in again" screen with no sign-in action | defect | Medium | CODE-VERIFIED |
| UX-07 | Saved memories put a permanent poison row in the sync outbox (non-UUID id); conflicts are dropped silently | defect | Medium | CODE-VERIFIED |
| UX-08 | Memory page hides failed deletes and offline fallback; error state is never rendered | defect | Medium | CODE-VERIFIED |
| UX-09 | With the Desktop IDE switched on, "Today" can't be reached, and there's no "open/leave IDE" command as ADR-015 promises | defect | Medium | CODE-VERIFIED |
| UX-10 | aura_admin: refresh is not single-flight, so multi-fetch pages send the admin to /login after 15 minutes | defect | Medium | CODE-VERIFIED |
| UX-11 | Settings sub-pages are opened with `go`: no back arrow, and Android back exits the app | defect | Medium | CODE-VERIFIED (nav) / device behaviour inferred |
| UX-12 | One-tap destructive actions (delete conversation, delete task, sign-out in page headers) with no confirmation or undo | defect | Medium | CODE-VERIFIED |
| UX-13 | 21 of 64 IconButtons have no tooltip or label, including chat Send | defect (a11y) | Medium | CODE-VERIFIED count / screen-reader output inferred |
| UX-14 | l10n is a stub: 34 English keys, about 198 hardcoded `Text('…')`, no RTL-aware layout | opportunity | Medium | CODE-VERIFIED |
| UX-15 | Raw exception strings shown to users although `AuraFailure` exists | opportunity | Low-Med | CODE-VERIFIED |
| UX-16 | A failure mid-stream overwrites the partial answer with the error bubble | defect | Low | CODE-VERIFIED |
| UX-17 | Routing edges: `browse` route casts a null `extra`; `/ide` is blank off Windows; conversation list has no ordering | defect | Low | CODE-VERIFIED |
| UX-18 | Backend endpoints with no client caller that the UX needs (token revoke, device revoke, run history) | opportunity | Medium | CODE-VERIFIED |

---

## UX-01 — A chat stream cannot be cancelled and has no idle timeout, so the composer can lock for good
- **Type / Severity / Evidence:** defect · High · CODE-VERIFIED
- **Files:lines:**
  - `aura_client/lib/core/ai/online_agents/llm_timeouts.dart` (only `kLlmConnectTimeout = 180s`, which covers the time to response headers)
  - `aura_client/lib/core/ai/online_agents/sse_agent_client.dart:162-183`: `.timeout()` wraps only `client.send`. `await for (… response.stream …)` has no timeout.
  - `aura_client/lib/features/chat/data/chat_repository.dart:151-154` (a send is ignored while any stream is in flight) and `:195-220` (`streamingMessageIdProvider` is cleared only in `finally`)
  - `aura_client/lib/features/chat/presentation/chat_view_model.dart:113-131` (the `canSend` lock is global, not per session)
  - `aura_client/lib/features/chat/presentation/widgets/chat_composer.dart:28-29, 70, 81, 93` (text field, mic and send are all disabled while locked)
  - `grep -i "cancel|stop|abort"` over `lib/features/chat` and `lib/core/ai/online_agents` finds no cancel or stop control for a chat stream.
- **Trigger:** the SSE body stalls after headers (mobile network handover, half-open TCP, a proxy buffering), or the user just wants to stop a slow answer. CLAUDE.md puts production at 5–10 tok/s with planning calls of up to 180 s.
- **Actual:** `await for` waits forever. `streamingMessageIdProvider` stays set, and the composer stays disabled in **every** conversation (the lock is global, and `startNewSession` does not clear it) until the app process is killed. There is no Stop button at any point.
- **Expected:** a Stop control while streaming, plus an inter-chunk idle timeout that ends the turn into the existing retry bubble.
- **Impact:** chat, the app's core screen, can become unusable until restart, and users can't abandon a long or wrong answer.
- **Root cause:** the timeout covers only time-to-headers; the stream subscription is not held anywhere it could be cancelled.
- **Minimal fix:** in `SseAgentClient`, apply `response.stream.timeout(idle, onTimeout: …)` (for example 60–90 s, above the server keepalive interval if one exists). Keep the `StreamSubscription`/`http.Client` in `ChatRepository` and expose `cancelStreaming()`, which closes the client and leaves the partial text marked "stopped". Show a Stop `IconButton` in the composer while `isStreaming`.
- **Regression test:** a fake `http.Client` that sends headers and one token and then never closes. Assert that `streamingMessageIdProvider` becomes null within the idle timeout (use fake_async), and that `cancelStreaming()` unlocks the composer at once.
- **Confidence:** High.

## UX-02 — Light theme (the default) renders Life Hub task titles and the health card as near-white on white
- **Type / Severity / Evidence:** defect · High · CODE-VERIFIED colours, visual result inferred
- **Files:lines:**
  - `aura_client/lib/core/theme/theme_mode_controller.dart:12`: the default is `ThemeMode.system`. `lib/main.dart:71-73` sets `theme: AuraThemeV2.light`.
  - `aura_client/lib/core/design_system/aura_theme_v2.dart:46-47`: `cardTheme.color = palette.surface`. `aura_palette.dart:98-99`: light `background 0xFFF8FAFC`, `surface 0xFFFFFFFF`.
  - `aura_client/lib/core/theme/theme.dart:11`: legacy `AuraTheme.textPrimary = 0xFFF8FAFC` (a dark-theme constant).
  - `aura_client/lib/features/tasks/presentation/widgets/task_item_card.dart:34` (`Card(` uses the theme surface, white) and `:60` (title colour `AuraTheme.textPrimary`) → 0xF8FAFC on 0xFFFFFF, roughly 1.05:1.
  - `aura_client/lib/features/tasks/presentation/widgets/empty_state_tasks.dart:22` ("All caught up!" in `AuraTheme.textPrimary` on the light background)
  - `aura_client/lib/features/health/presentation/widgets/health_daily_dashboard.dart:26, 42, 54, 110` (`Colors.white` text inside a theme `Card`)
  - Rendered by Life Hub with the palette background: `aura_client/lib/features/life_hub/presentation/life_hub_page.dart:35, 206-210, 296`.
- **Trigger:** any device whose OS is in light mode. That is the default, and no user action is needed.
- **Actual:** task titles, the empty-state headline and the health card headings are effectively invisible in the main Life Hub tab.
- **Expected:** text uses `AuraPalette.of(context).textPrimary`, which is AA-checked in `design_system_contrast_test.dart`.
- **Impact:** a primary tab is illegible for a large share of users, and the AA contrast gate cannot see it.
- **Root cause:** feature widgets still use the dark-only legacy `AuraTheme` constants and `Colors.white`. This is a concrete, worse instance of **F-04** in `docs/ux/UX_BASELINE_AUDIT.md:159-189`. F-04 is still ◐ open; `grep "Color(0x"` outside the design system now finds 87, against 99 in F-04. F-04 framed the risk as a dark-mode problem, but the visible failure is in light mode.
- **Minimal fix:** move `task_item_card.dart`, `empty_state_tasks.dart` and `health_daily_dashboard.dart` to `AuraPalette.of(context)` first, because they render inside Life Hub. Then add a ratchet test that fails on new `AuraTheme.textPrimary` or `Colors.white` text under `lib/features/`.
- **Regression test:** a widget test that pumps `TaskItemCard` under `AuraThemeV2.light`, finds the title `Text`, and asserts `contrastRatio(style.color, palette.surface) >= 4.5` using `contrast.dart`.
- **Confidence:** High for the colour arithmetic; how it looks on a real device is inferred.

## UX-03 — Sign-out keeps the previous user's local data, outbox and sync cursor, and never revokes the refresh token
- **Type / Severity / Evidence:** defect · High · CODE-VERIFIED path; cross-account effect PARTIALLY VERIFIED (needs two accounts on one device to observe)
- **Files:lines:**
  - `aura_client/lib/features/auth/presentation/auth_provider.dart:283-299`: `signOut()` clears secure storage and `tokenManager.clear()` and nothing else. It never touches Drift and never makes a network call.
  - `aura_client/lib/core/auth/token_manager.dart:305-309`: `clear()` is local only.
  - `aura_backend/api/v2/auth.py:186-189`: `POST /api/v3/auth/revoke` exists. `grep -rn revoke aura_client/lib` finds no caller (only hostd root revoke and GitHub disconnect).
  - Local tables have no user column, and the outbox and cursor are global: `aura_client/lib/core/database/database.dart:228-243`, and `sync_client.dart:96-118` (push sends every outbox row) and `:154-185` (pull uses the stored cursor).
  - `aura_backend/api/v2/sync.py:175-190`: the ownership check covers only rows that **already exist**. A missing row is created under the caller (`model(id=row_id, user_id=user.id)`).
- **Trigger:** user A signs out, and user B signs in on the same device (a shared or family tablet, or a tester switching accounts).
- **Actual:**
  1. B sees A's local tasks, events, finance, health, chats and memories.
  2. A's un-pushed offline creations sit in the outbox and are pushed under B's JWT, so they become B's server rows.
  3. B's first pull uses A's cursor, so B's older server data is skipped.
  4. A's refresh token stays valid server-side for its full lifetime.
- **Expected:** sign-out (or an account change) wipes the user-scoped Drift tables, the outbox and the sync cursor, and calls `/auth/revoke` best-effort.
- **Impact:** a privacy leak between accounts, data attributed to the wrong account, a session left live server-side, and a personal-OS app that looks empty or wrong for user B.
- **Root cause:** the session lifecycle is modelled only in secure storage. The local DB is treated as device-scoped.
- **Falsification:** no "wipe on sign-out" exists. The only bulk delete, `database.dart:498-509`, runs on decryption failure. The backend does reject updates to rows A already synced ("not owned by this user"), so the leak is limited to never-synced rows plus everything visible locally. Backlog CB-03's impact text mentions "cross-user session overwrite" on the backend; this client-side path is not listed there.
- **Minimal fix:** `signOut()` should (a) POST `/api/v3/auth/revoke` with the refresh token, best-effort, before `clear()`, and (b) call a new `AppDatabase.wipeUserData()` that deletes the domain tables, `outboxEntries` and the cursor row. Alternatively, store the last user id and wipe when a different id signs in, which keeps offline edits for a returning user.
- **Regression test:** an in-memory Drift DB. Insert a task and an outbox row, call `signOut()`, and assert both tables and the cursor are empty. Use a fake HTTP client to assert `/auth/revoke` was called.
- **Confidence:** High for the code path; the multi-account effect is inferred from it.

## UX-04 — Approving a tool call re-sends the latest user message, not the approved turn's; a mid-stream approval is silently lost
- **Type / Severity / Evidence:** defect · High · CODE-VERIFIED
- **Files:lines:**
  - `aura_client/lib/features/chat/data/chat_repository.dart:253-282` (`confirmTaintedTools` always replays `userMessages.last.content`, and removes the pending entries **before** sending)
  - `:151-154` (`sendChatMessage` returns quietly when another stream is in flight)
  - `:404-411` (pending calls from earlier turns are deliberately kept, via `addPending`)
  - The card is drawn per turn on every assistant bubble: `aura_client/lib/features/chat/presentation/widgets/message_bubble.dart:280-284`, and `engineer_confirmation_card.dart:51-54, 107-113`.
  - `chat_view_model.dart:136-146`.
- **Trigger, (a) wrong turn:** turn 1 produces a pending `write_file`. The user sends turn 2 and then taps Confirm on turn 1's card. The confirmation replays turn 2's text with turn 1's call ids.
- **Trigger, (b) lost approval:** the user taps Confirm while any other stream is streaming. The pending entries are removed, `sendChatMessage` returns early, and the card disappears with nothing executed and no message.
- **Actual:** the approval is attached to the wrong prompt, or is dropped silently.
- **Expected:** a confirmation re-runs the turn it belongs to (its user message, its `turnId`). It is disabled while another stream is in flight, and pending entries are removed only after the request is accepted.
- **Impact:** a confusing, untrustworthy approval flow at the safety boundary. It compounds **CB-04** (backlog: confirmation not bound to the approved call; evidence there cites `chat_view_model.dart:139-147` and `chat_repository.dart:253-282`). CB-04 is **still open** in current code. Re-planning from a *different* prompt makes the server-side mismatch CB-04 describes more likely.
- **Root cause:** stateless "replay the last user message" design, with optimistic removal.
- **Minimal fix:** resolve the user message that precedes the bubble whose `turnId` matches, and send that. Guard with `if (streamingMessageId != null) return` (and disable the Confirm button) *before* mutating pending state. Remove entries after the stream starts.
- **Regression test:** seed two turns, put a pending call on turn 1, confirm it, and assert the SSE request body's `message` equals turn 1's text. A second test: with a streaming id set, `confirmTools` leaves `pendingConfirmationProvider` unchanged.
- **Confidence:** High.

## UX-05 — Live voice and chat share one global pending-confirmation list
- **Type / Severity / Evidence:** defect · Medium · CODE-VERIFIED
- **Files:lines:**
  - `aura_client/lib/features/voice_live/presentation/live_voice_view_model.dart:74-86` (confirm and reject both `.clear()` the whole provider) and `:128-139` (`setPending` replaces it with name-only calls)
  - `aura_client/lib/features/voice_live/presentation/live_voice_page.dart:94` (card with no `turnId`, which means "show all": `engineer_confirmation_card.dart:52-54`)
- **Trigger:** the chat has an unresolved pending call, and the user opens live voice (`/voice` from the chat header).
- **Actual:**
  - Live voice's card lists the chat's pending calls as if they were voice calls.
  - A voice confirmation frame wipes them (`setPending`), and confirm/reject in voice also clears them.
  - Only the voice tool names are sent (`_socket?.confirm(tools)`), so the user may believe they approved chat calls that were in fact discarded.
  - Voice confirmations show tool names with no arguments, so the user approves blind (acknowledged in the comment at `:129-132`).
- **Expected:** separate state per surface, or keyed by surface.
- **Impact:** lost approvals and a misleading approval UI.
- **Minimal fix:** give live voice its own `liveVoicePendingProvider`, and stop calling `pendingConfirmationProvider.notifier.clear()/setPending` from the voice VM.
- **Regression test:** pre-seed the chat pending list, drive a `LiveConfirmationFrame` and then `confirmPending()`, and assert the chat pending list is unchanged.
- **Confidence:** High.

## UX-06 — An expired backend session ends in a "Sign in again" screen with no sign-in action
- **Type / Severity / Evidence:** defect · Medium · CODE-VERIFIED
- **Files:lines:**
  - `aura_client/lib/features/auth/presentation/auth_provider.dart:137-150`: a cached UI session is accepted when *either* a refresh token *or* any leftover Google ID token is on disk ("Do not treat that leftover Google ID token as freshly refreshed").
  - `token_manager.dart:140-208`: when refresh fails, the refresh token is cleared; the exchange then uses a stale ID token and gets a non-200, so `getValidAccessToken` returns null. `aura_api_client.dart:35-36, 80-85` sends no auth header, and the server answers 401.
  - `aura_client/lib/core/network/aura_failure.dart:228-235`: `authRequired`, "Sign in again", `canRetry: false`.
  - `aura_failure_view.dart:73-86`: shows Retry only if `canRetry`. A secondary action is shown only if the caller passes one, and the sole caller (`desktop/agent/agent_panel.dart:176-177`) passes none.
  - `features/sync/sync_engine.dart:55-58`: unauthorized becomes a generic `SyncState.error`.
  - `lib/main.dart:91-110`: a global banner exists only for `deviceAttestationRejected`, not for an expired session.
- **Trigger:** the refresh token expires or is revoked (30-day TTL, or an admin "revoke sessions" via `api/admin_platform.py:141`).
- **Actual:** the router still sees `authProvider.value != null`, so no redirect to `/login` happens. Screens say "Your session expired. Signing in again…" with no button. Sync shows an error icon, and Chat shows connection-error bubbles. The only way out is to find You → Profile → Sign Out.
- **Expected:** a definitive `401` from refresh *and* exchange moves the auth state to signed-out (or shows a global "Session expired — Sign in" banner with an action), keeping local data.
- **Root cause:** backend-session validity is never fed back into `authProvider`.
- **Minimal fix:** on `AuthExchangeFailure.other` caused by a 401/403 response (not a network error), set a new `sessionExpiredProvider`. Have `main.dart`'s builder show a `MaterialBanner` with a "Sign in" action that calls `signOut()` and then `go('/login')`. Pass `onSecondaryAction` into `AuraFailureView` for `authRequired`.
- **Regression test:** a fake HTTP client where refresh gives 401 and token exchange gives 401. Assert `sessionExpiredProvider` is true and that the banner with its "Sign in" action is in the tree.
- **Confidence:** High.

## UX-07 — Saved memories put a permanent poison row in the sync outbox; conflicts are dropped silently
- **Type / Severity / Evidence:** defect · Medium · CODE-VERIFIED
- **Files:lines:**
  - `aura_client/lib/core/security/deterministic_id.dart:5-16`: the id is a **decimal uint64 string**, not a UUID.
  - `aura_client/lib/features/chat/data/memory_repository.dart:171-186`: `saveMemory` enqueues `memories`/`upsert` with that id.
  - `aura_backend/api/v2/sync.py:167-171`: `uuid.UUID(mutation.id)` raises `ValueError`, so the result is `status="error", detail="id must be a UUID"`.
  - `aura_client/lib/core/sync/outbox_ack.dart:13-19`: only `applied` or `conflict` rows are dropped, so `error` rows stay **forever**.
  - `sync_client.dart:146`: the push still returns success. `features/sync/sync_engine.dart:28`: resent every 60 s.
- **Trigger:** saving any memory from the Memory page.
- **Actual:** each saved memory adds an outbox row that is rejected on every sync. The outbox grows without bound, every push re-sends all of them, and the UI says "synced". The real save goes through `POST /api/v3/memory`, so the fact itself is not lost.
- **Conflict UX:** `outbox_ack.dart:17` drops `conflict` rows. The server version then overwrites the local edit on the next pull, and the user is never told their edit lost.
- **Expected:** valid UUIDs (for example a v5 UUID from the fact); permanent `error` results dead-lettered with a visible count; a "your change on X was replaced by a newer version" notice for conflicts.
- **Root cause:** the id scheme predates the UUID requirement of the v3 sync API, and there is no dead-letter path.
- **Minimal fix:** `Uuid().v5(namespace, fact)` for memory ids. In `outboxIdsToDrop`, also drop (and log to a `sync_dead_letters` table) results whose `detail` is permanent (`id must be a UUID`, `unknown table`, `not owned by this user`).
- **Regression test:** extend `test/outbox_ack_test.dart` and `sync_client_outbox_test.dart`: a push returning `error / id must be a UUID` must not leave the row pending forever. Add a unit test that `saveMemory`'s id parses as a UUID.
- **Confidence:** High.

## UX-08 — Memory page hides failed deletes and offline fallback; error state is never rendered
- **Type / Severity / Evidence:** defect · Medium · CODE-VERIFIED
- **Files:lines:**
  - `aura_client/lib/features/chat/data/memory_repository.dart:145-163` (delete removes the row locally, then a remote failure is only `debugPrint`ed)
  - `:136-140` (the sync failure silently returns the local cache, with `kind` lost; it defaults to `semantic` per `MemoryItem`)
  - `:117-130` (the pull only upserts and never removes rows deleted on the server)
  - `aura_client/lib/features/memory/presentation/memory_view_model.dart:56-64, 86-89` (`remove` → `refresh`)
  - `memory_page.dart:64-72` (renders loading, empty or list; `state.error` is never shown)
- **Trigger:** delete a memory while offline or while the server errors; or open Memory offline.
- **Actual:** the deleted item disappears, then comes back on the next refresh with no explanation. Offline, the list looks live but is stale and the kind filters are wrong.
- **Expected:** this is a privacy-control surface ("what AURA remembers"). A failed delete must say so; offline data must be labelled stale.
- **Minimal fix:** have `deleteMemory` return or throw on non-2xx and show an `AuraSnackbar` error, and roll back or keep a "pending delete" state. Return an `isStale` flag from `syncMemories` and render a banner. Render `state.error` through `AuraFailureView`. Ask for confirmation before deleting.
- **Regression test:** a fake client returning 500 on DELETE. Assert a snackbar or error is shown and the item is still marked pending.
- **Confidence:** High.

## UX-09 — With the Desktop IDE switched on, "Today" can't be reached, and there's no open/leave IDE command
- **Type / Severity / Evidence:** defect · Medium · CODE-VERIFIED
- **Files:lines:**
  - `aura_client/lib/core/routing/router.dart:256-263`: every navigation to `/today` while `desktop_use_ide` is set redirects to `/ide`. The "Today" destination calls `context.go('/today')` (`:65-68`).
  - `features/settings/presentation/settings_page.dart:170-210`: the comment claims "never a forced redirect, per ADR-015".
  - `docs/architecture/015-desktop-ide.md:50-55`: "No forced redirect … 'Open Desktop IDE' command in palette."
  - `features/command_palette/command_palette.dart:21-38`: no `/ide` entry. `desktop/shell/desktop_commands.dart:51-160`: no command to go back to AURA Home.
  - From the IDE, the only route out is `desktop/host/host_panel.dart:56` ("Full Settings" → `/engineer-daemon`).
- **Trigger:** a Windows user turns on "Desktop IDE".
- **Actual:** the Today tab (the rail item, command palette "Today", post-login landing) always bounces to the IDE. From the IDE there's no discoverable way back to Home or Settings to switch it off.
- **Expected:** the preference picks the *landing* route only (for example, applied at launch or login), plus palette commands in both shells ("Open Desktop IDE" / "Open AURA Home").
- **Minimal fix:** apply the IDE redirect only when `state.matchedLocation == '/today'` *and* the previous location is null or `/login` (pass a one-shot flag set at startup). Add `_Destination('Desktop IDE', …, '/ide')` when `isDesktopIde`, and an "AURA Home" command in `buildDesktopCommands`.
- **Regression test:** a router test with the pref set: the initial location goes to `/ide`, then `router.go('/today')` stays on `/today`.
- **Confidence:** High.

## UX-10 — aura_admin: refresh is not single-flight, so multi-fetch pages send the admin to /login after 15 minutes
- **Type / Severity / Evidence:** defect · Medium · CODE-VERIFIED
- **Files:lines:**
  - `aura_admin/app/api/v3/[...path]/route.ts:6-20, 43-51`: each proxied request that gets a 401 independently calls `/auth/refresh` with the same refresh cookie.
  - `aura_backend/services/jwt_auth.py:226-233`: rotation; the old token "stops working immediately".
  - `aura_admin/src/lib/api.ts:43-46`: any 401 does `window.location.assign('/login')`.
  - Parallel loads: `app/(console)/models/page.tsx:24` (5 requests), plus `access/page.tsx:17`, `knowledge/page.tsx:18` and `learning/page.tsx:44`, all using `Promise.all`.
  - `app/login/page.tsx:21-34`: does not check for an existing session and always lands on `/` afterwards, so the deep link is lost.
- **Trigger:** the access cookie has expired (`maxAge` 900 s at `route.ts:63`), and the admin opens or reloads one of those pages.
- **Actual:** one request wins the rotation and the others get `null`, returning 401. The client redirects to /login although a valid rotated cookie was just set, and the admin must sign in with Google again and re-navigate.
- **Expected:** concurrent refreshes coalesce, or the client retries once after a 401 before redirecting.
- **Minimal fix:** in `api.ts`, on a 401, retry the request once (the winning response has already set the cookies) before redirecting, and redirect to `/login?next=<path>`. Better still, add a short-lived in-process promise cache in the route handler keyed by the refresh token.
- **Regression test:** extend `aura_admin/tests/e2e/mock-gateway.mjs` so refresh is single-use and access tokens expire. Load `/models` with an expired access cookie and assert there's no navigation to `/login`.
- **Confidence:** High (Next route handlers serve concurrent requests in one process; there's no lock).

## UX-11 — Settings sub-pages are opened with `go`: no back arrow, and Android back exits the app
- **Type / Severity / Evidence:** defect · Medium · CODE-VERIFIED navigation; device behaviour inferred
- **Files:lines:**
  - `aura_client/lib/features/settings/presentation/settings_page.dart:36, 43, 50, 73, 80, 87, 94, 101, 109, 117, 125`: all `context.go(…)`.
  - The sub-pages use a plain `AppBar(` with no `leading`, so `automaticallyImplyLeading` needs `canPop`: `memory_page.dart:18`, `appearance_page.dart:17`, `knowledge_center_page.dart:72`, `model_manager_page.dart:18`, `profile_page.dart:59`.
  - `aura_client/lib/core/routing/router.dart:45-63`: `/engineer-history`, `/github-connect`, `/engineer-daemon`, `/projects` and `/goals` are missing from the "You" group, so the rail highlights **Today** on those pages. `persona_picker.dart:143` also uses `go('/engineer-history')`.
- **Trigger:** You → Knowledge (or any row) on Android/iOS.
- **Actual:** no back affordance in the app bar. The system back gesture finds an empty stack and leaves the app.
- **Expected:** `context.push` for drill-down rows, as Life Hub already does (`life_hub_page.dart:107-109`), so there's a back arrow; and the right tab highlighted.
- **Minimal fix:** switch these to `push`, and add the engineer, project and goal paths to the index-3 or index-2 groups.
- **Regression test:** extend `test/shell_ia_test.dart`: tap a You row, expect a `BackButton`, `pop`, and be back on `/you`.
- **Confidence:** High for navigation semantics; behaviour per platform not observed.

## UX-12 — One-tap destructive actions with no confirmation or undo
- **Type / Severity / Evidence:** defect · Medium · CODE-VERIFIED
- **Files:lines:**
  - Delete conversation: `features/chat/presentation/widgets/chat_sidebar.dart:82-89`, then `chat_view_model.dart:84-87`, then `chat_repository.dart:546-552`. It deletes every message and enqueues server deletes. There's no tooltip either.
  - Delete task: `features/tasks/presentation/widgets/task_item_card.dart:140-144`, low-contrast `Colors.white24` icon, no tooltip.
  - Sign-out from content headers: `features/goals/presentation/goals_page.dart:73-77` and `features/projects/presentation/projects_page.dart:74-78`, an unconfirmed `signOut()` placed next to "Welcome back"; these pages are reached from Life Hub (`life_hub_page.dart:107-109`).
  - `grep -rn "Undo" lib/features` finds nothing.
- **Impact:** irreversible data loss from a mis-tap, made worse by UX-03 (sign-out doesn't revoke, but it does drop tokens).
- **Minimal fix:** an `AuraDialog` confirmation for deleting a conversation and for sign-out. An `AuraSnackbar` with Undo for deleting a task (a soft delete before the outbox enqueue). Remove the sign-out buttons from the Goals and Projects headers.
- **Regression test:** a widget test: tap delete conversation and expect a dialog; cancel, and the messages are still there.
- **Confidence:** High.

## UX-13 — 21 of 64 IconButtons have no tooltip or label, including chat Send
- **Type / Severity / Evidence:** defect (a11y) · Medium · CODE-VERIFIED count; screen-reader output inferred
- **Method:** a paren-balanced scan of every `IconButton(` under `aura_client/lib` for a `tooltip` argument (script at `/tmp/claude-0/-home-user-AURA-AI/63d8bd8f-7b21-5f4e-9eb5-d66ce864e54a/scratchpad/iconbtn.py`).
- **Notable misses:**
  - `chat/presentation/widgets/chat_composer.dart:91` (Send, the main chat action)
  - `chat/presentation/widgets/chat_sidebar.dart:82` (delete conversation)
  - `voice_live/presentation/live_voice_page.dart:60` (close live session)
  - `tasks/presentation/widgets/task_item_card.dart:40` (complete toggle) and `:140` (delete)
  - `calendar/presentation/widgets/calendar_month_grid.dart:36-37` (month prev/next)
  - `engineering/presentation/host_daemon_page.dart:280-281`
  - `chat/presentation/widgets/memory_bank_dialog.dart:61, 145`
  - `analytics/presentation/analytics_page.dart:31`
- **Wider context:** only 15 `Semantics(` and 9 `semanticLabel`/`ExcludeSemantics`/`MergeSemantics` uses exist in `lib/`. No `textScaler` clamping exists anywhere, so system text scaling is honoured, but about 294 raw `fontSize:` sites (F-04) make overflow at large scales likely. That last part is UNVERIFIED and needs a device.
- **Impact:** TalkBack and VoiceOver announce an unlabeled "button" for Send and delete. Desktop and web users get no hover hint.
- **Minimal fix:** add `tooltip:` to each of the 21. Add a lint-style test that scans `lib/` and fails on an `IconButton(` without `tooltip`, ratcheting down from 21.
- **Confidence:** High for the count. Semantics behaviour is not observed.

## UX-14 — l10n is a stub: 34 English keys, about 198 hardcoded strings, no RTL-aware layout
- **Type / Severity / Evidence:** opportunity · Medium · CODE-VERIFIED
- **Files:lines:**
  - `aura_client/lib/l10n/app_en.arb` is the only ARB file (34 keys, 34 `@` descriptions). `l10n.yaml` uses it as template.
  - `lib/main.dart:76-84` wires delegates; the comment says "only English shipped so far".
  - 26 `l10n`/`AppLocalizations.of` references in 3 files (`router.dart`, `home_page.dart`, `notification_center.dart`).
  - `grep -E "Text\(\s*'…'"` finds at least 198 single-line hardcoded literals, before counting multi-line, `label:` and `title:` strings.
  - 0 uses of `EdgeInsetsDirectional`, `Directionality` or `TextDirection.rtl`.
- **Context:** Arabic TTS ships, and Arabic ASR is planned (backlog **CB-29**, GAP-020; master roadmap line 425), but the UI can't be presented in Arabic. There's no `app_ar.arb` to compare key counts against: the count is 34 vs 0.
- **Recommended:** decide whether Arabic UI is in scope (the roadmap only commits to voice). If it is, extract strings screen by screen into the ARB, add `app_ar.arb`, switch `EdgeInsets.only(left/right)` to directional, and add a widget test that pumps the shell under `Locale('ar')` and asserts there are no overflow errors.
- **Confidence:** High.

## UX-15 — Raw exception strings shown to users although `AuraFailure` exists
- **Type / Severity / Evidence:** opportunity · Low-Medium · CODE-VERIFIED
- **Files:lines:** `features/goals/presentation/goals_page.dart:139` and `features/projects/presentation/projects_page.dart:140` (`Text('Error: $e')`); `features/life_hub/presentation/life_hub_page.dart:278` (`Text('$e')`); `features/auth/presentation/login_page.dart:208` (`'Error: ${authState.error}'`, also in release builds); `knowledge_view_model.dart:72`, `memory_view_model.dart:63` and `live_voice_view_model.dart:65` (`e.toString()` into state).
- **By contrast:** `aura_client/lib/core/network/aura_failure.dart:98` (`AuraFailure.from`) maps errors to human copy with retry semantics, and its only use outside `core/network` is `desktop/agent/agent_panel.dart:177`.
- **Fix:** route these through `AuraFailure.from(e)` and `AuraFailureView` (or a snackbar using `failure.title/message`).
- **Confidence:** High.

## UX-16 — A failure mid-stream overwrites the partial answer with the error bubble
- **Type / Severity / Evidence:** defect · Low · CODE-VERIFIED
- **Files:lines:** `aura_client/lib/features/chat/data/chat_repository.dart:211-216` (`_updateMessageText(assistantMsgId, '$kChatConnectionErrorPrefix: …')` replaces `displayText`) and `:229-251` (retry deletes the user message and the error row, then re-sends).
- **Trigger:** an SSE `error` frame or exception after some tokens have streamed.
- **Actual:** the streamed text the user was reading disappears and only an error bubble remains.
- **Expected:** keep the partial text and append a "response interrupted — Retry" footer. This matches the server rule in CLAUDE.md that failure after the first token surfaces as an error rather than being hidden.
- **Fix:** store the error in `TurnMeta` (or a flag) instead of replacing `content`; render both.
- **Confidence:** High.

## UX-17 — Routing edges
- **Type / Severity / Evidence:** defect · Low · CODE-VERIFIED
- `aura_client/lib/core/routing/router.dart:362-365`: `HostFileBrowserPage(root: state.extra as String)`. A web refresh or deep link to `/engineer-daemon/browse` has null `extra`, and the cast throws a `TypeError` in the builder. There's no `errorBuilder` on the `GoRouter` (`:234-394`).
- `router.dart:378-381`: `/ide` builds `SizedBox.shrink()` on non-Windows, so a deep link shows a blank page instead of redirecting to `/today`.
- `aura_client/lib/features/chat/data/chat_repository.dart:103-109`: `watchConversations()` says "ordering by latest" but has no `orderBy`, so the order of the session list is unspecified.
- **Fixes:** use `state.extra as String?` and redirect to `/engineer-daemon` when it's null; add a redirect `/ide*` → `/today` when `!isDesktopIde`; order by `max(createdAt) desc`.
- **Confidence:** High.

## UX-18 — Backend endpoints with no client caller that the UX needs
- **Type / Severity / Evidence:** opportunity · Medium · CODE-VERIFIED
- **Method:** route decorators in `aura_backend/aura_core/api/v3.py` and the routers mounted by `aura_backend/aura_service.py:125-139`, compared with `/api/v3…` strings in `aura_client/lib`, `aura_hostd/lib` and `aura_admin`.
- **No caller anywhere, and relevant to UX:**
  - `POST /api/v3/auth/revoke` (`api/v2/auth.py:186`): not called by the Flutter client (see UX-03). aura_admin does call it.
  - `POST /api/v3/auth/desktop/devices/revoke` (`api/v2/auth.py:230`): a user can pair a desktop daemon from the app (`pair/approve`) but can't unpair it.
  - `GET /api/v3/agents/runs` and `/agents/runs/{run_id}` (`v3.py:1245, 1266`): no run-history UI.
  - `GET /api/v3/knowledge/graph` and `/knowledge/graph/entities/{entity_key}` (`v3.py:863, 898`).
  - `GET /api/v3/tools` (`v3.py:1095`).
  - `/api/v3/learning/*` (`api/learning.py:26-64`).
- **Reverse check (client calls a missing route):** none found. Every `/api/v3/...` path in the client resolves to a mounted route: chat/stream, complete, knowledge documents/upload/search/cag/cag-pin, voice transcribe/speak/live, memory, feedback, agents, engineering workspaces/runs, insights (+generate/dismiss), sync, integrations/github, automation, auth nonce/token/refresh/desktop.
- **Backlog:** **CB-30** ("16 uncalled routes") is still open; this list is consistent with it.
- **Recommended:** wire revoke on sign-out (UX-03) and a "Paired devices → Revoke" row on the Host Daemon page. Declare the rest as API-only, or service-key-only (`/chat` non-stream is ADR-029's service surface).

---

## Coverage

**Inspected (read):**
- `aura_client/lib/core/routing/router.dart` (whole)
- `features/settings/presentation/settings_page.dart`, `command_palette/command_palette.dart`, `desktop/shell/desktop_shell.dart`, `desktop_commands.dart` (titles), `activity_bar.dart` (Settings note), `desktop/host/host_panel.dart`
- Auth:
  - `features/auth/presentation/auth_provider.dart`, `login_page.dart` (dev panel and error), `profile_page.dart` (sign-out card)
  - `core/auth/token_manager.dart`, `core/network/aura_api_client.dart`, `aura_failure.dart`, `aura_failure_view.dart`, `lib/main.dart`
- Sync:
  - `core/sync/sync_client.dart`, `outbox_ack.dart`, `features/sync/sync_engine.dart`, `core/database/database.dart` (outbox, wipe)
  - backend `api/v2/sync.py`, `infrastructure/db/models.py:262-271`
- Chat:
  - `features/chat/presentation/chat_view_model.dart`, `data/chat_repository.dart` (lines 1-460, 540-560)
  - `widgets/engineer_confirmation_card.dart` (1-200), `message_bubble.dart` (260-300), `chat_composer.dart`, `chat_sidebar.dart`
  - `core/ai/online_agents/sse_agent_client.dart`, `llm_timeouts.dart`
- Live voice: `features/voice_live/presentation/live_voice_view_model.dart` (70-140), `live_voice_page.dart` (55-66)
- Memory: `features/chat/data/memory_repository.dart`, `features/memory/presentation/*`, `core/security/deterministic_id.dart`, `core/ai/memory/aura_memory.dart`
- Theming:
  - `core/theme/theme.dart`, `theme_mode_controller.dart`
  - `core/design_system/aura_palette.dart` (light), `aura_theme_v2.dart` (cardTheme)
  - `tasks/.../task_item_card.dart`, `empty_state_tasks.dart`, `health/.../health_daily_dashboard.dart`, `life_hub_page.dart` (grep), `goals_page.dart`, `projects_page.dart`
- Endpoint inventory: backend route decorators and client/admin/hostd path strings, as in UX-18.
- `aura_admin`: `app/api/v3/[...path]/route.ts`, `src/lib/api.ts`, `app/login/page.tsx`, `models/page.tsx` (load); `Promise.all` grep.
- Docs: `docs/ux/UX_BASELINE_AUDIT.md` (F-03, F-04, status table, not-audited list), `docs/architecture/015-desktop-ide.md:30-70`, `docs/roadmaps/AURA_CRITICAL_BACKLOG.md` (whole).

**Not inspected / not settled:**
- Anything at runtime: no Flutter SDK, so no `flutter analyze` or `flutter test` run and no counts quoted. Screen-reader output, focus order, text-scale overflow, and how the colours actually look are all inferred.
- The Desktop IDE internals beyond the shell, commands and host panel: editor, explorer, git, terminal, agent panel (except the line using `AuraFailure`). The desktop approvals dialog is already covered by F-03 (🚫 blocked on protocol) and was not re-audited.
- Knowledge Center upload and quarantine UX, the onboarding flow, finance/calendar dialogs (form validation), automations tabs, analytics, the model manager, the GitHub connection flow.
- aura_admin pages beyond the load functions, its `confirm-dialog.tsx`, accessibility of the admin UI, and the Playwright suite.
- `aura_hostd` UX (CLI).

## Cleared hypotheses (checked and rejected)
- **The developer "mock sign-in" bypass is reachable in release.** Rejected: `login_page.dart` gates the panel with `kDebugMode` (lines 140-170 region, `if (kDebugMode)`), and the debug operator exchange is also `kDebugMode`-gated (`token_manager.dart:213`).
- **The client calls v3 endpoints that don't exist.** Rejected: every client path resolves to a mounted route (UX-18).
- **Concurrent token refresh double-spends the refresh token in the Flutter client.** Rejected: `TokenManager.getValidAccessToken` shares one in-flight future (`token_manager.dart:90-106`, AURA-010). The double-spend survives only in aura_admin's proxy (UX-10).
- **Outbox error rows are wiped on HTTP 200.** Rejected as a current bug: it was fixed (`outbox_ack.dart`, `sync_client.dart:131-145`). The opposite problem, permanent errors retained forever, is UX-07.
- **Profile page is unreadable in light mode.** Rejected: it forces `backgroundColor: AuraTheme.background` (dark) at `profile_page.dart:58`, so white text is legible there. It just ignores the theme (an F-04 consistency issue). Goals and Projects do the same (`goals_page.dart:22`, `projects_page.dart:23`).
- **The Desktop IDE is unreachable.** Rejected: `settings_page.dart:170-210` adds the toggle. The real problem is the reverse: Home becomes unreachable (UX-09).
- **`/engineer-daemon/browse` crashes on desktop.** Rejected for desktop: the only caller passes `extra` (`host_daemon_page.dart:392`). Only a web refresh or deep link hits the null cast (UX-17).
