# AURA architecture diagrams

Six views of the system, drawn as [Mermaid](https://mermaid.js.org/) so they render on GitHub,
diff in review, and stay next to the code they describe. Each one is derived from the code and
ADRs named beneath it, not from a plan.

| # | Diagram | Answers | Sources |
| --- | --- | --- | --- |
| 01 | [System context](01-system-context.md) | Who and what does AURA talk to? | `docs/architecture/00-overview.md`, `docs/deployment/architecture.md` |
| 02 | [Backend layers](02-backend-layers.md) | What may import what, and what enforces it? | ADR-001, `tests/architecture/` |
| 03 | [Turn pipeline and trust channels](03-turn-pipeline.md) | What happens to one chat turn, and what can an item in the prompt do? | `aura_core/kernel/kernel.py`, ADR-006, ADR-029 |
| 04 | [Deployment topologies](04-deployment.md) | Where does each piece run, and what can reach what? | `deploy/docker-compose.prod.yml`, `docs/deployment/` |
| 05 | [Engineer agent](05-engineer-agent.md) | Where does a tool command actually execute? | ADR-011, ADR-013, ADR-014, ADR-016 |
| 06 | [Agents communication and tool calling](06-agents-and-tools.md) | How do agents hand work on, how is a tool call authorised, and which agents and tools exist? | live `Container` registry, `tools/`, `agents/`, ADR-006, ADR-036 |

## Colour and line conventions

| Style | Meaning |
| --- | --- |
| Green | Backend application code (gateway, kernel, workers) |
| Blue | Clients, ingress, and edge components |
| Amber | State and context (databases, memory, retrieved content) |
| Purple | Inference hosts and generation |
| Red, solid | Guards, sandboxes, and isolation boundaries |
| Red, dashed | Opt-in, flag-gated, or proposed: off unless an operator enables it |
| Grey | Observability, CI, and external systems |
| Dashed edge | Optional, background, or asynchronous relation |
| Thick edge | Deployment trigger |

## Keeping them true

- These describe the system **as built**. Where the code has a known gap, the diagram shows the
  gap and says so (see the note under 03 §3.3) rather than drawing the intended design.
- "Built" is not "proven": diagrams do not imply a component is live. Live state is in
  `docs/status/`.
- Change a compose network, a pipeline stage, or a provider kind and the matching diagram changes
  in the same commit.
- To render outside GitHub: `npx -p @mermaid-js/mermaid-cli mmdc -i <file>.md -o <file>.svg`.
