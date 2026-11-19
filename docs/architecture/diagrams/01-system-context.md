# 01 · System context

Who and what AURA talks to. One backend (the **Control Plane**), three clients, and a set of
inference hosts behind a single provider router.

```mermaid
flowchart TB
    classDef person fill:#1f2937,stroke:#111827,color:#ffffff
    classDef client fill:#dbeafe,stroke:#2563eb,color:#0b1b3a
    classDef core fill:#dcfce7,stroke:#16a34a,color:#052e16
    classDef store fill:#fef3c7,stroke:#d97706,color:#451a03
    classDef infer fill:#ede9fe,stroke:#7c3aed,color:#2e1065
    classDef ext fill:#f3f4f6,stroke:#6b7280,color:#111827
    classDef gated fill:#fee2e2,stroke:#dc2626,color:#450a0a,stroke-dasharray: 5 3

    user(["End user"]):::person
    operator(["Operator / Super Admin"]):::person

    subgraph clients["Clients"]
        direction LR
        app["<b>aura_client</b><br/>Flutter: Android, iOS, macOS,<br/>web, desktop<br/>Riverpod · go_router · Drift/SQLite"]:::client
        console["<b>aura_admin</b><br/>Next.js operator console<br/>11 pages"]:::client
        hostd["<b>aura_hostd</b><br/>pure-Dart local daemon<br/>acts on the user's own<br/>files and terminal"]:::client
    end

    subgraph cp["AURA Control Plane · aura_backend (Python / FastAPI)"]
        direction TB
        gateway["<b>v3 HTTP gateway</b><br/>/api/v3/* · authn · quotas ·<br/>cost-weighted rate limits"]:::core
        kernel["<b>AIKernel</b><br/>safety · memory · retrieval ·<br/>planning · tools · generation"]:::core
        router["<b>ProviderRouter</b><br/>5 routing policies ·<br/>circuit breakers · pre-token failover"]:::core
        workers["<b>Celery workers + beat</b><br/>ingestion · realtime ·<br/>nightly knowledge refresh"]:::core
        inproc["<b>In-process engines</b><br/>embeddings · reranker ·<br/>Whisper STT · Piper / eSpeak TTS ·<br/>Tesseract OCR"]:::core
    end

    subgraph stores["State"]
        direction LR
        pg[("<b>PostgreSQL</b><br/>system of record<br/>Alembic = only schema authority")]:::store
        qd[("<b>Qdrant</b><br/>vectors · versioned indexes")]:::store
        rd[("<b>Redis</b><br/>cache · session state · locks")]:::store
    end

    subgraph inference["Inference hosts (all behind the router)"]
        direction LR
        ollama["<b>Ollama</b><br/>CPU, self-hosted"]:::infer
        vllm["<b>vLLM</b><br/>GPU, OpenAI-compatible<br/>HF Space or local profile"]:::infer
        hosted["<b>Hosted open-weight API</b><br/>ADR-030 slots hosted1..3<br/>AURA_ALLOW_HOSTED_INFERENCE"]:::gated
        claude["<b>Claude</b><br/>ADR-033, complex turns only<br/>AURA_ALLOW_CLAUDE_INFERENCE"]:::gated
    end

    subgraph external["External systems"]
        direction LR
        github["GitHub<br/>OAuth · repo tools"]:::ext
        mcp["MCP servers"]:::ext
        sources["Knowledge sources<br/>refresh pipeline"]:::ext
        customer["Customer systems<br/>Track AP connectors<br/>(proposed, read-only)"]:::gated
    end

    user --> app
    operator --> console
    app -- "HTTPS · JWT + device attestation" --> gateway
    app -- "WebSocket /api/v3/voice/live" --> gateway
    console -- "HTTPS" --> gateway
    hostd <-- "WebSocket /api/v3/host/agent<br/>paired token" --> gateway
    app -. "pairs with" .-> hostd

    gateway --> kernel
    kernel --> router
    kernel --> inproc
    kernel --> pg
    kernel --> qd
    kernel --> rd
    workers --> pg
    workers --> qd
    workers --> rd

    router --> ollama
    router -- "openai_compatible" --> vllm
    router -.-> hosted
    router -.-> claude

    kernel --> github
    kernel --> mcp
    workers --> sources
    kernel -.-> customer
```

**Reading the diagram**

- Solid edges are live. Dashed edges and red boxes are **opt-in, flag-gated, or proposed**; they
  are off unless an operator enables them.
- The self-hosting rule is a fitness-tested invariant: only `echo`, `openai_compatible`,
  `ollama`, and the one gated `claude` kind can be built by `llm/registry.py`
  (`tests/architecture/test_no_external_inference.py`).
- Embeddings, reranking, ASR, TTS, and OCR run **inside** the Control Plane container. Only
  chat/completion generation crosses to the inference plane
  (`docs/deployment/architecture.md` §3).
- Sync between the Flutter app and the backend uses the generic cursor protocol on
  `/api/v3/sync`; the app keeps a local Drift database and works from it first.
