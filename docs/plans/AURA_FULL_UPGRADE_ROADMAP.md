# AURA AI — Full Upgrade Roadmap
## Desktop IDE Productionization + Two-Plane Deployment Architecture

**Date:** 2026-09-25  
**Scope:** Consolidated upgrade roadmap from the current AURA architecture, audit baseline, deployment decisions, and Desktop IDE production plan.

---

# 1. Executive Objective

Upgrade AURA AI from its current advanced development state into a production-grade self-hosted AI platform, with the **Desktop IDE as the immediate product priority** and a clean two-plane production deployment:

```text
                         AURA AI
                            │
             ┌──────────────┴──────────────┐
             │                             │
      AURA Control Plane             AURA Inference Plane
          Railway                    Hugging Face GPU
             │                             │
      API / Kernel / Agents          vLLM / Models / GPU
      Memory / RAG / Tools            Embeddings / Rerank
      Personal OS / Admin             Vision / ASR / TTS
             │                             │
             └──────── authenticated ─────┘
                       service link
```

The Desktop IDE becomes the first major production product surface:

```text
AURA Desktop
├── Workspace
├── Explorer
├── Editor
├── Terminal
├── Git / Diff
├── Problems / Tests / Debug
├── AURA AI Coding
├── Agent Workspace
├── Approvals / Security
├── Host Management
└── Production Packaging / Updates
```

The roadmap preserves the existing AURA architecture rather than replacing it.

---

# 2. Core Engineering Principles

All implementation must follow these principles:

1. **Inspect before changing.**
2. Preserve existing architecture and ADRs unless a documented blocker requires change.
3. No fabricated completion.
4. A feature is complete only after behavioral verification.
5. Prefer incremental, reversible changes.
6. Keep domain/business logic outside UI.
7. Keep security and authorization before convenience.
8. Use:
   `BUILD → MEASURE → BREAK → EVALUATE → FIX → PROVE`.
9. Keep server orchestration separate from model execution.
10. Keep the Flutter client separate from infrastructure credentials.
11. Do not introduce Kubernetes or unnecessary distributed complexity without measured need.
12. No external AI inference APIs.
13. No model execution on the mobile client.
14. All normal AI requests flow through the AURA Control Plane.
15. The Inference Plane is an internal self-hosted model-serving system.

---

# 3. Current Target Architecture

## 3.1 Application architecture

```text
Flutter Mobile/Desktop
        │
        ▼
AURA Control Plane
        │
        ├── API
        ├── Auth / RBAC
        ├── AURA Kernel
        ├── ProviderRouter
        ├── Memory
        ├── RAG
        ├── Knowledge
        ├── Planning / Reasoning
        ├── Agents
        ├── Tools
        ├── Safety / Policy
        ├── Personal OS
        ├── Admin
        └── Celery / Scheduling
                │
                ▼
       Remote Self-Hosted
       Inference Provider
                │
                ▼
       AURA Inference Plane
                │
                ▼
               vLLM
                │
                ▼
             L40S GPU
```

## 3.2 Desktop architecture

```text
AURA Desktop
    │
    ├── Workspace
    ├── Intelligence
    └── Control
          │
          ▼
      aura_hostd
          │
          ▼
   Local operating system
```

The Desktop remains a professional IDE/workstation, while AURA intelligence is deeply integrated into the development workflow.

---

# 4. Repository Strategy

Use two independently deployable server repositories.

## Repository 1 — `aura-control-plane`

Owns:

- FastAPI
- authentication
- authorization
- API
- AURA Kernel
- ProviderRouter
- memory
- RAG orchestration
- knowledge
- planning
- reasoning orchestration
- agents
- tools
- safety/policy
- Personal OS
- Celery
- scheduled automation
- admin APIs
- database migrations
- PostgreSQL integration
- Redis integration
- Qdrant integration
- audit logging
- observability integration
- remote inference provider

Deployment target:

```text
Railway
```

## Repository 2 — `aura-inference-plane`

Owns:

- vLLM
- LLM model serving
- embedding serving
- reranking
- vision
- ASR
- TTS
- GPU health
- model lifecycle
- batching
- inference concurrency
- model configuration
- inference metrics

Deployment target:

```text
Hugging Face GPU server
```

## Critical boundary

Do NOT split `aura_core` arbitrarily.

The Control Plane owns:

> What should happen?

The Inference Plane owns:

