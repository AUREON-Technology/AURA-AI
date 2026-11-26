# Interview and decisions: security triage 2026-11-26

| ID | Question | Answer | Source | Impact |
|---|---|---|---|---|
| D-01 | Which slice of "Security" to assess? | Whole-platform sweep at triage depth (option 4). The agent had recommended backend-only. | USER | Breadth over depth; the register lists unread areas as follow-ups. |
| D-02 | Which release gate applies? | **Private beta** with known users (option 2, the agent's recommendation). | USER | The three P1 items block the beta. An external pen test becomes a follow-up rather than a blocker. Accepted-risk items need an owner and review date. |
| D-03 | Push the reports to the branch and open a PR. | Approved ("Go ahead with it"). | USER | Draft PR #326. |

## Open decisions

- **OD-01:** Named owner and review date for accepted risks SEC-T-07 (local daemon trust) and SEC-T-08 (root containers). Not supplied; the plan does not invent them.
- **OD-02:** Whether the beta cohort will include customer data classed as sensitive. This changes how hard SEC-T-03, SEC-T-06 and the unverified Qdrant isolation probe (CB-40) block.
- **OD-03:** Pen-test timing: before beta expansion or before public launch.
