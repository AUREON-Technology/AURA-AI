# Terminal — requirements and acceptance criteria (2026-11-22)

Source column: **D-n** = owner decision in `02-...`; **EXISTING** = behaviour already implemented and kept; **REC** = agent
recommendation, not yet approved.

| ID | Requirement (MUST/SHOULD) | Source | Acceptance criteria | Status today |
|---|---|---|---|---|
| FR-001 | A terminal MUST start only for a granted **and** trusted root and report refusals as `error` frames; a session MUST NOT outlive its connection. | EXISTING | Ungranted → `root_not_granted`; untrusted → `workspace_not_trusted`; socket drop ends the PTY and removes the session. | PASS (code); refusal test exists, drop/teardown untested (G-07) |
| FR-002 | Exit status MUST reach the UI; a normal `exit N` shows "Session ended (exit code N)" with Restart. | EXISTING | `exit 7` → `exited` frame 7 → UI text. | PASS (probe, Windows interactive); UI untested |
| SEC-001 | Persisted scrollback MUST be encrypted at rest with the mechanism chat content uses; pre-existing plaintext rows MUST be wiped once. | D-1 | Stored column does not contain a known marker string; a restored session still shows its last screen; an upgraded DB holds no plaintext terminal rows. | GAP (G-02) |
| SEC-002 | Revoking/untrusting a root MUST NOT end live terminals, and the Host panel SHOULD NOT imply that it does. | D-2 | Documented; test asserts existing session survives untrust; panel copy reviewed. | Behaviour matches; copy unchecked |
| FR-003 | The UI MUST allow creating additional terminals per workspace, switching between them, and closing/killing each; closing removes its PTY and its persisted entry. | D-3 | "+" adds a session; tabs list sessions; close sends `close`, PTY gone, `closeSession` called; restored state has no closed session. | GAP (G-03) |
| FR-004 | Closing a workspace MUST end that workspace's shells. | D-3 | After workspace close, daemon reports no live session for it. | GAP (G-03) |
| FR-005 | The daemon SHOULD cap concurrent sessions per workspace (REC: 8) and refuse the next with a coded `error` frame. | REC | 9th `start` gets the new code; UI shows a localized message. | GAP |
| FR-006 | Output MUST be decoded as a stream so multi-byte characters split across frames render intact. | EXISTING intent | Test: a character split across two frames renders once, with no U+FFFD. | GAP (G-01) |
| FR-007 | The PTY size MUST match the visible terminal after the session starts, regardless of layout/connect ordering. | EXISTING intent | After `started`, the client sends its current size; `$Host.UI.RawUI.WindowSize` matches the view. | UNVERIFIABLE (G-06) |
| DATA-001 | Scrollback truncation MUST NOT cut an escape sequence in half before replay (REC: cap on a line boundary). | REC | Replay of a truncated buffer produces no stray control text. | GAP (G-09) |
| NFR-001 | Bulk output (REC target: 50 MB `cat`) MUST NOT freeze the UI. | REC; threshold needs measuring first | Measured frame time and memory recorded before and after; target agreed after measurement. | UNVERIFIABLE (G-05) |
| UX-001 | The terminal's exclusion from the accessibility tree SHOULD be disclosed. | EXISTING trade-off | Documented in the user guide/status doc. | GAP (G-10) |
| UX-002 | The terminal MUST stay left-to-right under an Arabic UI, with localized chrome. | EXISTING | Existing LTR/RTL shell smoke test passes. | PASS (headless) |
| QA-001 | Every requirement above MUST have an automated test that CI runs; ConPTY and macOS MUST have a dated real-machine checklist result. | — | Tests named per requirement; checklist report committed. | GAP (G-07) |
