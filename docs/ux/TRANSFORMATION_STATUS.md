# UX Transformation — Phase Status

One row per phase of the UX transformation brief. A phase is complete only when implementation
exists **and** behaviour works **and** tests pass **and** accessibility and responsive behaviour
are checked **and** architecture is preserved **and** the evidence is recorded. Code existing is
not completion; CI passing is not completion either, where the acceptance criterion needs a
running app.

**Verification available here:** the real toolchain. `UX_TEST_PLAN.md` has the two-minute recipe
for fetching the Flutter version `client-ci.yml` pins and running `analyze` + `test` locally —
which is how every "awaiting CI" row below became a measured result instead of a hope.

**Last local run** (Flutter 3.44.6 / Dart 3.12.2, the CI pin):
`flutter analyze` → **No issues found!**; `flutter test` → **477 passed, 0 failed, 3 skipped**,
including the 138 in `test/ux/`. The suite grew past the 381 this transformation last measured
because other work landed alongside it; the UX figure is the one this document is accountable
for.

**`Client CI` executed again on 2026-10-28, after weeks of not being assigned a runner — and it
confirmed these numbers.** `CLAUDE.md` records runs 142–143 running every step with
`analyze-and-test` green at 381 tests, which is exactly what the local runs below had been
reporting. Two other jobs are red for reasons of their own (`build-android` runs the runner out of
disk; `build-windows`, which is `continue-on-error`, fails 10 `path_containment_test.dart` cases on
8.3 short paths), so **read the `analyze-and-test` job for a client diff, not the run's overall
tick.**

It is intermittent, though, and worth knowing before you read a red X as a verdict: four of that
same day's runs — 148, 149, 150 and 151, the last being the one on the `#143` merge — still died in
2–4 seconds with `runner_id: 0` and no log archive. So a local run remains the verification that
is actually available on demand.

| Phase | Scope | Status | Evidence |
|---|---|---|---|
| 0 | UX baseline audit | **COMPLETE** | `UX_BASELINE_AUDIT.md` — 12 findings, each citing file and line |
| 1 | Design tokens | **COMPLETE** (analyze clean; suite green) | `DESIGN_SYSTEM.md`, `test/ux/design_tokens_test.dart`, contrast gate extended |
| 2 | Shared component system | **PARTIALLY IMPLEMENTED** | `AuraCommandPalette` added; `AuraTextField` gained autofocus/focusNode/prefixIcon so search inputs need not drop to a raw TextField. 11 of ~27 target components exist — `COMPONENT_GUIDE.md` lists the rest in duplication order |
| 3 | Desktop shell | **IMPLEMENTED** | F-01, F-02, F-06 and F-09 all closed with passing tests. Shell *rendering* at three surface sizes still uncovered — the layout rules are tested as a value, not as pixels |
| 4 | Explorer + Editor | **PARTIALLY IMPLEMENTED** | F-08 closed (unimplemented editor commands now throw). Explorer search/filter, editor breadcrumbs, symbols and the selection→AURA actions NOT STARTED |
| 5 | Command system, Terminal, Git, Diff | **PARTIALLY IMPLEMENTED** | F-07 closed — Ctrl/Cmd+Shift+P over a shared palette component, 28 tests. Terminal, Git and Diff UX untouched; mobile's palette not yet migrated onto the shared component |
| 6 | Problems + Tests + Debug | **PARTIALLY IMPLEMENTED** | F-09 closed — the problem count is now visible with the panel collapsed. Test Explorer and the unified diagnostics experience NOT STARTED |
| 7 | AURA AI interaction | **PARTIALLY IMPLEMENTED — not by this transformation** | Much of rule 12's progressive disclosure exists in `features/chat/presentation/widgets/`: `step_log_panel.dart` renders the plan/tool steps from the backend's `step` SSE frames, `tool_call_chip.dart` and `turn_status_row.dart` the execution, `citation_chip.dart` / `user_facing_citations.dart` the sources, `streaming_message_skeleton.dart` the wait. This transformation added the one trust signal that was arriving and rendering nowhere: `TurnMeta.tainted`, i.e. the turn's context was quarantined (ADR-006). Still missing: **context inspection** — what AURA *knows* for this turn (current file, relevant files, diagnostics, git state) — and rule 13's structured response sections |
| 8 | Agent Workspace | **PARTIALLY IMPLEMENTED — not by this transformation** | The pieces exist as message-level widgets: `sandbox_diff_view.dart`, `sandbox_file_view.dart`, `sandbox_terminal_view.dart`, `pending_actions_card.dart`, `engineer_confirmation_card.dart`, `github_branch_summary.dart`. What rule 15 asks for and does not exist is the **run as a first-class view**: task, plan with a current step, changed-file set, test status and security state as regions you can look at, rather than fragments scattered down a transcript you have to scroll |
| 9 | Approval + Security UX | **BLOCKED** | F-03: the approval payload carries `command`, `root`, `paths`, `diff_preview` and nothing else. Reason, risk and reversibility are not transmitted, and inventing them client-side would be fabrication. Protocol change across backend + hostd + client is the prerequisite |
| 10 | Host Management | **NOT STARTED** | |
| 11 | Universal loading/error/empty/offline states | **COMPLETE** | Failures: `AuraFailure`/`AuraFailureView` classify the backend's ~30 `kind` slugs into 12 states, Retry only where it helps, a `compact` form for one-slot failures, adopted across 13 surfaces. Sync: `AuraSyncStatusView` folds `SyncState` and the parked count into five phases including `pendingRefused`, plus `SyncAlertBanner` in the app shell so an unsaved change is visible on every screen and not just in chat |
| 12 | Mobile UX | **NOT STARTED** | Not audited yet — see audit §3 |
| 13 | Accessibility | **PARTIALLY IMPLEMENTED** | Contrast gate extended; activity bar semantics, focus and non-colour badge counts added; Escape dismisses a floating panel. Keyboard traversal order, focus visuals and real screen-reader output all still unverified |
| 14 | Themes + Personalization | **PARTIALLY PRE-EXISTING** | Light/dark via `AuraPalette`; desktop layout persistence already real |
| 15 | Motion + micro-interactions | **PARTIALLY PRE-EXISTING** | `AuraMotion` tokens with a reduced-motion guard exist; adoption across call sites unaudited |
| 16 | Performance / perceived latency | **NOT STARTED** | Every metric in the brief is **UNKNOWN** — needs a device |
| 17 | UX evaluation | **NOT STARTED** | Cannot begin before there is something running to evaluate |
| 18 | Golden-path certification | **NOT STARTED** | Requires a paired hostd, a real repository and a device |

