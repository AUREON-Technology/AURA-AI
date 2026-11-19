# 05 · Engineer agent: where a command actually runs

The Engineer persona acts on real code. A tool call can land in one of two places, chosen per
turn, and both sit behind the same planning, policy, and confirmation path.
See ADR-011 (desktop), ADR-013 (server workspaces), ADR-014 (confirmation boundary), and
ADR-016 (agent platform).

```mermaid
flowchart TB
    classDef user fill:#1f2937,stroke:#111827,color:#ffffff
    classDef kern fill:#dcfce7,stroke:#16a34a,color:#052e16
    classDef guard fill:#fef3c7,stroke:#d97706,color:#451a03
    classDef desk fill:#dbeafe,stroke:#2563eb,color:#0b1b3a
    classDef srv fill:#fee2e2,stroke:#dc2626,color:#450a0a
    classDef rec fill:#f3f4f6,stroke:#6b7280,color:#111827

    person(["User"]):::user
    consent["Engineer consent<br/>checked before the turn runs"]:::guard

    subgraph kernel["AIKernel · one bounded plan / execute / replan loop (ADR-016)"]
        direction TB
        loop["<b>Agentic loop</b><br/>five data-driven budgets:<br/>iterations · risk · time ..."]:::kern
        reg["<b>ToolRegistry</b><br/>1 schema validation<br/>2 policy: capability grants · taint · sensitivity<br/>3 confirmation for side-effecting calls"]:::guard
        route{"Desktop daemon<br/>paired and online,<br/>tool is workspace_local<br/>and side-effecting?"}:::kern
        loop --> reg --> route
    end

    subgraph desktop["Path A · the user's own machine (ADR-011)"]
        direction TB
        relay["<b>HostAgentRegistry + relay</b><br/>WebSocket /api/v3/host/agent"]:::desk
        hostd["<b>aura_hostd</b><br/>enforces containment itself,<br/>never trusts the server<br/>own command policy · may hold a call<br/>for a local human approval"]:::desk
        files[("Real project files<br/>and terminal")]:::desk
        relay --> hostd --> files
    end

    subgraph server["Path B · server-side persistent workspace (ADR-013, opt-in)"]
        direction TB
        mgr["<b>WorkspaceManager</b><br/>durable checkout per (user, repo)<br/>stopped between turns · reaper · max_running cap"]:::srv
        runner["<b>Docker sandbox runner</b><br/>each command = its own throwaway<br/>container over the bind mount.<br/>Never docker exec."]:::srv
        git["<b>Host-side git</b><br/>the GitHub token never<br/>enters the container"]:::srv
        mgr --> runner
        mgr --> git
    end

    boundary["<b>Confirmation at the workspace boundary</b> (ADR-014)<br/>writes inside the workspace are free;<br/>git_push and other boundary crossings need a human yes"]:::guard
    ledger[("<b>Durable run ledger</b><br/>agent_runs · steps · tool calls<br/>per (user, agent scope)")]:::rec

    person --> consent --> loop
    route -- "yes" --> relay
    route -- "no" --> mgr
    git --> boundary
    hostd -.-> boundary
    loop -. "records" .-> ledger
    reg -. "audit" .-> ledger
```

**What keeps this safe**

| Property | Mechanism |
| --- | --- |
| A model cannot run a tool by emitting a tool-call frame | frames are informational; only planning and execution run tools (ADR-006) |
| A poisoned document cannot trigger a write silently | a quarantined or caller-supplied context taints the turn, so a side-effecting tool needs explicit confirmation |
| The desktop daemon is not an open shell | containment and a command policy live in the daemon, which can still ask the local human |
| A workspace cannot reach the data tier or the gateway | its network is `internal: true`; only an allowlisting proxy reaches the internet |
| The gateway cannot create a privileged container | it never touches the Docker socket; a broker checks the request body before the socket proxy sees it |
| Memory does not leak between personas | memory is isolated per (user, agent scope) |

**Status.** The desktop path is built; Phase J (server workspaces) is **built, not proven**.
`deploy/scripts/verify-engineer-workspaces.sh`, run on the VPS, is what produces the evidence, and
a green `pytest` cannot stand in for it because the default suite's sandbox runner never touches
the Docker API.
