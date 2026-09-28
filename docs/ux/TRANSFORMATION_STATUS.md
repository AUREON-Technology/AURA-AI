# UX Transformation — Phase Status

One row per phase of the UX transformation brief. A phase is complete only when implementation
exists **and** behaviour works **and** tests pass **and** accessibility and responsive behaviour
are checked **and** architecture is preserved **and** the evidence is recorded. Code existing is
not completion; CI passing is not completion either, where the acceptance criterion needs a
running app.

**Verification available here:** the real toolchain. `UX_TEST_PLAN.md` has the two-minute recipe
for fetching the Flutter version `client-ci.yml` pins and running `analyze` + `test` locally —
which is how every "awaiting CI" row below became a measured result instead of a hope.

**Last local run** (Flutter 3.44.6 / Dart 3.12.2, the CI pin, against `bb23079`):
`flutter analyze` → **No issues found!**; `flutter test` → **299 passed, 0 failed, 3 skipped**,
including the 28 in `test/ux/`.

**GitHub `Client CI` is not a gate right now.** It has not been assigned a runner in weeks: every
run, on `main` as well as on PRs, fails in 2–4 seconds with no step starting and no log archive.
Local runs are the only real verification until that is fixed at the repository/account level.

| Phase | Scope | Status | Evidence |
|---|---|---|---|
| 0 | UX baseline audit | **COMPLETE** | `UX_BASELINE_AUDIT.md` — 12 findings, each citing file and line |
| 1 | Design tokens | **COMPLETE** (analyze clean, 299 tests pass locally) | `DESIGN_SYSTEM.md`, `test/ux/design_tokens_test.dart`, contrast gate extended |
| 2 | Shared component system | **NOT STARTED** | 10 of ~25 target components exist; gap enumerated in audit §2 |
| 3 | Desktop shell | **PARTIALLY IMPLEMENTED** | F-01 and F-02 fixed, with passing tests. F-06 (responsive) and F-09 (attention badges) NOT STARTED |
| 4 | Explorer + Editor | **NOT STARTED** | F-08 open |
| 5 | Command system, Terminal, Git, Diff | **NOT STARTED** | F-07 open |
| 6 | Problems + Tests + Debug | **NOT STARTED** | |
| 7 | AURA AI interaction | **NOT STARTED** | |
| 8 | Agent Workspace | **NOT STARTED** | |
| 9 | Approval + Security UX | **BLOCKED** | F-03: the approval payload carries `command`, `root`, `paths`, `diff_preview` and nothing else. Reason, risk and reversibility are not transmitted, and inventing them client-side would be fabrication. Protocol change across backend + hostd + client is the prerequisite |
| 10 | Host Management | **NOT STARTED** | |
| 11 | Universal loading/error/empty/offline states | **NOT STARTED** | `AuraEmptyState` and `AuraSkeleton` exist as raw material |
| 12 | Mobile UX | **NOT STARTED** | Not audited yet — see audit §3 |
| 13 | Accessibility | **PARTIALLY IMPLEMENTED** | Contrast gate pre-existing and extended; activity bar semantics and focus added. Keyboard traversal, focus visuals and screen-reader output all unverified |
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

1. **F-06** — responsive desktop shell (wide / standard / compact). The largest single change to
   the shell, which is why it is not in the same diff as the Phase 3 honesty fixes.
2. **F-09** — attention badges on the activity bar. The bottom panel is collapsed by default, so a
   new diagnostic is currently invisible.
3. **F-08** — make unimplemented editor commands throw rather than `break`, so wiring one to a
   menu fails in a test instead of quietly in production.
4. **Phase 2** — promote the highest-traffic one-off widgets (status badge, tree item, toolbar,
   context menu) into the shared component set.