> Execute model inference.

---

# 5. Two-Plane Deployment Roadmap

## DPL-0 — Deployment Baseline Audit

Inspect:

- Dockerfiles
- Docker Compose
- CI/CD
- environment files
- ProviderRouter
- model provider adapters
- health endpoints
- metrics
- startup scripts
- existing inference assumptions
- Railway compatibility
- Hugging Face runtime compatibility

Produce a dependency/ownership map before modifying code.

### Exit criteria

Every server component classified as:

- Control Plane
- Inference Plane
- Shared contract
- External infrastructure
- Desktop/mobile

---

## DPL-1 — Repository Split

Create:

```text
aura-control-plane
aura-inference-plane
```

Preserve Git history where practical.

Do not perform a destructive rewrite merely for repository separation.

Create explicit ownership documentation.

---

## DPL-2 — Inference API Contract

Create a versioned internal API:

```text
/v1/chat/completions
/v1/embeddings
/v1/rerank
/v1/vision/*
/v1/audio/transcriptions
/v1/audio/speech
/v1/health
/v1/models
```

The contract must define:

- request schema
- response schema
- streaming
- authentication
- timeouts
- request IDs
- correlation IDs
- model IDs
- capability IDs
- error semantics
- limits

---

## DPL-3 — Hugging Face Inference Plane

Deploy the GPU inference service.

Requirements:

- NVIDIA GPU detection
- CUDA verification
- vLLM
- model loading
- health/readiness
- authenticated API
- streaming
- concurrency
- batching
- GPU metrics
- model metadata
- failure handling
- no silent CPU fallback

Use the available L40S server appropriately.

Do not assume every model must remain loaded simultaneously.

---

## DPL-4 — Railway Control Plane

Deploy:

- API
- workers
- scheduler
- database integration
- Redis
- Qdrant integration
- remote inference provider

Use Railway secrets/environment configuration.

Never commit secrets.

---

## DPL-5 — Secure Service-to-Service Communication

Architecture:

```text
Flutter
   │
   ▼
Railway Control Plane
   │
   │ authenticated HTTPS
   ▼
Hugging Face Inference Plane
```

Requirements:

- HTTPS/TLS
- dedicated service credential
- secret rotation strategy
- request authentication
- request IDs
- correlation IDs
- bounded timeouts
- request size limits
- inference endpoint not publicly usable without authentication

Flutter must never know the inference URL or inference credentials.

---

## DPL-6 — ProviderRouter Integration

Maintain:

```text
Kernel
  ↓
ProviderRouter
  ↓
SelfHostedRemoteInferenceProvider
  ↓
Inference Plane
  ↓
vLLM
```

ProviderRouter retains:

- model selection
- provider selection
- failover
- health
- latency metadata
- streaming safety
- circuit breaking

---

## DPL-7 — Independent CI/CD

### Control Plane

PR:

- lint
- tests
- architecture checks
- security checks
- dependency audit
- Docker build
- deployment validation

Production:

- build
- deploy Railway
- migration/check
- health verification
- smoke tests

### Inference Plane

PR:

- lint
- config validation
- security checks
- Docker build
- inference contract tests

Production:

- build
- deploy
- GPU verification
- model loading
- readiness
- inference smoke test
- streaming smoke test

Each repository must be independently deployable.

---

## DPL-8 — Independent Versioning

Example:

```text
Control Plane:    v3.x.y
Inference Plane:  v1.x.y
```

Record compatibility explicitly.

Do not silently deploy incompatible contracts.

---

## DPL-9 — Failure Testing

Test:

- inference unavailable
- timeout
- 401/403
- 429
- 5xx
- model unavailable
- model loading failure
- GPU unavailable
- OOM
- interrupted streaming
- network failure
- Control Plane restart
- Inference Plane restart
- independent restart/recovery

---

## DPL-10 — Deployment Observability

Track:

- correlation ID
- request ID
- model
- revision
- provider
- capability
- queue time
- TTFT
- generation time
- token counts
- status
- error class
- GPU utilization
- VRAM

Separate:

```text
API latency
Queue latency
RAG latency
Tool latency
Network latency
Model queue latency
GPU generation latency
```

---

## DPL-11 — Rollback and Recovery

Control Plane:

```text
Railway previous known-good release
```

Inference:

```text
previous known-good image/model configuration
```

A failure in one plane should not automatically require rebuilding the other.

---

