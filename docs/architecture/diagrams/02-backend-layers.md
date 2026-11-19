# 02 · Backend layers (hexagonal)

`aura_core` is a framework-free core with adapters plugged into ports. The dependency arrow never
reverses, and `tests/architecture/` enforces it with AST-based fitness tests.

```mermaid
flowchart TB
    classDef edge fill:#dbeafe,stroke:#2563eb,color:#0b1b3a
    classDef wire fill:#fce7f3,stroke:#db2777,color:#500724
    classDef engine fill:#dcfce7,stroke:#16a34a,color:#052e16
    classDef pure fill:#fef3c7,stroke:#d97706,color:#451a03
    classDef vendor fill:#f3f4f6,stroke:#6b7280,color:#111827

    subgraph wiring["Composition root"]
        container["<b>container.py</b><br/>the only place concrete meets abstract<br/>every adapter is built lazily"]:::wire
    end

    subgraph outer["Outer ring · adapters and entry points (vendor SDKs imported lazily, inside functions)"]
        direction LR
        api["<b>api/</b><br/>v3 gateway · admin · health · metrics<br/>+ api/v2 auth · sync · insights<br/>(permanent platform adapters, ADR-002)"]:::edge
        adapters["<b>adapters/</b><br/>Postgres · Qdrant · Redis · in-memory<br/>Docker sandbox · host-agent relay<br/>host git · GitHub · connectors · MCP"]:::edge
        providers["<b>llm/providers/</b><br/>openai_compatible · ollama<br/>claude (gated) · echo"]:::edge
    end

    subgraph engines["Engines · depend on domain + ports only"]
        direction LR
        kernel["<b>kernel</b><br/>AIKernel"]:::engine
        safety["<b>safety</b>"]:::engine
        prompts["<b>prompts</b>"]:::engine
        context["<b>context</b>"]:::engine
        memory["<b>memory</b>"]:::engine
        knowledge["<b>knowledge</b>"]:::engine
        planning["<b>planning</b>"]:::engine
        reasoning["<b>reasoning</b>"]:::engine
        execution["<b>execution</b>"]:::engine
        tools["<b>tools</b>"]:::engine
        llm["<b>llm</b><br/>router · cache"]:::engine
    end

    subgraph core["Core contracts · standard library only"]
        direction LR
        domain["<b>domain/</b><br/>entities · value objects<br/>errors · events"]:::pure
        ports["<b>ports/</b><br/>Protocols: LLMProvider · VectorStore<br/>MemoryStore · DocumentStore · Cache<br/>EventBus · Tool · Clock · AuditSink"]:::pure
    end

    vendors["Vendor SDKs and infrastructure<br/>asyncpg · qdrant-client · redis · docker · anthropic · httpx"]:::vendor

    container ==> outer
    container ==> engines
    outer --> engines
    engines --> core
    adapters -. "implements" .-> ports
    providers -. "implements" .-> ports
    outer --> vendors
```

**Rules the tests pin**

| Rule | Where it is enforced |
| --- | --- |
| `domain/` and `ports/` import only the standard library | `tests/architecture/test_dependency_rules.py` |
| Engines import domain and ports, never adapters | same |
| Vendor SDKs are imported inside functions, not at module scope | same |
| The `anthropic` SDK is importable only from `llm/providers/claude.py` | `SANCTIONED_SDK_IMPORTERS` |
| No other hosted-vendor branch in the provider registry | `test_no_external_inference.py` |
| No DDL string anywhere under `aura_core` (Alembic only) | `test_no_runtime_ddl.py` |
| An adapter's `__init__` never makes a network call; the first real use does, behind an `asyncio.Lock` when callers could race | pattern: `PostgresPool._get_pool()` |

Every storage port also has a **real in-memory adapter**, not a mock. That is why the default
`pytest` run needs no Docker, no network, and no model.
