# 04 · Deployment topologies

AURA has two supported deploy targets that stay live side by side until a deliberate cutover.
Neither workflow redeploys the other (`docs/deployment/architecture.md` §1, §5).

## 4.1 Control Plane on Railway, Inference Plane on a Hugging Face Space

```mermaid
flowchart TB
    classDef user fill:#1f2937,stroke:#111827,color:#ffffff
    classDef ingress fill:#dbeafe,stroke:#2563eb,color:#0b1b3a
    classDef cp fill:#dcfce7,stroke:#16a34a,color:#052e16
    classDef store fill:#fef3c7,stroke:#d97706,color:#451a03
    classDef inf fill:#ede9fe,stroke:#7c3aed,color:#2e1065
    classDef gated fill:#fee2e2,stroke:#dc2626,color:#450a0a,stroke-dasharray: 5 3
    classDef ci fill:#f3f4f6,stroke:#6b7280,color:#111827

    users(["Clients<br/>Flutter app · console · hostd"]):::user
    dns["DNS + HTTPS edge<br/>no nginx: uvicorn is the edge,<br/>so the header floor and metrics gate live in build_app"]:::ingress

    subgraph rw["Railway · Control Plane (root directory aura_backend/)"]
        direction TB
        gw["<b>gateway</b><br/>FastAPI v3 · AIKernel · ProviderRouter<br/>agents · tools · safety · memory · RAG · RBAC"]:::cp
        wk["<b>Celery worker + beat</b>"]:::cp
        pre["<b>pre-deploy</b><br/>sh /app/scripts/railway-predeploy.sh<br/>runs alembic upgrade head<br/>before the app container starts"]:::cp
        pg[("Managed PostgreSQL")]:::store
        rd[("Redis")]:::store
        qd[("Qdrant")]:::store
    end

    subgraph hf["Hugging Face Space · Inference Plane (GPU)"]
        vllm["<b>vLLM</b> · OpenAI-compatible<br/>/v1/chat/completions · /v1/models<br/>refuses to boot without an API key"]:::inf
    end

    hosted["Hosted open-weight API<br/>ADR-030 slot hosted1<br/>(gated, open-weight IDs only)"]:::gated

    gh["GitHub<br/>main"]:::ci

    users --> dns --> gw
    gw --> pg
    gw --> rd
    gw --> qd
    wk --> pg
    wk --> rd
    wk --> qd
    pre -. "migrates" .-> pg
    gw -- "authenticated HTTPS<br/>openai_compatible provider" --> vllm
    gw -. "hosted slot" .-> hosted

    gh == "auto-deploys every main push<br/>(not gated by CI)" ==> rw
    gh -. "deploy/spaces/inference-vllm/**<br/>uploaded with hf upload" .-> hf
```

- Only chat/completion generation crosses to the inference plane. Embeddings, reranking, STT, TTS,
  and OCR stay in the gateway container.
- Railway's **dashboard** pre-deploy command is what runs; `railway.json` alone changes nothing
  live for this service. Never put `&&`, `||` or `;` in that field.
- The model identity the router assumes (`AURA_PROVIDER_HFSPACE_*`) and what vLLM actually serves
  (`VLLM_MODEL`, `VLLM_MAX_MODEL_LEN`) are necessarily duplicated, so
  `tests/deployment/test_inference_contract.py` pins them against each other.

## 4.2 Single-host stack (Docker Compose, `deploy/docker-compose.prod.yml`)