# 6. Desktop IDE — Primary Product Priority

The Desktop IDE is now the first priority.

The goal is:

> Make AURA Desktop a production-ready professional IDE/workstation with first-class AI integration.

It should feel like:

> a professional IDE first, an AI system second.

---

# 7. Desktop Production Roadmap

## IDE-0 — Full Desktop Baseline Audit

Perform a read-only audit of:

- `aura_client`
- Desktop implementation
- `DesktopShell`
- Explorer
- Editor
- Terminal
- Host Panel
- Drift desktop state
- routing
- theme
- event bus
- agent integration
- filesystem
- pairing
- authentication
- packaging
- release scripts
- CI

Classify every component:

```text
VERIFIED
PARTIALLY VERIFIED
NOT IMPLEMENTED
BROKEN
UNTESTED
```

Do not use source-code existence as proof of functionality.

---

# 8. IDE-1 — AURA Desktop Design System

Create a unified professional IDE design system.

## Layout tokens

Define:

- activity bar width
- sidebar width
- secondary sidebar
- editor minimum width
- bottom panel height
- tab height
- title bar
- status bar
- panel split rules
- compact density
- comfortable density

Use the existing `AuraIdeDensity` concept.

## Visual system

Standardize:

- typography
- icons
- spacing
- borders
- elevation
- dialogs
- menus
- tooltips
- controls
- empty states
- loading
- errors

Avoid:

- mobile-style cards
- excessive rounded containers
- excessive shadows
- inconsistent spacing
- random icon sizing
- excessive decorative UI

## State system

Every important component must define:

```text
idle
hover
focused
active
selected
loading
success
warning
error
disabled
offline
permission-required
dirty
unsaved
running
failed
```

---

# 9. IDE-2 — Professional Desktop Shell

Target:

```text
┌──────────────────────────────────────────────────────────────┐
│ Title / Workspace / Global Controls                         │
├────┬───────────────┬─────────────────────────────┬───────────┤
│    │               │                             │           │
│ A  │ Explorer      │ Main Workspace              │ AURA      │
│ C  │ / Sidebar     │                             │ /Control  │
│ T  │               │ Editor / Diff / Welcome     │           │
│ I  │               │                             │           │
│ V  │               │                             │           │
│ I  │               │                             │           │
│ T  │               │                             │           │
│ Y  │               │                             │           │
├────┴───────────────┴─────────────────────────────┴───────────┤
│ Terminal / Problems / Output / Debug                        │
├──────────────────────────────────────────────────────────────┤
│ Branch | Sync | Host | Agent | Diagnostics | Status          │
└──────────────────────────────────────────────────────────────┘
```

Requirements:

- persistent activity bar
- resizable sidebars
- resizable bottom panel
- collapse/expand
- workspace-aware title
- command/search
- status bar
- responsive layout
- layout persistence
- focus mode
- distraction-free editor mode
- keyboard navigation

---

# 10. IDE-3 — Workspace & Explorer 2.0

Implement:

- workspace roots
- folder/file tree
- lazy loading
- modified indicators
- Git status
- hidden/ignored files
- context menus
- new file/folder
- rename
- delete
- refresh
- copy path
- copy relative path
- reveal in filesystem
- open terminal
- workspace search
- safe drag/drop where appropriate

Large repositories must remain responsive.

---

# 11. IDE-4 — Editor 2.0

Use the existing `EditorProvider` abstraction.

Required:

- multi-tab editing
- pinned tabs
- dirty state
- preview tabs
- split editor
- editor groups
- breadcrumbs
- syntax highlighting
- line numbers
- folding
- indentation guides
- bracket matching
- find
- replace
- replace-all
- go-to-line
- go-to-symbol
- quick open
- recent files
- undo/redo
- autosave
- session restoration
- crash recovery

---

# 12. IDE-5 — Command Palette & Navigation

Create:

```text
CommandRegistry
ShortcutRegistry
QuickOpen
CommandPalette
GlobalSearch
WorkspaceSearch
```

Commands:

```text
File
Edit
View
Go
Terminal
Git
AURA
Agent
Workspace
Host
Debug
Window
```

Keyboard-first operation must be supported.

---

# 13. IDE-6 — Real Terminal / PTY

This is a major production milestone.

Build:

- PTY sessions
- interactive stdin
- stdout/stderr streaming
- terminal resize
- terminal tabs
- terminal groups
- process lifecycle
- exit codes
- environment handling
- working directory
- restart
- kill
- search
- shell selection

