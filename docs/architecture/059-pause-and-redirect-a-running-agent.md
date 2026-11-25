# ADR-059: pause, resume and redirect a running agent

Status: Proposed (built, tested locally; not run against a live gateway or a device)

## Problem
The only control a person had over a running agent was `stop-all` (ADR-041), which cancels. There
was no way to hold the agent while looking at what it was doing, or to tell it "not that, this".

## Decision
Cooperative control, honoured at points where nothing is half-applied:

- `ActiveTurns` (the registry `stop-all` already uses) gains `pause_user`, `resume_user`,
  `redirect_user`. A turn holds `paused`, a `resumed` event and a bounded queue of instructions
  (5 waiting, 1000 characters each, whitespace collapsed).
- `ExecutionEngine.execute/stream` take an optional `hold` callable, awaited **before each batch is
  dispatched, never during one**. It waits while the turn is paused and returns true when an
  instruction is waiting; the pass then ends with `blocked_reason="redirected"`.
- `AgenticExecutor` (the loop) keeps the work already done, then, at the checkpoint at the top of the
  next pass, drains the queue and replans with the person's words as feedback
  (`replan(reason="user_redirect")`). It emits `redirected` / `pause_timeout` loop events. At most five
  mid-pass redirects per run; beyond that a redirected pass is treated like any other.
- A pause nobody answers ends after 15 minutes (`MAX_PAUSE_S`), says so, and the run carries on, so a
  forgotten phone cannot hold a turn, its connection and its sandbox forever.
- Routes `POST /api/v3/agents/{pause,resume,redirect}`; owner-scoped, service keys refused, audited
  (a redirect is audited by length, its text is not stored). The Flutter run-history page has the three
  buttons next to stop-all.

## Limits (stated, not hidden)
- **Process-local**, exactly like stop-all: a turn held by another gateway worker is not reached
  (`scope: "this_process"`).
- A single long tool call cannot be paused or redirected until it returns; stop-all is the way to cut one.
- A redirect whose replan fails is dropped (the run continues its old plan); the `redirected` event
  still records that it was received.
- A turn with a single batch and no later pass has no checkpoint left to apply a redirect to.
