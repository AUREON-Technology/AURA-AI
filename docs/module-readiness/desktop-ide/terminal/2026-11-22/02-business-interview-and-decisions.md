# Terminal — decision log (2026-11-22)

| ID | Decision | Source | Affects | Status |
|---|---|---|---|---|
| D-1 | Terminal scrollback stays persisted for restore, but **encrypted** with the same mechanism chat content uses; existing plaintext `desktopTerminalState` rows are wiped once rather than migrated. | USER (chose the recommendation, 2026-11-22) | G-02; `TerminalNotifier._persist/_loadSession`, `database.dart` schema + migration | CONFIRMED. Open sub-question for the plan: whether to also lower the 500 000-char cap. |
| D-0 | Scope = Desktop IDE integrated terminal (hostd PTY), not `run_command` or the chat `SandboxTerminalView`. | Agent assumption from the one-word request | all | ASSUMPTION, unconfirmed |
| D-2 | Revoking or untrusting a workspace root does **not** end its live terminals; the trust check stays connection-time only. Rationale: it is the user's own shell, and the trust gate decides who may start one, not who may keep one. | USER (2026-11-22) | G-04 (now an accepted behaviour, not a gap); the UI should not imply revoke closes terminals | CONFIRMED. The Host panel wording should say existing terminals stay open (to check in the plan). |
| D-3 | Multiple terminals per workspace, a per-terminal close/kill control, and ending a workspace's shells when the workspace closes. | USER (chose the recommendation, 2026-11-22) | G-03; `TerminalView`, `TerminalNotifier`, `TerminalSessionManager` | CONFIRMED. The per-workspace cap (proposed 8) is a recommendation, not yet approved. |
| D-4 | Windows shell stays `powershell.exe` (5.1) for this release; a shell picker is out of scope. | DEFERRED, agent default (low-risk, reversible); owner may reopen | G-08 | DEFERRED, non-blocking |