Platform handling:

```text
Windows → PowerShell / cmd
Linux → user shell
macOS → user shell
```

Do not assume POSIX shell semantics on Windows.

---

# 14. IDE-7 — Git & Diff

Implement:

- branch
- status
- stage/unstage
- commit
- checkout
- branch creation
- fetch
- pull
- push
- history
- file history
- conflict indication

Diff:

- unified
- side-by-side
- inline
- file navigation
- hunk review
- revert hunk
- AI-generated diff labeling

---

# 15. IDE-8 — AURA AI Coding Layer

AURA must be embedded in the developer workflow.

Selection actions:

```text
Explain
Fix
Refactor
Optimize
Generate tests
Document
Translate
Simplify
Find bug
```

AI editing must follow:

```text
Request
  ↓
Plan
  ↓
Files affected
  ↓
Proposed changes
  ↓
Diff review
  ↓
Accept / Reject
  ↓
Tests
  ↓
Result
```

Never silently modify multiple files without a reviewable change representation.

---

# 16. IDE-9 — Inline Intelligence

Support:

- inline completion
- contextual suggestions
- explain selection
- quick fixes
- code transformation
- test generation
- documentation generation

The AI must understand:

- current file
- workspace
- relevant files
- diagnostics
- Git state
- task context

Context should be explicit and inspectable.

---

# 17. IDE-10 — Problems / Tests / Debug

## Problems

Unified diagnostics:

```text
Errors
Warnings
Info
Hints
```

Each:

- file
- line
- column
- severity
- source
- message
- quick fix

## Test Explorer

Support:

- test discovery
- run
- rerun
- run file
- run selection
- result
- stack trace
- failure navigation

## Debug

Build real foundations:

- debug session
- breakpoints
- continue
- step over
- step into
- step out
- variables
- call stack
- watches
- debug console

Do not ship fake debugger functionality.

---

# 18. IDE-11 — Agent Workspace

Integrate the existing Engineer architecture.

Example:

```text
AURA Agent
──────────────────
Task:
Fix authentication bug

Plan
✓ inspect auth
✓ locate failing path
● modify refresh logic
○ run tests
○ review diff
```

Display:

- task
- plan
- current step
- tool activity
- changed files
- terminal commands
- diagnostics
- approvals
- final result
- failures
- recovery

---

# 19. IDE-12 — Approval & Security UX

Security must be understandable.

Example:

```text
AURA wants to run:

git push origin feature/auth-fix

Risk:
Remote repository modification

[Review] [Approve] [Reject]
```

File access:

```text
AURA requests:

C:\Projects\MyApp

Permissions:
Read / Write / Execute

[Grant] [Cancel]
```

Show:

- action
- target
- requested permission
- reason
- risk
- reversibility

Do not bypass backend/host policy.

---

# 20. IDE-13 — Host Management

Create a compact professional Host panel:

```text
AURA Host
Status: Connected
Platform: Windows
Version: x.x.x
Workspace Roots: 3
Active PTY: 2
Agent Sessions: 1
Security: Protected
```

Sections:

- workspace access
- processes
- shell
- permissions
- pairing
- connection
- diagnostics
- logs

---

# 21. IDE-14 — Personalization

Persist:

- open workspace
- tabs
- editor groups
- active file
- sidebar width
- bottom-panel height
- visible panels
- terminal sessions
- selected activity
- theme
- density
- zoom
- recent workspaces

Use the existing Desktop-local state architecture.

---

# 22. IDE-15 — Accessibility

Implement:

- keyboard-only navigation
- visible focus
- semantic labels
- accessible dialogs
- predictable tab order
- scalable text
- contrast validation
- reduced-motion support
- accessible menus

---

# 23. IDE-16 — Performance

Measure:

- startup
- workspace open
- tree expansion
- file open
- save
- terminal startup
- AI TTFT
- panel transitions
- memory
- CPU
- large repository behavior

Never block the UI isolate with:

- filesystem scans
- Git operations
- terminal streams
- AI response processing
- large JSON parsing

---

# 24. IDE-17 — Large Repository Support

Test:

```text
1,000 files
10,000 files
50,000 files
100,000+ files
```

Also test:

- deep nesting
- large files
- ignored directories
- generated directories
- `.git`
- symlinks
- worktrees where applicable

