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
`flutter analyze` → **No issues found!**; `flutter test` → **377 passed, 0 failed, 3 skipped**,
including the 111 in `test/ux/`.

**GitHub `Client CI` is not a gate right now.** It has not been assigned a runner in weeks: every
run, on `main` as well as on PRs, fails in 2–4 seconds with no step starting and no log archive.
Local runs are the only real verification until that is fixed at the repository/account level.

| Phase | Scope | Status | Evidence |
|---|---|---|---|
| 0 | UX baseline audit | **COMPLETE** | `UX_BASELINE_AUDIT.md` — 12 findings, each citing file and line |
| 1 | Design tokens | **COMPLETE** (analyze clean, 299 tests pass locally) | `DESIGN_SYSTEM.md`, `test/ux/design_tokens_test.dart`, contrast gate extended |
| 2 | Shared component system | **PARTIALLY IMPLEMENTED** | `AuraCommandPalette` added; `AuraTextField` gained autofocus/focusNode/prefixIcon so search inputs need not drop to a raw TextField. 11 of ~27 target components exist — `COMPONENT_GUIDE.md` lists the rest in duplication order |
| 3 | Desktop shell | **IMPLEMENTED** | F-01, F-02, F-06 and F-09 all closed with passing tests. Shell *rendering* at three surface sizes still uncovered — the layout rules are tested as a value, not as pixels |
| 4 | Explorer + Editor | **PARTIALLY IMPLEMENTED** | F-08 closed (unimplemented editor commands now throw). Explorer search/filter, editor breadcrumbs, symbols and the selection→AURA actions NOT STARTED |
| 5 | Command system, Terminal, Git, Diff | **PARTIALLY IMPLEMENTED** | F-07 closed — Ctrl/Cmd+Shift+P over a shared palette component, 28 tests. Terminal, Git and Diff UX untouched; mobile's palette not yet migrated onto the shared component |
| 6 | Problems + Tests + Debug | **PARTIALLY IMPLEMENTED** | F-09 closed — the problem count is now visible with the panel collapsed. Test Explorer and the unified diagnostics experience NOT STARTED |
| 7 | AURA AI interaction | **NOT STARTED** | |
| 8 | Agent Workspace | **NOT STARTED** | |
| 9 | Approval + Security UX | **BLOCKED** | F-03: the approval payload carries `command`, `root`, `paths`, `diff_preview` and nothing else. Reason, risk and reversibility are not transmitted, and inventing them client-side would be fabrication. Protocol change across backend + hostd + client is the prerequisite |
| 10 | Host Management | **NOT STARTED** | |
| 11 | Universal loading/error/empty/offline states | **IMPLEMENTED for failures** | `AuraStateTone` + `AuraFailure`/`AuraFailureView`: the backend's ~30 stable `kind` slugs classify into 12 distinct states with their own copy, and Retry appears only where retrying can help. `syncing` / `stale` / explicit `retrying` are NOT STARTED, and per-screen adoption beyond the agent panel and Explorer is ongoing |
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

1. **Adopt `AuraFailureView` across the remaining error surfaces.** The classifier exists and is
   tested; most screens still render `$err` in red. This is mechanical and high-value — one
   screen per commit.
2. **`syncing` / `stale`** — the two states left from rule 19's vocabulary. Both need a real
   signal first: check what the sync layer actually exposes before designing UI for it, the way
   the failure states were built on the backend's existing `kind` slugs rather than on invented
   distinctions.
3. **Phase 2 proper** — promote the most-duplicated one-offs (tree item, status badge, toolbar,
   context menu) into the shared set, in duplication order. See `COMPONENT_GUIDE.md`.
4. **Migrate the mobile command palette** onto `AuraCommandPalette`, behind tests written first.
5. **F-04 / F-05** — the per-screen colour, type and monospace migration.

Phase 9 stays blocked until the approval payload carries reason, risk and reversibility.

## Known limit of the failure states

Rule 35 asks for *internet unavailable* and *Control Plane unavailable* as separate states. **The
client cannot distinguish them.** A failed socket arrives as `AuraApiError.unreachable` with no
status code, and that is equally consistent with a dead network and a dead server. Both therefore
render as one state worded not to claim either — saying "you are offline" when the server is down
sends the user to check their wifi for nothing. Splitting them would need a reachability probe
against a third party, which is a design decision (and a privacy one) rather than a UI change.
