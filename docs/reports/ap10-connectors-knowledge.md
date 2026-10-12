# AP10: Files, Documents and Git Connectors; Project Knowledge; Identity Modes (evidence)

**Phase:** AP10 ([Track AP roadmap](../roadmaps/AURA_ADAPTIVE_PLATFORM_ROADMAP.md)) · **Gap:** GAP-059 ·
**Backlog:** CB-11, CB-40 · **ADRs:** 020, 021 · **Started:** 2026-10-12 · **Base:** `910949a` (main)

**Maturity reached:** IN PROGRESS. This first slice covers:
- the CB-11 isolation prerequisite;
- implementation area 1 for **tabular files** (CSV and JSON).

The remaining areas are listed under *Not reached*. The exit criteria are not met yet.

**Ordering note:** the roadmap lists AP9 (the Console Project Factory) as a dependency. This slice is
backend-only, as AP2–AP8 were, and needs no console.

## CB-11: "global" is an explicit sentinel (prerequisite)

**Before:** both vector adapters treated a point with no `owner_id`, or an empty one, as global.
Qdrant used an is-empty clause for this. So a write path that forgot the owner made that user's
content visible to every user.

**Now:**
- `ports/storage.py` defines `GLOBAL_OWNER = "global"` and `require_point_owner()`.
- `QdrantVectorStore.upsert` and `InMemoryVectorStore.upsert` refuse an owner-less point. The whole
  batch is checked before anything is written.
- On read, only the exact sentinel is global. A point left over without an owner matches no filter,
  so it fails closed rather than open.
- The knowledge engine and index manager use the same constant.
- Every existing writer already set `owner_id` explicitly (knowledge chunks, index builds, memory),
  so no legitimate write changes.

**Evidence:** `tests/unit/test_vector_owner_isolation.py` runs the same scenarios against the
in-memory adapter and against `QdrantVectorStore` on qdrant-client's in-process mode, which
evaluates the real filter objects. It covers:
- refused writes, including a mixed batch;
- an owner-less legacy point invisible to every caller;
- no cross-user leakage;
- global content still excludable.

The old test asserting "a missing owner key counts as global" is replaced by its opposite.

**Not proven:** the isolation probe against a real Qdrant server, with a document ingested per
identity, is still to run (AP10 exit criterion).

## Area 1: the Files connector, tabular part

`aura_core/adapters/connectors/files.py`, `ConnectorKind.FILES` (service account, no credential).

**Containment:**
- The endpoint is a directory relative to `AURA_ADAPTATION_FILES_ROOT`. With that unset the kind is
  not registered at all.
- Absolute paths, `..`, and symlinked directories that resolve outside the root are refused
  (`invalid_endpoint`).
- Symlinked files are skipped with an issue, and files are opened with `O_NOFOLLOW`.
- Limits are 20 MB per file and 100,000 rows per table. Hitting either is recorded as an issue and
  as truncation.

**Discovery:**
- Each top-level `*.csv` or `*.json` (an array of objects) becomes a table in the `files` schema.
- The connector builds the PostgreSQL connector's catalog snapshot and maps it with the same
  `artifacts_from_catalog`. So the normalizer, planner, compiler and gate are unchanged.
- Provenance cites `files_catalog` / `files_profile`, not `pg_catalog`.

**Inference (evidence only):**
- **Column types:** a type is assigned only when every value fits it. Otherwise the column is text.
  Leading-zero codes stay text.
- **Primary key:** `id` or `<table>_id`, when all values are present and unique.
- **Foreign key:** `<name>_id`, when every value occurs in the key of table `<name>` or `<name>s`.
- **Profiles** use the PostgreSQL connector's pattern rules. No row values are included unless
  `sample_values` is set.

**Reads:** compiled SQL templates run through the gate's in-memory evaluator
(`InMemoryReadConnector`). FILES joins POSTGRES as a SQL-template kind in
`domain/blueprint.py` and `factories/tools.py`. No SQL text is rendered.

**Evidence:** `tests/unit/test_adaptation_files.py` (17 tests):
- type, key and profile inference;
- no foreign key claimed for a dangling reference;
- byte-identical rediscovery;
- JSON tables, plus a malformed JSON file recorded as an issue;
- containment: escape attempts, no root, symlinked file and directory;
- typed reads, and refusal of another connector's template.

**End-to-end:** the HR pilot's recorded rows, written as CSV files, go through discovery → twin →
plan → compile → **AP8 gate passes**. Every compiled SQL tool runs, and its result is compared with
the oracle reading the files.

## Not reached (rest of AP10)

1. **Documents.** PDF/DOCX/MD/HTML → `KnowledgeEngine.ingest` with an explicit project owner, and
   retrieval for the project agent.
   - Only `pypdf` is in the lockfile.
   - DOCX needs a new pinned dependency.
2. **Excel.** Needs `openpyxl`, which is not in the lockfile.
3. **Git connector:** ORM models, migrations, routes and API docs → AUSM candidates.
4. **`CompositeFetcher`** wired in `container.py`.
5. **MySQL and SQL Server** discovery adapters.
6. **Identity modes:** service-account role → policy mapping, delegated OIDC passthrough, and
   refusal on an expired delegated token.
7. **Exit evidence:**
   - pilot retrieval eval;
   - cross-identity isolation probe on a real Qdrant;
   - Git discovery goldens.

## Residual risks

- The kind is off until an operator sets `AURA_ADAPTATION_FILES_ROOT` and mounts a directory. The
  production and dev compose files deliberately do neither yet.
- Files are re-read on every tool call: bounded by the file-size cap, but not cached.
- Foreign keys are inferred only from the `_id` naming convention. Other relationships stay
  undiscovered rather than guessed.