Use:

- lazy loading
- indexing
- filtering
- debounced search
- virtualization
- cancellation

---

# 25. IDE-18 — Crash Recovery

Recover:

- unsaved files
- workspace layout
- terminal sessions
- incomplete AI tasks
- interrupted operations
- hostd restart
- application crash

Example:

```text
AURA recovered your previous workspace.

3 files had unsaved changes.
1 terminal session was interrupted.
1 AURA task is resumable.

[Review Recovery]
```

---

# 26. IDE-19 — Packaging & Updates

Production package must include:

```text
AURA Desktop
+
aura_hostd
```

Implement:

- installer
- version display
- update check
- update download
- update install
- rollback/failure recovery
- uninstall
- optional portable mode

The bundled `aura_hostd` binary must be a verified production artifact.

---

# 27. IDE-20 — Cross-Platform Certification

For every claimed platform:

```text
build
install
launch
login
pair host
grant workspace
open repository
edit
save
terminal
Git
AURA
agent
shutdown
restart
update
```

Do not claim support based only on successful compilation.

---

# 28. IDE-21 — Desktop Observability

Track privacy-conscious metrics:

- startup
- crashes
- workspace failures
- host failures
- terminal failures
- AI failures
- agent failures
- Git failures
- editor errors
- resource pressure

Use the existing `IdeEvent` architecture.

Important events:

```text
workspace.opened
file.changed
agent.started
agent.step
agent.completed
approval.requested
git.changed
diagnostic.added
terminal.started
terminal.exited
host.connected
host.disconnected
```

Do not collect source code/prompts by default for diagnostics.

---

# 29. IDE-22 — Security Certification

Adversarially test:

## Filesystem

- `..`
- symlink escape
- Windows junction escape
- unauthorized roots
- path confusion

## Commands

- shell chaining
- redirection
- interpreter spawning
- environment manipulation
- command laundering
- Windows-specific bypasses

## Agents

- unauthorized tool calls
- prompt injection
- permission escalation
- approval bypass
- cross-workspace access

---

# 30. IDE-23 — AI UX Evaluation

Measure:

- time to invoke AURA
- time to first visible response
- time to understand proposed change
- diff review time
- accept/reject behavior
- agent completion
- recovery after failure
- approval comprehension

Golden workflows:

```text
Explain code
Fix bug
Generate feature
Refactor
Generate tests
Run tests
Fix failing tests
Review diff
Commit
Push
```

---

# 31. IDE-24 — Golden Path Production Certification

The final test:

```text
Install AURA
↓
Login
↓
Pair hostd
↓
Grant workspace
↓
Open real Git repository
↓
Explore
↓
Open files
↓
Edit
↓
Save
↓
Run terminal
↓
Run tests
↓
See diagnostics
↓
Ask AURA
↓
AURA creates plan
↓
AURA modifies multiple files
↓
Review diff
↓
Approve
↓
Run tests
↓
Fix failure
↓
Review final diff
↓
Commit
↓
Push
↓
Restart
↓
Restore workspace
↓
Resume
```

This is the primary Desktop production acceptance test.

---

# 32. Desktop Priority Order

```text
P0  Baseline audit
P1  Design system
P2  Desktop shell
P3  Explorer
P4  Editor
P5  Command/navigation
P6  Real PTY terminal
P7  Git/diff
P8  AURA coding
P9  Problems/tests/debug
P10 Agent workspace
P11 Security/approvals
P12 Host management
P13 Personalization
P14 Performance
P15 Packaging/update
P16 Reliability/recovery
P17 Security certification
P18 Golden-path certification
```

---

# 33. AURA Platform Follow-Up Roadmap

Desktop productionization is the immediate priority. After it is stable, continue the wider AURA platform roadmap.

## Platform-1 — Immediate correctness/security

Address:

- PostgreSQL pool race
- server-side sensitive-data encryption
- encrypted backups
- restore drills
- idempotency
- security regression tests

---

## Platform-2 — Production Inference Plane

Move from development inference to dedicated production model serving.

Recommended architecture:

```text
Control Plane
      │
      ▼
ProviderRouter
      │
      ▼
GPU inference pools
      │
      ├── general-fast
      ├── general-quality
      ├── reasoning
      ├── coder
      ├── vision
      ├── embedding
      ├── reranker
      ├── ASR
      └── TTS
```

Use vLLM for production LLM serving where compatible.

