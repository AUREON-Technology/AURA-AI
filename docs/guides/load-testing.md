# Load testing: the concurrent-user matrix

How to measure how many concurrent users a deployment actually serves, with the harness in
`aura_backend/scripts/loadtest/` (roadmap Phase 3). How to run the 1→100 user matrix against
staging, the VPS or a GPU host, how to read the report it writes, and the rule for when a
capacity number counts.

## The rule

**Capacity is accepted only after this harness has measured it on the target hardware, with the
target model, and the report records the run conditions next to the numbers.** A number from any
other host, model, provider setting or rate-limit configuration doesn't carry over. That covers
every echo-provider or in-process run, including the harness's own self-validation. A report with
`UNSPECIFIED` hardware or an unrecorded target SHA is not evidence. The roadmap's standing rule
applies here too: "built" and "tested" are not "proven".

The harness is a measuring tool. It never asserts a capacity number: the `-m load` wrapper
passes whenever the run completes and the target answered. The PASS/FAIL results for the launch
thresholds live in the report, next to the hardware that produced them.

## What it measures

Each scenario sends the gateway's own v3 requests, with the same routes, payloads and SSE
frames the Flutter client uses:

| Scenario | Requests per iteration | Notes |
| --- | --- | --- |
| `chat` (A) | `POST /api/v3/chat/stream` | Short question with retrieval over the identity's own documents. **The launch TTFT and response thresholds are evaluated on this scenario.** |
| `long_context` (B) | `POST /api/v3/chat/stream` | A pasted transcript of about 12k characters (`--long-context-chars`) plus retrieval over a ~20 KB corpus, in a session whose episodic history keeps growing. |
| `agent` (C) | `POST /api/v3/chat/stream` with `plan: true` | A search + calculator request, so the turn runs plan → tool → observe → respond. `agent.step_frames` counts the `step` frames that arrived. |
| `engineer` (D) | stream, then up to 3 confirm rounds | Engineer persona: open a workspace, read `README.md`, write a file, run the tests. Runs only when `--engineer-repo` is set, the identity's role can use the persona, and the target has workspaces enabled. Otherwise it's skipped and the report gives the exact reason. It auto-confirms only workspace-local tools (`open_workspace`, `clone_repository`, `write_file`, `edit_file`, `run_command`, `git_status`, `git_diff`). It never confirms `git_push`, `git_commit`, `destroy_workspace` or any `github_*` write, and it counts every call it declined. |
| `voice` (E) | `POST /voice/transcribe` → `POST /chat/stream` → `POST /voice/speak` | The three voice-note calls. Skipped unless `/health/ready` reports both an STT and a TTS engine. Uses a synthesized 2 s WAV by default, which isn't intelligible speech: a short transcript falls back to a fixed question and is counted. **Pass `--voice-wav` with a real 16 kHz mono recording for representative STT cost.** The `/voice/live` WebSocket isn't driven by this harness. |
| `api` | one of `GET /knowledge/documents`, `POST /knowledge/search`, `GET /memory`, `GET /agents` | Non-LLM API traffic. **The API p95/p99 thresholds are evaluated on this population.** |
| `mixed` (F) | weighted draw from the above | Default weights are `chat=50, long_context=10, agent=15, voice=5, api=20, engineer=0`. Change them with `--mix`. A scenario that isn't available is dropped from the mix and listed in the report. It isn't silently redistributed. |

For each scenario and each concurrency step, the report gives:

- RPS and requests/min
- goodput (successful iterations per second)
- TTFT p50/p95/p99 (time to the first `token` frame)
- E2E p50/p95/p99 (the whole iteration, retries included)
- tokens/sec, from `done.usage.completion_tokens` over the span after the first token, when the stream reports it
- the error rate, broken down into `4xx`, `5xx`, `overload_429`, `unavailable_503`, `timeout`, `connection` (which includes a stream that ended without a `done` frame) and `blocked` (a safety-guard `error` frame)
- retries