```mermaid
flowchart TB
    classDef pub fill:#dbeafe,stroke:#2563eb,color:#0b1b3a
    classDef app fill:#dcfce7,stroke:#16a34a,color:#052e16
    classDef state fill:#fef3c7,stroke:#d97706,color:#451a03
    classDef inf fill:#ede9fe,stroke:#7c3aed,color:#2e1065
    classDef obs fill:#f3f4f6,stroke:#6b7280,color:#111827
    classDef jail fill:#fee2e2,stroke:#dc2626,color:#450a0a

    internet(["Internet"]):::pub

    subgraph net_edge["network: edge · 10.77.10.0/24 (ingress-facing)"]
        direction TB
        nginx["<b>nginx</b><br/>the only published port · TLS · HSTS<br/>static 10.77.10.10 · HTTP/1.1 only"]:::pub
        console["<b>console</b><br/>Next.js, served under /console"]:::pub
        gateway["<b>gateway</b><br/>no host port<br/>trusts X-Forwarded-For from nginx only"]:::app
    end

    subgraph net_data["network: data · internal, no route out"]
        direction TB
        workers["<b>worker-realtime</b> · <b>worker-ingestion</b> ·<br/><b>beat</b> · <b>worker-training</b> (training profile)"]:::app
        postgres[("postgres")]:::state
        redis[("redis")]:::state
        qdrant[("qdrant")]:::state
        ollama["<b>ollama</b><br/>CPU · qwen2.5:3b q4_K_M<br/>about 5-10 tokens/s"]:::inf
        vllm["<b>vllm</b><br/>gpu profile"]:::inf
    end

    subgraph net_egress["network: egress · outbound only"]
        pulls["model-weight pulls<br/>ollama · vllm · ingestion"]:::obs
    end

    subgraph net_gate["networks: ollama_gate (internal) + aura_edge (shared with dev stack)"]
        devgate["<b>ollama-dev-gate</b><br/>answers as 'ollama' for the dev stack"]:::inf
    end

    subgraph net_obs["network: observability"]
        direction LR
        prom["prometheus"]:::obs
        graf["grafana"]:::obs
        loki["loki + promtail"]:::obs
        alert["alertmanager"]:::obs
    end

    subgraph net_sbx["sandbox profile · Engineer workspaces (off by default)"]
        direction TB
        broker["<b>sandbox-broker</b><br/>net: sandbox_ctl + sandbox_docker<br/>checks the request body"]:::jail
        proxysock["<b>docker-socket-proxy</b><br/>net: sandbox_docker<br/>EXEC=0 · CONTAINERS only"]:::jail
        ws["workspace containers<br/>one throwaway container per command<br/>net: sandbox_egress (internal)"]:::jail
        sproxy["<b>sandbox-proxy</b><br/>allowlisted egress hosts only"]:::jail
    end

    internet --> nginx
    nginx --> console
    nginx --> gateway
    gateway --> postgres
    gateway --> redis
    gateway --> qdrant
    gateway --> ollama
    gateway -.-> vllm
    workers --> postgres
    workers --> redis
    workers --> qdrant
    ollama --> pulls
    vllm --> pulls
    workers -. "ingestion, training" .-> pulls
    ollama --- devgate

    gateway -- "sandbox_ctl" --> broker
    broker -- "sandbox_docker" --> proxysock
    proxysock -. "creates" .-> ws
    ws --> sproxy
    sproxy --> pulls

    prom -. "scrapes" .-> gateway
    prom --> alert
    prom --> graf
    loki --> graf
```

Isolation rules worth keeping in view:

- **Prod and dev compose files stay at parity** on network `internal:` flags, socket-proxy env
  allowlists, and which images are built rather than pulled. They share one host, so hardening one
  stack and not the other leaves the weaker one as the attack surface
  (`tests/unit/test_compose_security_parity.py`).
- **The gateway cannot reach the Docker socket.** The socket proxy filters endpoints, not request
  bodies, so a broker sits between them and checks the body. The gateway never `docker exec`s: the
  proxy refuses it, and each sandbox command is its own container over the workspace's bind mount
  (`tests/unit/test_sandbox_exec_surface.py`).
- Postgres budget: every uvicorn worker and Celery child holds connection pools sized so the worst
  case (108) stays under `max_connections = 120` (`tests/unit/test_postgres_connection_budget.py`).