---

# 34. Platform-3 — Scalability

Benchmark:

```text
1
2
5
10
20
30
40
50
75
100
```

Measure:

- RPS
- TTFT p50/p95/p99
- E2E latency
- queue wait
- tokens/sec
- GPU
- VRAM
- DB connections
- Redis
- Qdrant
- Celery
- failover
- 5xx
- unauthorized execution
- cross-user leakage

Do not claim 50/100-user readiness without measured evidence.

---

# 35. Platform-4 — RAG 2.0

Benchmark:

- Qwen3-Embedding variants
- Qwen3-Reranker variants
- query rewriting
- multi-query
- domain filters
- time filters
- user/project filters
- multimodal retrieval

Metrics:

- Recall@5/10
- MRR
- nDCG
- precision@k
- groundedness
- citation precision
- citation completeness
- faithfulness

---

# 36. Platform-5 — Memory 2.0

Implement:

- secure memory
- remember/forget
- export
- lifecycle
- decay
- consolidation
- scope isolation
- user/agent/project/workspace/conversation/org scopes
- memory quality evaluation

---

# 37. Platform-6 — Personal OS Completion

Complete CRUD/search/archive/bulk operations for:

- tasks
- calendar
- finance
- health
- projects
- goals

Maintain proper authorization and synchronization.

---

# 38. Platform-7 — Agent Platform

Create durable:

```text
AgentRun
AgentStep
Observation
Decision
ToolCall
Approval
Failure
Completion
```

Implement:

- bounded loops
- specialized agents
- agent memory scopes
- agent evaluations
- resumability
- failure recovery

---

# 39. Platform-8 — Multimodal

Implement:

- vision
- document understanding
- video timeline indexing
- visual grounding
- multimodal RAG

---

# 40. Platform-9 — Voice 2.0

Evaluate:

- Qwen3-ASR
- faster-whisper
- neural VAD
- barge-in
- echo cancellation
- streaming ASR
- streaming inference
- streaming TTS

Keep Arabic TTS only after passing actual Arabic evaluation.

---

# 41. Platform-10 — NLP Understanding

Implement structured understanding for:

- language
- intent
- entities
- structured IR
- context extraction
- task classification

---

# 42. Platform-11 — Knowledge Graph

First verify current implementation.

If absent, implement a lightweight Postgres-native graph with:

- provenance
- confidence
- validation
- relationships
- temporal information

Do not introduce a graph database without measured need.

---

# 43. Platform-12 — Proactivity

Architecture:

```text
Events
 ↓
Outbox
 ↓
Trigger
 ↓
Context
 ↓
Candidate insight
 ↓
Policy
 ↓
Grounding
 ↓
Priority
 ↓
Notification
```

---

# 44. Platform-13 — Real MLOps

Distinguish:

```text
Knowledge refresh
Evaluation refresh
Model training
```

Do not call non-weight-changing routines “training.”

Eventually implement:

- LoRA/QLoRA
- dataset filtering
- privacy filtering
- training
- evaluation
- safety evaluation
- human approval
- model registry
- canary
- rollback

---

# 45. Platform-14 — Evaluation System

Build gates for:

- unit
- integration
- RAG
- agents
- safety
- multimodal
- voice
- performance
- security
- model releases

Evaluation failures must block relevant production releases.

---

# 46. Platform-15 — Observability

Track:

- model
- revision
- provider
- queue
- TTFT
- tokens
- GPU
- cache
- RAG
- tools
- agent iterations
- failures

---

# 47. Platform-16 — Flutter Completion

Build a client API reachability matrix.

Verify:

- loading
- errors
- offline
- retry
- auth expiry
- stream reconnection
- voice
- multimodal
- citations
- agent status
- approvals
- model status

Remove or document unreachable backend surfaces.

Do not let Flutter bypass the Control Plane.

---

# 48. Platform-17 — Admin Control Plane

Admin should control:

- models
- deployment
- rollback
- providers
- GPU
- queues
- agents
- tools
- approvals
- RAG
- memory
- training
- security
- feature flags

---

# 49. Platform-18 — HA / Disaster Recovery

Implement:

- backups
- restore drills
- failure recovery
- service restart
- inference recovery
- data recovery
- configuration recovery

Do not claim HA without tested recovery behavior.

---

# 50. Platform-19 — Model Supply Chain

Track:

- model ID
- revision
- SHA
- license
- quantization
- source
- scanner results
- benchmark results

