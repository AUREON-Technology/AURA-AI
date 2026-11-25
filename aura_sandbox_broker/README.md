# Sandbox broker

A small private service between AURA's backend and Railway Sandboxes. It holds the Railway token
and fixes how a sandbox is made. Plan: `docs/plans/railway-sandboxes-plan.md`. Proof of the
network fence: `docs/reports/railway-sandbox-spike-2026-11-25.md`.

**Do not deploy: the outbound allowlist can be bypassed** through content-delivery networks that
serve the allowed hosts (confirmed on a real sandbox; see the end of
`docs/reports/railway-sandbox-spike-2026-11-25.md`).

**Status: built; tested against an in-memory vendor, with the guest scripts run on real sandboxes
through the CLI. Not deployed, and not proven against the official SDK.** Nothing calls it yet.

## What it decides (callers cannot)

| Setting | Value |
|---|---|
| Network | `ISOLATED`: no private network, no public domain |
| Variables | none passed into a sandbox |
| Size, region | 2 vCPU, 2 GB, `us-west2` |
| Idle timeout | 120 minutes (a crash safety net) |
| Outbound | the seven hosts of `deploy/sandbox/filter`, through a proxy, behind `nftables` |
| Commands | unprivileged user, own cgroup, deadline enforced inside the sandbox; no user namespaces, `AF_VSOCK` or `io_uring` |
| Files | moved as the unprivileged user; paths must stay in the workspace |

The fence is raised from the broker's own copy of `guest/fence.sh` after **every** boot, then
proven. Only then does it write a ready mark naming that boot, which `guest/run.sh` and
`guest/file.sh` require. If it fails, the sandbox is destroyed.

## API

Every route but `GET /health` needs `Authorization: Bearer <AURA_BROKER_TOKEN>`. Errors are
`{"error": "<code>"}`.

| Route | Body, result |
|---|---|
| `GET /v1/sandboxes` | `{"sandboxes": [{"id", "status", "createdAt"}]}` |
| `POST /v1/sandboxes` | `{}` or `{"checkpoint": "ws-<32 hex>"}`, `201 {"id"}` |
| `DELETE /v1/sandboxes/{id}` | `204`, also when already gone |
| `POST /v1/sandboxes/{id}/exec` | `{"command": ["..."], "timeout_s": 1-900}`, NDJSON |
| `POST /v1/sandboxes/{id}/exec/{command_id}/cancel` | `204` |
| `PUT /v1/sandboxes/{id}/files?path=` | raw bytes, `204` (100 MiB limit) |
| `GET /v1/sandboxes/{id}/files?path=` | raw bytes |
| `GET /v1/sandboxes/{id}/usage` | `{"bytes"}` of the workspace |
| `POST /v1/sandboxes/{id}/checkpoint` | `{"name": "ws-<32 hex>"}`, `204` |
| `GET /v1/checkpoints` | `{"checkpoints": [{"name", "createdAt"}]}` |
| `DELETE /v1/checkpoints/{name}` | `204`, also when already gone |

Exec lines, in order: `{"type":"started","command_id"}`, then `stdout` and `stderr` with `data`,
then either `{"type":"exit","exit_code","timed_out","truncated"}` or `{"type":"error","error"}`
(`fence_missing`, `sandbox_unsafe`, `exit_unknown`, `sandbox_not_found`, `provider_error`). A
stream with neither last line was cut off: treat the command's outcome as unknown. Closing the
connection cancels the command. After `exit_unknown` the broker has tried to end the command and
destroyed the sandbox if it could not.

Error codes: `unauthorized`, `invalid_request`, `not_found`, `sandbox_not_found`,
`sandbox_busy` (a checkpoint is in progress), `checkpoint_not_found`, `file_not_found`,
`path_refused`, `file_too_large`, `fence_failed`, `cancel_failed`, `provider_error`.

## Configuration

| Variable | Meaning |
|---|---|
| `AURA_BROKER_TOKEN` | Shared secret, at least 32 characters. The backend gets the same value. |
| `AURA_SANDBOX_RAILWAY_TOKEN` | A **project token** for the sandbox project only. |
| `AURA_SANDBOX_ENVIRONMENT_ID` | The sandbox project's environment id. |
| `PORT` | Default `8080`. |

The service refuses to start if the sandbox environment is the one it runs in. Do not give it
any other credential.

## Run and test

```
npm ci --ignore-scripts
npm test
```

## Deploy (not done yet)

1. In the production Railway project, add a service from this repository with root directory
   `aura_sandbox_broker`. Give it **no public domain**; the backend reaches it over private
   networking.
2. Set the three variables above in Railway's dashboard.
3. Never add `AURA_SANDBOX_RAILWAY_TOKEN` to the backend service.

## Known limits

- **Open and blocking:** the proxy checks the host named when a tunnel opens, not the name inside
  TLS, so other sites on an allowed host's network are reachable.
- One instance is assumed: the "busy while checkpointing" rule is per instance, and nothing here
  limits how many sandboxes exist. The backend enforces per-person and platform limits.
- A sandbox whose creation fails inside the vendor SDK can be left running until its idle timeout;
  the backend must reconcile the list.
- `GET /v1/sandboxes` returns what the SDK's `list` returns; behaviour past one page is unverified.
- An allowed host can still receive data from a command that holds someone else's credentials.