It also scrapes `/metrics` before, during and after each step (see
[Server-side metrics](#server-side-metrics)).

Every request becomes a sample, whatever happened to it. A timeout, a refused connection, an
nginx 503 and a mid-stream `error` frame are all counted. A harness that dropped its failures
would report the survivors' latency as if it were the system's.

## Where to point it

Two network paths are worth measuring, and they measure different things:

1. **Direct to the gateway, inside the Docker network** (`http://gateway:8000`). This measures
   the application and the model, and it's the one to use for capacity. It's also the only path
   where `/metrics` is reachable, because nginx denies it on the public edge.
2. **Through nginx** (`https://<domain>`). This measures the edge as well, and from one load
   host you mostly measure its per-IP limits: `limit_conn aura_conn 24`, `aura_chat` at
   2 requests/s with a burst of 5, and `aura_general` at 30 requests/s
   (`deploy/nginx/templates/aura.conf.template`). Past 24 concurrent connections from one IP,
   nginx answers 503 with an HTML body. The report labels these `non-JSON body (edge proxy
   limit_req/limit_conn?)`. Use this path to check the edge on purpose, or run it from several
   load hosts.

**Application rate limits apply on both paths.** They're 60 cost units/min per user and 120 per
IP. A chat turn costs 10 units, so one identity gets 6 turns a minute and one IP gets 12
(`aura_core/security/rate_limit.py`). A matrix from one host with production limits mostly
measures the limiter. The report flags any step where more than 5% of requests got a 429.

For a capacity run, raise `AURA_RATE_LIMIT_USER_PER_MIN` and `AURA_RATE_LIMIT_IP_PER_MIN` on the
target for the test window only. Settings validation requires the limiter to stay enabled in
staging and production, so raise the limits rather than disabling it. Record the values with
`--condition` and put them back afterwards.

## Identities

Every virtual user needs its own identity. Otherwise the per-user rate limit, per-user
live-voice cap and per-user state all collapse into one user. The harness never stores a secret
or a token in the repository or in the results file. There are three modes:

- **`--auth mint`** (use this for a full matrix). The operator already holds the target's JWT
  signing secret (`deploy/secrets/jwt_secret`). The harness reads it from an environment
  variable (`--jwt-secret-env`, default `AURA_LOADTEST_JWT_SECRET`) and mints one HS256 access
  token per virtual user, for synthetic users `loadtest-vu-001` … `loadtest-vu-100`. The claim
  shape is exactly what `services/jwt_auth.create_access_token` issues, and a default-suite test
  pins that against the real verifier. Tokens last an hour and are re-minted before they expire.
  They carry the production gateway's `iss`/`aud` (`aura-production` / `aura-api-production`);
  for another target, set `AURA_LOADTEST_JWT_ISSUER` and `AURA_LOADTEST_JWT_AUDIENCE` to the
  values its gateway derives from its own `AURA_ENVIRONMENT` (or `AURA_JWT_ISSUER`/`_AUDIENCE`).
  The default role is `User`. `--mint-role Developer` is needed for the Engineer scenario, and
  only works if `Developer` is in the target's `AURA_ENGINEER_AGENT_ROLES`. Admin roles are
  refused. Minting grants nothing that holding the secret doesn't already grant.
- **`--auth tokens --tokens-file FILE`**: one access token per line, obtained through the real
  login flow (`/api/v3/auth/token` or `/web/token`) and assigned to virtual users round-robin.
  Access tokens live 15 minutes, so this mode only suits short runs.
- **`--auth none`**: only for a gateway running mock auth, which is local only (settings
  validation refuses it in staging and production). Every virtual user becomes `mock-user`, and
  the report says so.

Before any load, the harness does two things that aren't measured:

- **Preflight**: `GET /health/ready`, `GET /api/v3/agents`, `GET /api/v3/engineering/workspaces`,
  and a 16-token `POST /api/v3/chat` probe that records the provider and model actually serving.
  If `/agents` is rejected, the run aborts before any load (exit code 2).
- **Setup**: it ingests two documents per identity (`loadtest-handbook` and `loadtest-corpus`),
  because knowledge is owner-scoped. The document ids are fixed, so re-runs don't grow the
  corpus. Skip this with `--no-setup`.

## Running it

All commands run from `aura_backend/`. The load host needs only `httpx` and `PyJWT`, and both
are already in `requirements.lock`.

### On the VPS or a GPU host (inside the gateway's network)

Run the harness from a **separate, throwaway container** built from the gateway image. The image
copies all of `aura_backend/`, so it contains the harness from whatever commit it was built from.
Give the container no volumes and override the entrypoint:

```bash
cd /opt/aura-stack
NET=$(docker inspect "$(docker compose -p aura ps -q gateway)" \
      --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' | tr ' ' '\n' | grep edge | head -1)
IMAGE=$(docker inspect "$(docker compose -p aura ps -q gateway)" --format '{{.Config.Image}}')
export AURA_LOADTEST_JWT_SECRET="$(cat deploy/secrets/jwt_secret)"

docker run --rm --network "$NET" \
  -e AURA_LOADTEST_JWT_SECRET \
  -v "$PWD/docs/reports/loadtest:/out" \
  --entrypoint python "$IMAGE" -m scripts.loadtest run \
    --base-url http://gateway:8000 \
    --metrics-url http://gateway:8000/metrics \
    --auth mint \
    --scenarios chat,mixed \
    --matrix 1,2,5,10,20,30,40,50,75,100 \
    --ramp 10 --duration 60 --think 1-3 --cooldown 10 \
    --label gpu-l4-qwen7b \
    --target-sha "$(git rev-parse HEAD)" \
    --target-hardware "1x NVIDIA L4 24GB, 8 vCPU, 32 GB RAM" \
    --model "vLLM Qwen2.5-7B-Instruct-AWQ" \
    --condition AURA_RATE_LIMIT_USER_PER_MIN=100000 \
    --condition AURA_RATE_LIMIT_IP_PER_MIN=100000 \
    --condition AURA_WORKERS=4 \
    --out-dir /out
```

Two things to avoid:

- **Don't use `docker compose run gateway …` or `docker exec` into the running gateway.**
  `compose run` mounts the shared `prometheus_multiproc` volume, and the image's entrypoint
  deletes every `*.db` file in it on start. That wipes the live gateway's metrics mid-test.
  `docker exec` puts the load generator on the gateway's own CPUs.
- **Watch CPU contention.** A load generator on the same host as the gateway competes with it
  for CPU. That's acceptable for a GPU-bound model, but record it
  (`--condition load_generator=same-host`). For a CPU-bound deployment, run from a second
  machine on the same private network.

Before the run, check `docker compose ls` and make sure you're pointing at the stack you mean.
The VPS runs `aura` (prod) and `aura-dev` side by side.

### Against staging over HTTPS

```bash
python -m scripts.loadtest run --base-url https://staging.example \
  --auth mint --scenarios mixed --matrix 1,2,5,10,20 \
  --target-sha <sha> --target-hardware "<describe>" --out-dir ../docs/reports/loadtest
```

Add `--ca-bundle` for a private CA. Without `--metrics-url`, the bounded-queue threshold is
`NOT EVALUATED`.

### As a pytest run

`tests/load/test_capacity_matrix.py` takes the same flags from one environment variable:

```bash
AURA_LOADTEST_ARGS="--base-url http://gateway:8000 --auth mint --scenarios mixed \
  --target-hardware '...' --target-sha $(git rev-parse HEAD) --out-dir ../docs/reports/loadtest" \
  pytest tests/load/test_capacity_matrix.py -m load -s
```

When `AURA_LOADTEST_ARGS` is unset, the test skips cleanly. It's excluded from the default run by
the `load` marker, like every test under `tests/load/`.

### Quick checks and the harness's own self-test

```bash
# The real app on the in-memory container + echo provider. Validates the harness only.
python -m scripts.loadtest run --in-process --matrix 1,3 --iterations 2 --think 0 --ramp 0 \
  --scenarios chat,agent,api,mixed --out-dir /tmp/lt

# Re-render a report from its JSON (e.g. after a renderer change).
python -m scripts.loadtest render /tmp/lt/loadtest-<stamp>.json
```

The default `pytest` suite runs the self-test in `tests/unit/test_loadtest_harness.py` and
`tests/integration/test_loadtest_smoke.py`. Together they take a few seconds and need no network.

### Useful flags

- `--iterations N`: a fixed number of iterations per virtual user instead of a timed window.
- `--retries N`: retry 429, 502, 503, 504 and connection errors, honouring `Retry-After` and
  capped by `--retry-cap-s`. The default is 0, so the report shows what a client without retry
  logic would see. A stream is only retried if it failed before any output arrived.
- `--max-output-tokens`: default 256. Size it the way CLAUDE.md says: a worst-case generation
  has to finish inside the gateway's timeout at the model's real throughput.
- `--timeout`: default 300 s, which matches nginx's `proxy_read_timeout` on `/api/v3/chat/stream`.
- `--no-raw-samples`: leave per-request rows out of the JSON. They're kept by default because
  they're the evidence.
- Threshold overrides: `--api-p95-ms`, `--api-p99-ms`, `--ttft-p95-ms`, `--response-p95-ms`,
  `--max-5xx-rate` and `--max-queue-depth`.

## Reading the report

Every run writes `<label>-<UTC stamp>.json` and a Markdown report rendered from it, in the same
shape as `docs/reports/phase-a4-load-test.md`. The report is rendered *only* from the JSON, so it
can't show a number the JSON doesn't have.

### Status

Warnings come first: an echo or in-process run, 429-dominated steps, too few identities, no
metrics, synthetic voice audio. Then there's one line per scenario giving the highest user count
at which every evaluated threshold passed at that step and every step below it, plus the
saturation knee.

### Run conditions

The date, target and harness SHAs, the redacted gateway URL, hardware, model (declared and
probed), environment, identities, load-generator host, matrix and step shape, token and timeout
settings, and every `--condition`. **A number without this table isn't a result.**

### Measured numbers

One table per scenario, one row per concurrency step. Only requests *started* inside the steady
window count, so ramp-up and tail-drain are excluded. The error-class table and the top error
details, such as `chat_stream: unavailable_503 (all_providers_failed) x12`, tell you *what*
failed. For `mixed`, the per-request-type table splits the latency by route.

### Launch thresholds

Each threshold is evaluated per step and gets one of four verdicts:

- `PASS` or `FAIL`
- `NO DATA`: the step had no samples of that population, e.g. an API threshold in a chat-only run.
- `NOT EVALUATED`: the signal wasn't available.

| Threshold | Target | Population |
| --- | --- | --- |
| Non-LLM API p95 / p99 | < 500 ms / < 1 s | Successful `api` requests. Speech is inference and isn't included. |
| TTFT p95 | < 2.5 s | Scenario A (`chat`) streams only. |
| Ordinary response p95 | < 15 s | Successful scenario A iterations only. |
| 5xx rate | < 1% | All requests. Counts 5xx, 503, client timeouts, dropped connections and mid-stream server errors. **Doesn't count 429s**, which are the limiter doing its job. |
| Bounded queue | not growing at end of step | Server-side `aura_llm_queue_depth`. Fails if the last third of the step's samples averages more than max(1, 50% of the first third's average) above the first third, or exceeds `--max-queue-depth`. |

Agent and long-context turns plan or prefill before their first token, so they're reported but
not held to the interactive-chat budget.

### Saturation

The knee is the last step before goodput stopped growing by at least 10% while E2E p95 rose by
at least 10%. Past the knee, extra users only queue. With closed-loop users, a saturated system
shows flat throughput and rising latency rather than an unbounded backlog, so read capacity from
the knee and the threshold table together. "No knee found" means the matrix didn't go high
enough.

### Server-side metrics

When `--metrics-url` is reachable, each step records:

- queue depth, turns in flight and LLM in-flight, sampled every `--metrics-interval` seconds
- server-side mean TTFT and turn duration
- LLM calls by outcome, rate-limit rejections, fallbacks and circuit opens

The report also shows whether each signal Phase 3 asks for is actually exported. As of this
harness:

- **`aura_llm_queue_depth` is declared but never set** anywhere in `aura_core`, so the
  bounded-queue threshold reads `NOT EVALUATED` until the router maintains it.
- **`aura_turns_in_flight` is only maintained by `AIKernel.run`** (`POST /chat`), not by
  `AIKernel.stream`, so it reads ~0 under the streamed load every scenario generates. Read
  `aura_llm_inflight` instead.
- **Neither a database-pool metric nor a Celery queue-depth metric is exported.**

The report says all of this per run rather than printing zeros.

## Recording a result

1. Run the full matrix on the target. Use `mixed` for capacity, plus `chat` on its own for a
   clean TTFT curve.
2. Commit the JSON and Markdown under `docs/reports/`, named for the hardware and model.
3. Cite that report, and only that report, wherever a capacity number is claimed.
4. A new model, GPU, provider concurrency setting (`OLLAMA_NUM_PARALLEL`, vLLM batch settings),
   worker count or rate-limit configuration means a new run. The old numbers don't carry over.