Production models must be pinned and reproducible.

---

# 51. Platform-20 — Production Canary

Progressively launch:

```text
5
↓
10
↓
25
↓
50
↓
100
```

At each stage verify:

- reliability
- latency
- resource utilization
- security
- failure behavior
- user-visible errors
- recovery

---

# 52. Final AURA Target State

```text
                           AURA AI
                              │
          ┌───────────────────┴───────────────────┐
          │                                       │
     CLIENT LAYER                           CONTROL PLANE
          │                                       │
  ┌───────┼────────┐                    ┌─────────┼─────────┐
  │       │        │                    │         │         │
Mobile  Desktop   Web                 Kernel    Agents    Personal OS
          │                               │
          │                         ProviderRouter
          │                               │
          │                   ┌───────────┼───────────┐
          │                   │           │           │
          │                 Memory       RAG        Tools
          │                   │           │           │
          │                   └───────────┼───────────┘
          │                               │
          │                         INFERENCE API
          │                               │
          │                         ─────────────
          │                               │
          │                       INFERENCE PLANE
          │                               │
          │                             vLLM
          │                               │
          │                    ┌──────────┼──────────┐
          │                    │          │          │
          │                  LLMs      Vision      Audio
          │                    │
          │                  L40S GPU
          │
          └──────────── Desktop Host
                         │
                     aura_hostd
                         │
                  Local filesystem /
                  terminal / processes
```

---

# 53. Master Execution Sequence

The overall implementation order should be:

```text
PHASE A
Desktop baseline audit
        ↓
PHASE B
Desktop design system
        ↓
PHASE C
Desktop shell + Explorer + Editor
        ↓
PHASE D
Command system + PTY
        ↓
PHASE E
Git + Diff + Problems + Tests
        ↓
PHASE F
AURA AI coding integration
        ↓
PHASE G
Agent workspace + approvals
        ↓
PHASE H
Host security + recovery
        ↓
PHASE I
Packaging + updates
        ↓
PHASE J
Desktop production certification
        ↓
PHASE K
Two-repository split
        ↓
PHASE L
Hugging Face inference deployment
        ↓
PHASE M
Railway control-plane deployment
        ↓
PHASE N
Secure integration
        ↓
PHASE O
Load/performance testing
        ↓
PHASE P
RAG / Memory / Agent / Multimodal upgrades
        ↓
PHASE Q
MLOps + evaluation gates
        ↓
PHASE R
Canary production rollout
```

---

# 54. Definition of Done

AURA Desktop is production-ready only when:

```text
✓ Professional IDE UI/UX
✓ Stable Desktop shell
✓ Workspace management
✓ Explorer
✓ Editor
✓ Command system
✓ Real PTY terminal
✓ Git
✓ Diff
✓ Problems
✓ Tests
✓ Debug foundation
✓ AURA inline intelligence
✓ Multi-file AI editing with review
✓ Agent workspace
✓ Approval UX
✓ Host management
✓ Security controls
✓ Persistent layout
✓ Crash recovery
✓ Large repository performance
✓ Installer
✓ Update mechanism
✓ Cross-platform verification
✓ CI/CD
✓ Security certification
✓ Golden-path E2E certification
```

The platform deployment is complete only when:

```text
✓ Control Plane deployed on Railway
✓ Inference Plane deployed on Hugging Face GPU
✓ Service authentication verified
✓ ProviderRouter uses remote self-hosted inference
✓ No Flutter → inference path
✓ No external AI inference APIs
✓ Independent GitHub repositories
✓ Independent CI/CD
✓ Independent versioning
✓ Health checks
✓ Observability
✓ Failure recovery
✓ Rollback
✓ Backup/restore
✓ Load testing
```

---

# 55. Engineering Completion Rule

Never report:

> "Implemented"

merely because source code exists.

Use:

```text
IMPLEMENTED
        ↓
BUILT
        ↓
EXECUTED
        ↓
TESTED
        ↓
FAILURE TESTED
        ↓
RECOVERED
        ↓
MEASURED
        ↓
VERIFIED
```

Only the final state is considered production-ready.

AURA's objective is not merely to accumulate features.

The objective is to produce a **coherent, secure, measurable, maintainable, professional AI operating environment** where the Desktop IDE, Control Plane, and Inference Plane behave as one system while remaining independently deployable and independently recoverable.