## Carried debt (from the audit, not yet scheduled)

- **F-04** — 99 hardcoded `Color(0x…)` and 296 raw `fontSize:` outside the design system, across
  ~40 files. To be migrated screen by screen with a contrast assertion each, not as one sweeping
  commit: an unreviewed bulk colour change is how a dark-mode regression ships.
- **F-05** — 19 of 20 files still carrying `fontFamily: 'Courier'` literals.
- **F-10** — the UX suite covers the shell only.

## Next

In order, and each on its own commit:

1. **Phase 2 proper** — promote the most-duplicated one-offs (tree item, toolbar, context menu)
   into the shared set, in duplication order. See `COMPONENT_GUIDE.md`.
2. **Migrate the mobile command palette** onto `AuraCommandPalette`, behind tests written first.
3. **F-04 / F-05** — the per-screen colour, type and monospace migration. Several of the screens
   touched by the failure-state adoption are still on V1's `AuraTheme` constants, which is the
   next thing a reader of those files will notice.

Phase 9 stays blocked until the approval payload carries reason, risk and reversibility.

## Deliberately still rendering their own error text

Not oversights — each is a different axis from the HTTP failures `AuraFailure` classifies:

- `desktop/host/host_panel.dart` — uses `describeHostdError`, a purpose-built describer for the
  local daemon. Host reachability is its own axis, with its own vocabulary.
- `features/auth/presentation/login_page.dart` — inline validation feedback under a form field,
  not a page-level state.
- `features/automations/.../gmail_view_model.dart` and `core/security/path_containment.dart` —
  strings, not widgets.

## Known limit of the failure states

Rule 35 asks for *internet unavailable* and *Control Plane unavailable* as separate states. **The
client cannot distinguish them.** A failed socket arrives as `AuraApiError.unreachable` with no
status code, and that is equally consistent with a dead network and a dead server. Both therefore
render as one state worded not to claim either — saying "you are offline" when the server is down
sends the user to check their wifi for nothing. Splitting them would need a reachability probe
against a third party, which is a design decision (and a privacy one) rather than a UI change.

## Why two phases say "not by this transformation"

A parallel workstream has been changing the client throughout this period (156 commits on `main`
between 2026-09-28 and 2026-10-28 alone). Two phases this document had recorded as NOT STARTED
were in fact substantially built by it — `step_log_panel.dart`'s own docstring notes the very
thing a Phase 7 audit would have found, that the `step` frames "have flowed from the backend
since the turn feature shipped" with nothing rendering them, and then renders them.

Recorded this way rather than quietly flipped to IMPLEMENTED for two reasons. The next person
reading this needs to know those surfaces exist so they extend them instead of rebuilding them.
And a status this document did not earn should not read as if it had.

The lesson for this file: a status table is only worth the date of its last measurement. Both
phases above were re-checked against the tree on 2026-10-30, by reading the widgets and their
mount points — not inferred from the roadmap.

## One trust signal that cannot be rendered yet

`TurnMeta.guardsComplete` parses as `json['guards_complete'] == true`. A backend that never sends
the field is therefore indistinguishable from one reporting guards that did not finish, and
warning on `!guardsComplete` would fire on every turn from an older backend. A false alarm about
safety checks is worse than the silence it replaces, so nothing renders it, and
`turn_trust_notes_test.dart` pins that restraint rather than leaving it to be rediscovered.

Making the field nullable — the shape `resolvedAgent` already uses, where absent and false are
different answers — is the prerequisite. It is a change to `turn_meta.dart` and the backend's
`done` frame, so it is recorded here rather than done here.

`tainted` has the opposite shape and so *is* rendered: `json['tainted'] == true` is only ever set
by an affirmative statement from the backend, which `kernel.py` makes on every `done` frame.

## A constraint worth knowing before you mount anything in the shell

`SyncEngine.build()` starts a `Timer.periodic(60s)` and fires a `synchronize()` microtask. Merely
*observing* sync state therefore starts a timer, which fails any widget test that pumps the
observer without overriding the provider — `overflow_layout_test` already carries such an override
for `ChatHeader`, and `shell_ia_test` needed one once `SyncAlertBanner` went into `ShellLayout`.

That is why the banner watches `parkedOutboxCountProvider` and not `syncEngineProvider`: a plain
database watch rather than an engine with a lifecycle, and the signal that actually matters here.
Even so, mounting it in the shell means a test that pumps *any* route now touches the database,
which is what the two `shell_ia_test` overrides pay for.

Making the engine lazy — no timer until something asks it to sync — would remove that tax for
every future shell test. It is a change to `features/sync/`, not to UX, so it is recorded here
rather than done here.
