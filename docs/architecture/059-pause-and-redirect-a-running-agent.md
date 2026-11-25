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

## Across workers
With a cache every worker shares (Redis: `RedisCache.shared_across_processes`), `stop-all`, `pause`,
`resume` and `redirect` also append an event to a per-user list (`aura:agentctl:{user}:v1`, capped at 50,
10-minute TTL -- a redirect's text therefore sits in Redis for at most that long). Each running turn polls
that list once a second and applies events in order, so a turn on another worker stops, holds or takes the
instruction within about a second. Events already in the list when a turn begins are ignored for it, so a
stop pressed earlier never kills a later turn. The routes then report `scope: "all_workers"`; the counts
are still of the answering process's turns only. With an in-memory cache nothing is published and the scope
stays `this_process`. A cache outage never undoes the local effect. Verified with two `ActiveTurns` over
one cache and with two real Redis clients (stop, pause and redirect each crossed).

## Limits (stated, not hidden)
- A single long tool call cannot be paused or redirected until it returns; stop-all is the way to cut one.
- A control event pressed in the instant between a turn starting and its first poll (well under a second)
  is treated as predating it and is not applied to that turn.
- A redirect whose replan fails is dropped (the run continues its old plan); the `redirected` event
  still records that it was received.
- A turn with a single batch and no later pass has no checkpoint left to apply a redirect to.
