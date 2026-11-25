# Railway Sandboxes: Phase 0 proof, 2026-11-25

> **S2 below was disproved later the same day** (the allowlist could be bypassed through
> content-delivery networks), then met again after the proxy was rebuilt: see
> [Broker proof](#broker-proof) at the end, whose fourth run is the current result.

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

## Broker proof

`broker_guest.py` runs the broker's own guest scripts (`aura_sandbox_broker/guest`) on real
sandboxes, through the CLI. Two runs; the second followed an independent security review of the
broker and its fixes. All sandboxes (18 across the day) were destroyed and no checkpoint remains.

| Run | Result |
|---|---|
| First (before the review) | 48 passed, 1 failed: the test read a process limit with an option `dash` lacks |
| Second (after the fixes) | 62 passed, 4 failed |

The second run's passes include the review's findings on a real kernel: nothing runs without a
ready mark from this boot; a fence that fails its proof leaves the sandbox unusable; a privileged
TCP listener fails the fence; startup files and `.curlrc` left by the agent take no part; a cancel
issued before the start holds; only the standard streams are inherited; user namespaces,
`AF_VSOCK` and `io_uring` are refused while ordinary sockets and processes work. The kernel has no
Yama `ptrace_scope`.

The four failures:

- **Allowlist bypass (2, confirmed).** With a connection tunnelled to `registry.npmjs.org`, a
  command received HTTP 200 for `www.cloudflare.com`; through `objects.githubusercontent.com` it
  received 200 for `pages.github.com`. The proxy checks only the host named when the tunnel opens,
  not the name inside TLS. Anyone with a site on those networks could receive data from a command.
  The same attempt through the two PyPI hosts failed certificate validation. The Docker sandbox's
  proxy has the same design (not tested there). **Not fixed.** Whether requiring the TLS name to
  match is enough, or the networks also honour a different HTTP `Host` header, is untested.
- **Cancel check (1, unconfirmed).** The final process listing came back empty rather than "none";
  the command's group was gone. The test now repeats an empty answer. Not yet rerun.
- **Replaced workspace (1, test fault).** An earlier check left a mode-000 folder, so the setup's
  `rm` failed and the workspace was never replaced. The test now asserts the replacement. Not yet
  rerun.

### Third run: the TLS-name-checking proxy

`tinyproxy` was replaced by `guest/proxy.py`, which requires the TLS server name to be the
tunnel's host and refuses encrypted ClientHellos. Result: **77 passed, 3 failed**, and one of the
passes below is void. All sandboxes and the checkpoint were destroyed.

- `git ls-remote` and clone, a `pip` wheel download, a registry document over 1 MB, thirty
  concurrent requests and a TLS 1.2 request all worked through the proxy.
- The two earlier open items passed: cancel by id, and a replaced workspace (with its control).
- A different **TLS name** was refused through `pypi.org`, `files.pythonhosted.org` and
  `registry.npmjs.org`. The fourth case (`objects.githubusercontent.com`) printed a pass, but the
  CLI could not connect, so it is **not verified** live; the fence's own proof and the unit tests
  cover the rule.
- **Still open (3 failures).** With the TLS name correct and only the HTTP `Host` header changed:

| Tunnel and TLS name | Host header | Answer |
|---|---|---|
| `pypi.org` | `www.fastly.com` | **200, Fastly's page** |
| `files.pythonhosted.org` | `www.fastly.com` | **200, Fastly's page** |
| `objects.githubusercontent.com` | `pages.github.com` | **200, GitHub Pages' page** |
| `registry.npmjs.org` | `www.cloudflare.com` | 403 |
| `github.com` | `pages.github.com` | 301 back to `github.com` |
| `codeload.github.com`, `api.github.com` | `pages.github.com` | 400 |

  Three of the seven allowed hosts therefore still lead elsewhere on the same network. A proxy
  that does not decrypt cannot see the `Host` header. Whether an attacker's own site on that
  network would receive a request this way was inferred from these two third-party sites, not
  tested with a site we control.

### Fourth run: TLS inspection for three hosts

The proxy now terminates TLS for `pypi.org`, `files.pythonhosted.org` and
`objects.githubusercontent.com` and forwards only a body-less `GET` or `HEAD` whose `Host` names
that host, rewritten from its parts. Certificates are issued in the sandbox at each boot by an
authority limited to those names, whose key is destroyed. The same change fixes a gap reported by
an automated review of the third run's commit: a second ClientHello claiming an unusual version
was not checked (reproduced in tests first).

Result: **87 passed, 0 failed.** All sandboxes and the checkpoint were destroyed.

- A different TLS name was refused through all four hosts tried, including the one that had not
  connected before.
- A different `Host` header: **421** from the proxy for the three inspected hosts; 403 from the npm
  registry's network; 301 back to `github.com`; 400 from `codeload` and `api.github.com`.
- Through the inspected hosts, `pip` installed into a virtual environment, Python's own HTTPS
  worked and the release-download host answered. A `POST` was refused, the agent could not read the
  proxy's key, and no authority key was found on disk.
- Through the other hosts, `git`, a large registry document, thirty concurrent requests and TLS 1.2
  worked.
- A GitHub release download failed: it redirects to `release-assets.githubusercontent.com`, which
  is not allowed. The Docker sandbox's list does not contain it either.

Not covered: `npm install` and `uv` (not installed in the proof sandbox), the official SDK, and an
independent review of the inspection code.

**After this run**, an automated review of its commit found that, once the client had sent a cipher
change, a ClientHello declaring 131,072 to 131,396 bytes was passed on unchecked (on the four
hosts that are not inspected). Reproduced in tests and fixed. The fix has local tests only; it was
not rerun on a real sandbox. Whether any of those networks would accept so large a greeting was
not tested.
