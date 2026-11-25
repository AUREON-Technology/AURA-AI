# Railway Sandboxes: Phase 0 proof, 2026-11-25

Plan: [`docs/plans/railway-sandboxes-plan.md`](../plans/railway-sandboxes-plan.md). Scripts:
`deploy/scripts/railway-sandbox-spike/` (`run.py` drives the Railway CLI, `guest.sh` runs inside
the sandbox).

## Result

**The gate (S1–S3) passes** with the fence rebuilt on `nftables`. S4 passes. S5 fails on two raw
CLI behaviours and passes with workarounds. S6 and S7 are **not measured**.

| # | Check | Verdict |
|---|---|---|
| S1 | Commands run unprivileged and cannot change the fence | **Pass** (14 checks, after one fix below) |
| S2 | Outbound reaches the seven allowed hosts only | **Pass** (20 checks, including IPv6, DNS and raw sockets) |
| S3 | Nothing private reachable, no platform credential inside | **Pass**, with the limit noted below |
| S4 | Checkpoint of 1 GiB within 60 s, boot within 30 s | **Pass**: 33.2 s and 7.1 s; file identical |
| S5 | 100 MiB both ways; streaming, timeout, cancel | **Raw CLI fails timeout and download; workarounds pass** |
| S6 | API calls fit 10,000 an hour | **Not measured** |
| S7 | Checkpoint cost | **Not measured** |

## Environment

- Project `aura-sandboxes` (`a555a4dc-4cfc-4ab8-9a95-aba71b92849d`), created for this proof. It holds
  no services. The account's current plan; Pro was not needed.
- Railway CLI 5.23.0 (5.75.0 is available). The CLI warns that sandboxes are experimental.
- Guest: Ubuntu 26.04.1, kernel `6.18.60-railway`, region `us-west2`. A default sandbox reported
  4 CPUs and 2 GB, so the broker must set the size explicitly.
- 14 sandboxes created, 14 destroyed. The one checkpoint was deleted.
- Create to `RUNNING`: 5.3–6.4 s. One command round trip from a home connection: 1.5 s.
  Installing and starting the fence on a plain image: 17.8 s (a template will pre-install the
  packages).

## Findings that change the build

1. **`iptables` cannot do this.** The kernel lacks the `owner` and `REJECT` extensions, so rules
   fail to load. Native `nftables` with `meta skuid` works. The working rule set is in `guest.sh`.
2. **The image's `python3` and `node` live under `/root`**, which the agent user cannot read. The
   template must install system packages.
3. **`/var/lib/pebble/default` ships world-writable and root-owned.** The first run failed S1 on
   it. Setup now removes the write bit; the reruns passed. The folder holds only `layers/`, and no
   `pebble` process was running when inspected.
4. **A restored checkpoint has no fence and no proxy.** The broker must apply both after every
   boot, before any agent command, and destroy the sandbox if that fails.
5. **The process layout differs between boots** (process 1 was `sleep infinity` in some sandboxes
   and `/rwinit/init` in others). The bootstrap must not depend on it.
6. **The CLI's `--timeout` does not stop the command.** It exits 124 while the process keeps
   running, and killing the client leaves it running too. Working: `timeout --kill-after` inside
   the sandbox, and `pkill -KILL -u <agent uid>` to cancel.
7. **One command's output is cut at about 7 MiB with no error** (7.4–7.5 MB of 100 MiB arrived).
   Working: read in 4 MiB pieces. 100 MiB took 90 s down and 95–101 s up from a home connection;
   speed from inside Railway was not measured.
8. Variables inside a sandbox are identifiers only (`RAILWAY_SANDBOX_ID`, project, environment and
   VM IDs; some boots add `RAILWAY_VMINIT_GUARD_*`). No token, secret or database variable name
   was found in any process, and no file in `/etc`, `/run`, `/root` or `/var/lib` mentioned a
   Railway token.

## What was and was not shown for S3

- Two isolated sandboxes could not reach each other on either private address (IPv4 `10.250/16`,
  IPv6 `fd12::/16`). Controls showed the server answering itself on both addresses.
- No metadata endpoint answered, and AURA's `*.railway.internal` names did not resolve.
- **Limit:** the sandbox project holds no services, so there was no private service to try to
  reach. The protection rests on the separate project, as designed. `--private-network` was never
  used, so its behaviour is untested.

## Not covered

- **The official SDK.** Every check used CLI 5.23.0. The broker will use the TypeScript SDK, whose
  timeout, cancel, file transfer and API-call use must be proven with a project token. Findings 6
  and 7 are CLI observations; the in-sandbox workarounds do not depend on either client.
- **S6.** The CLI does not expose its API calls. From the SDK source, a command needs a shell token
  as well as its connection; whether the SDK reuses tokens is unverified.
- **S7.** Check the project's usage page after billing updates.
- Disk quota enforcement, behaviour at the sandbox cap, and checkpoint time for many small files
  (the 1 GiB test was one incompressible file).
- Allowed hosts can still receive data: a command holding someone else's credentials could push to
  an allowed host such as GitHub. Today's Docker allowlist has the same property.
