# AP10: Files, Documents, Git, MySQL and SQL Server Connectors; Project Knowledge; Identity Modes (evidence)

**Phase:** AP10 ([Track AP roadmap](../roadmaps/AURA_ADAPTIVE_PLATFORM_ROADMAP.md)) · **Gap:** GAP-059 ·
**Backlog:** CB-11, CB-40 · **ADRs:** 020, 021 · **Started:** 2026-10-12 · **Base:** `910949a` (main)

**Maturity reached:** IN PROGRESS. Three slices so far:
- 2026-10-12: the CB-11 isolation prerequisite, and implementation area 1 for **tabular files**
  (CSV and JSON);
- 2026-10-13: area 1 for **documents** (Markdown, plain text, HTML, PDF) feeding **project
  knowledge**, with the cross-identity isolation probe and the pilot retrieval eval;
- 2026-10-14: area 2, the **Git connector**, reading a repository checkout's SQL, ORM models,
  migrations, routes and API description as twin evidence, with Git discovery goldens;
- 2026-10-14: area 3, **`CompositeFetcher`** in `container.py`, with a fetcher that reads
  documents through Track AP connectors for the nightly refresh;
- 2026-10-14: area 4, **MySQL and SQL Server discovery** behind the same port as PostgreSQL,
  proven against live MySQL 8.0 and SQL Server 2022 servers;
- 2026-10-18: area 5, **identity modes**: delegated token passthrough with refusal on a missing,
  expired or rejected token, and service-account role bindings enforced through the existing
  tool and agent gates.

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

## Area 1: documents and project knowledge (2026-10-13)

**Path:** a document is discovered, approved, ingested on deploy, then searched by the project's
agents.

1. **Discovery.** The Files connector reports each top-level `*.md`, `*.markdown`, `*.txt`,
   `*.html`/`*.htm` and `*.pdf` as a `document:<file name>` artifact (`ArtifactKind.DOCUMENT`).
   - The artifact carries format, size and SHA-256 only, never the text.
   - Office formats (DOCX, XLSX, PPTX, …) are reported as `unsupported_format` issues.
   - Symlinks are skipped, like table files. Hidden and oddly named files are skipped.
   - An edited document changes the report digest.
2. **Twin.** The normaliser maps each document to a DOCUMENT element, labelled with its file name.
3. **Blueprint.** The compiler makes every DOCUMENT element a `knowledge.sources` entry.
   - When there are any, the blueprint lists the native tool `project_knowledge` in
     `native_tools` and in every agent's grant. The grant is part of the digest the approver
     signs.
   - The compiler reserves the name, so no table can compile a tool that shadows it.
   - The blueprint refuses the tool without sources, and refuses a source from another connector.
4. **Deploy.** After the pointer moves, `ProjectKnowledge.sync` (`adaptation/knowledge.py`) fetches
   each source through the connector's new `fetch_document` (`ports.connectors.DocumentConnector`).
   - The bytes are extracted by `knowledge.documents.extract_document`:
     - Markdown and text are taken as they are;
     - HTML through the stdlib parser, with scripts, styles and markup dropped;
     - PDF through `pypdf`, page by page.
   - The extractor is injected by the container, so the adaptation layer still imports no engine
     (`test_core_immutable_from_adaptation.py`).
   - Each document is ingested through the ordinary `KnowledgeEngine.ingest` path at
     `TrustTier.UNTRUSTED`, so it gets the same guards and quarantine as any upload.
5. **Search.** The `bp<version>_project_knowledge` tool needs the `project_data` capability. It
   returns JSON passages, each with its document name, citation and score.

**Ownership and isolation.**
- Project knowledge is owned by `project:<project id>`. This is namespaced the same way a service
  key acts as `service:<name>`.
- Since CB-11, owner filters match exactly. As a result:
  - a person's retrieval never sees project documents;
  - project search runs with `include_global=False`;
  - one project never sees another's documents.
- The gateway now refuses a JWT subject of `global`, `project:*` or `service:*`
  (`domain.identifiers.is_reserved_principal`). Without that, such a subject would read and write
  as that owner. Firebase UIDs never contain a colon, so no real user is affected.

**Sync behaviour.**
- Sync is idempotent: an unchanged file is skipped on its content hash, and an edited file becomes
  the next version of the same document.
- A document no source names any more is deleted, including after a rollback.
- An unreadable file is recorded as a per-document failure, not a failed deploy.
- A file whose hash differs from the one the twin recorded is reported as
  `changed_since_discovery`.
- The outcome appears in the deploy audit record and in blueprint readiness.

**Evidence:** `tests/unit/test_adaptation_project_knowledge.py` (25 tests).
- **End to end.** The HR pilot's CSVs and documents go through discovery → twin → compile →
  **AP8 gate passes** → approval → deploy. Then:
  - all 5 documents are ingested;
  - every generated agent holds the tool;
  - a call with `project_data` returns the leave policy;
  - a call without it is refused.
- **Isolation probe**, run over the whole knowledge engine on both the in-memory adapter and
  `QdrantVectorStore` in qdrant-client's in-process mode. It ingests a personal document, a global
  one, project `p0`'s documents and project `p1`'s documents, then checks:
  - each project sees only its own owner;
  - no person (alice, bob, anonymous) retrieves a `project:*` chunk;
  - alice sees her own and global content, and bob sees only global content.
- **Pilot retrieval eval** (`tests/fixtures/pilots/<pilot>/retrieval_eval.json`), recall@3:
  - **1.00** on HR: 14 questions over 5 documents, one of each format;
  - **1.00** on e-commerce: 8 questions over 3 documents.

  This used the suite's hash embedder with hybrid lexical retrieval. It proves the plumbing and
  gives a lexical floor, not the deployed model's recall.
- **Sync:** convergence, re-version on edit, removal when narrowed, isolated per-document failures,
  global documents never removed even when the store lists them beside the owner's (the PostgreSQL
  store's `list_active` does, the in-memory one does not),
  a connector that cannot fetch documents, a blueprint from another project refused, and HTML
  `<script>` text never indexed.
- **Gateway:** reserved-subject refusal in `tests/unit/test_authenticator_capabilities.py`.
- **Real PostgreSQL 16** (`tests/load/test_adaptation_project_knowledge_postgres.py`, opt-in
  `-m load`, run locally 2026-10-13 against a throwaway database). It covers:
  - sync over `PostgresDocumentStore`: 5 ingested, then 5 unchanged, then 1 removed when narrowed;
  - element metadata round-tripping through JSONB;
  - the store listing the global handbook beside the project's documents, with the handbook left
    untouched.

**Pilot fixtures added:**
- HR: `expenses-policy.md`, `remote-work.html` (it includes a hostile `<script>`),
  `onboarding.txt`, `code-of-conduct.pdf` (a one-page text PDF);
- e-commerce: `shipping-policy.md`, `warranty.html`.

## Area 2: the Git connector (2026-10-14)

`aura_core/adapters/connectors/git.py`, with readers in `code_schema.py` (SQL) and
`code_python.py` (Python). `ConnectorKind.GIT`, service account, no credential.

**Where the code comes from.**
- The endpoint is a checkout directory under `AURA_ADAPTATION_GIT_ROOT`: a read-only clone the
  operator keeps current. Containment is the Files connector's: the same `resolve_directory`
  check, no symlinks followed, and per-file, per-repository and file-count caps, each recorded as
  an issue when hit.
- With the root unset, the kind is not registered.
- AURA never runs `git` and never imports or executes a line of the repository. Python is read
  with `ast`, and only literal values are used; a test plants a file-writing statement and
  confirms it never runs.
- `tests/`, `node_modules/`, virtualenvs, build output and dot-directories are skipped.
- **GitHub over the API is not offered.** Activation requires `write_capable is False`, and a
  GitHub token cannot prove that: a classic `repo` scope always includes write, and fine-grained
  token permissions cannot be introspected. The way forward is a credential minted with verified
  read-only permissions (a GitHub App installation token with `contents: read`), which belongs to
  the identity-modes work.

**What it reads, in order, into one schema model:**
1. **SQL** (`*.sql`), in natural path order (`V2__…` before `V10__…`):
   - `CREATE TABLE`, `CREATE TYPE … AS ENUM` (and `ALTER TYPE … ADD VALUE`), simple views;
   - `ALTER TABLE` (add, drop and rename columns and constraints, and rename the table), `DROP`;
   - `COMMENT ON`, `GRANT` (including `ALL TABLES IN SCHEMA`), `SET search_path`.

   A tokeniser recognises strings, quoted identifiers, dollar-quoted bodies and comments, so a
   `;` or `CREATE TABLE` inside one never counts. `ON DELETE SET NULL` is never read as
   nullability.
2. **Alembic** migrations, in `down_revision` order, not file order: `create_table`,
   `add_column`, `drop_table`, `drop_column`, `create_foreign_key` and
   `create_unique_constraint`.
3. **ORM models**, for tables no migration declared:
   - SQLAlchemy: classic `Column` and 2.0 `Mapped[...]`, including annotation-only columns,
     `__table_args__`, and `Enum` over literals or a local `enum.Enum`;
   - Django: default and `db_table` names, the implicit `bigint` key, `ForeignKey` and
     `OneToOneField` as `<name>_id`, and `choices` as a closed value set.
4. The first **OpenAPI 3.x** file, through the OpenAPI connector's own mapping.
5. **FastAPI and Flask routes**, as operations: `APIRouter(prefix=)`, `Blueprint(url_prefix=)` and
   `<int:id>` converters are handled. Where the API description already has the same operation,
   the description wins.

Types, nullability, defaults and **constraint names follow PostgreSQL's own rules**
(`numeric(10,2)`, `employees_email_key`, an enum outside `public` printed schema-qualified). The
result is mapped by the same `artifacts_from_catalog` as a live database, and each table records
where it was declared (`declared_in`, `file`).

**How the twin and planner use it.** Code says what the system *declares*, not what it holds:
- Every `code_*` artifact enters the twin at `CODE_CONFIDENCE` (0.8).
- The Git source's SOURCE element carries `queryable: false`.
- The planner skips such sources entirely: none of their elements is a target, their roles get
  no agent, and their permissions widen nothing.
- What code contributes is evidence and `same_as` proposals. A route like
  `PATCH /leave-requests/{id}/approve`, matched to the database's `leave_requests` table, counts
  (at half weight, per the existing process-mining rule) as a transition into `approved`. In the
  HR pilot that carries `workflow.status` on the **database** column, which a database-only twin
  left just below the threshold. The tool still reads the database; the approver sees the added
  capability and its evidence.

**Evidence:** `tests/unit/test_adaptation_git.py` (30 tests).
- **Parity with the live catalog.** Each pilot's `schema.sql`, read as code, equals
  `golden/catalog.json`, the catalog PostgreSQL 16 recorded from the same DDL:
  - **HR:** 6 relations, 33 columns (type, nullability, default, comment), 20 constraints by name,
    the enum and 11 grants;
  - **e-commerce:** 8 relations, 37 columns, 22 constraints, the enum and 10 grants, including a
    quoted mixed-case table, a composite foreign key and an enum in another schema.

  The hostile column comment arrives as text.
- **Git discovery goldens.** `golden/discovery_git.json` for both pilot repositories
  (`tests/fixtures/pilots/<pilot>/repo/`), rendered by `scripts/pilot_discovery_goldens.py`. The
  existing PostgreSQL and OpenAPI goldens are byte-identical after the change.
  - **HR app** (SQLAlchemy 2.0, Alembic, FastAPI): it declares exactly the database's five base
    tables, every column name, and the same primary, unique and foreign keys. Two real
    code-versus-database differences are kept, not smoothed over: `assets.tag` is `varchar(32)` in
    code but `text` in the database, and `attendance.id` is `integer` in code but `bigint` in the
    database.
  - **Shop app** (Django, Flask, an OpenAPI file) exercises the Django conventions and
    description-over-route precedence.
- **Twin and plan.**
  - Code elements stay at or below 0.8 confidence and database elements at 1.0.
  - `same_as` is proposed for every one of the five HR tables against its model.
  - Routes attach to the entity they serve.
  - Adding the repository never targets it and never removes a capability or an agent from the
    database-only plan, for either pilot. A mutation check confirmed both pilots fail without the
    planner rule.
  - A blueprint compiles with the repository attached, and every read template targets the
    database.

## Area 3: `CompositeFetcher` and connector-backed knowledge sources (2026-10-14)

**Before:** the container sent every registered knowledge source, whatever its type, to
`HttpSourceFetcher`. `CompositeFetcher` (`learning/sources.py`) existed and was tested, but
nothing built it.

**Now** the container builds it:
- `web`, `rss` and `api` go to the HTTPS fetcher.
- The default stays the HTTPS fetcher, so a source registered under any other type is fetched
  exactly as before.
- With Track AP on, a new `connector` type is registered, served by `ConnectorSourceFetcher`.

**A `connector` source** (`uri: connector:<connector id>`) is read through the connector
registry's `open_active`:
- A connector that is pending, disabled or deleted fails the source; it is never read around.
- The connector must implement the new `DocumentConnector.list_documents()`, which the Files
  connector does for its directory, and the Git connector for a checkout's root `README` and
  `docs/` or `doc/` tree. Those files are now also DOCUMENT artifacts in Git discovery, so a
  blueprint can make them project knowledge too.
- Every run re-lists the documents. A new or edited file reaches knowledge without a new
  blueprint, and the knowledge engine's content hash keeps unchanged ones from being re-embedded.
- Document ids are stable per connector and locator.
- The source's `max_documents` and `max_bytes_per_document` ceilings hold.
- One unreadable file is skipped, not fatal.

**Two rules, each enforced twice.** The admin API applies them on registration, and the fetcher
again on every run, because a registry row is data and the refresh runs unattended:
- **Untrusted.** A trust tier above `COMMUNITY` is refused (422 at registration, an error at fetch
  time), never quietly lowered.
- **Project-owned.** The owner is set to `project:<id>` by the API, never chosen by the caller, and
  the fetcher refuses any other owner. A connector's documents therefore keep AP10's isolation:
  no person and no global search sees them.

The API answers 503 for a `connector` source when Track AP is off. With Track AP off, a
`connector` row would fall to the HTTPS default and be refused there, because its uri is not
https.

**Git documentation paths are contained:**
- Only a root `README.*` or a file under `docs/` or `doc/`, in a document format, with plain
  segment names, is a valid locator.
- A symlink anywhere on the path is not followed.
- `..`, absolute paths, source files and dot-files are refused.

**Evidence:** `tests/unit/test_adaptation_connector_sources.py` (30 tests), covering:
- fetch, extraction, stable ids and ceilings;
- trust, owner and uri refusals;
- an inactive connector, a connector that cannot list documents, and an unreadable document;
- Git documentation scope, containment and discovery;
- the container's dispatch table with Track AP off and on.

**End to end through the real container over HTTP:**
1. A trust-tier-2 connector source is refused (422).
2. A Files connector is registered.
3. A `connector` source is registered, and its owner reads `project:p0`.
4. A refresh before activation fails that source with `ConflictError`.
5. The connector is approved and activated.
6. The next refresh ingests all five pilot documents at `UNTRUSTED`, owned by `project:p0`. None
   of them is visible to a person or to the global listing.

Without Track AP, the same API call answers 503.

## Area 4: MySQL and SQL Server discovery (2026-10-14)

**Before:** `ConnectorKind.MYSQL` and `SQL_SERVER` existed in the domain (with service-account and
credential rules), but no factory served them. Registering one answered "unsupported kind".

**Now** `adapters/connectors/mysql.py` and `mssql.py` implement the same `DiscoveryConnector` port
as PostgreSQL, and `container.py` wires both factories:
- The drivers are **PyMySQL** and **python-tds**. Both are pure Python, so the image needs no
  `libmysqlclient` or ODBC driver. They are imported lazily and run in a worker thread.
  pyOpenSSL is added because python-tds negotiates TLS through it. The lockfile is regenerated
  with its own command; the only new pins are `pymysql`, `python-tds` and `pyopenssl`.
- Each capture produces the **PostgreSQL snapshot shape**, so `artifacts_from_catalog` and
  everything after it (normalizer, twin, planner, compiler) are shared, not forked.
  - Column types take the twin's canonical names: `varchar(n)` / `nvarchar(n)` become
    `character varying(n)`; `tinyint(1)` and `bit` become `boolean`; `datetime` and `datetime2`
    become `timestamp without time zone`; MySQL `timestamp` and `datetimeoffset` become
    `timestamp with time zone`. The engine's own type name stays as `type_name`.
  - A MySQL column `ENUM` becomes an enum type named `<table>_<column>`.
  - **CHECK text is rewritten only outside string literals.**
    - MySQL: `information_schema` escapes the clause once more, and adds charset introducers
      (`_utf8mb4'…'`) and backticks. All are undone, so a value such as `'on_leave'` is never
      cut.
    - SQL Server: `col IN (…)` is stored as a reversed OR chain, `([col]='b' OR [col]='a')`. That
      exact shape becomes `col IN ('a', 'b')` again, in declaration order. Other boolean
      structure stays as it is.
    - A LIKE pattern's `[0-9]` is not treated as an identifier.
  - **Grants are read from other principals too.** MySQL keeps them in `mysql.db` and
    `mysql.tables_priv`; SQL Server keeps them in `sys.database_permissions` at object, schema and
    database scope, each expanded to the tables it covers.
    - Without `SELECT` on those grant tables, or without `VIEW DEFINITION`, the result is the
      `grants_not_visible` issue, not a failed run. SQL Server additionally reports
      `check_definition_hidden`.
    - The connector's own account is excluded, as PostgreSQL excludes owner grants.
  - **Profiles are aggregates computed on the server**, exactly as on PostgreSQL. MySQL uses
    REGEXP. T-SQL has no regular expressions, so each pattern rule is an exact LIKE equivalent
    over a binary collation.
- **Read-only:**
  - MySQL sessions are opened `READ ONLY` and the capture runs in a `READ ONLY` transaction with
    `MAX_EXECUTION_TIME`.
  - SQL Server has no read-only session. The capture runs in one transaction that is always rolled
    back, with a `LOCK_TIMEOUT`.
  - Both connectors only ever send constant statements, plus one reviewed profiling builder each,
    which `test_no_freeform_sql.py` now lists.
- **Privilege inspection** feeds `ConnectorHealth.write_capable`, so activation refuses any
  finding:
  - **MySQL** parses `SHOW GRANTS`. Anything beyond `SELECT`, `SHOW VIEW`, `SHOW DATABASES` or
    `USAGE` is a finding, and so are a granted role and `WITH GRANT OPTION`.
  - **SQL Server** reads *effective* permissions (`fn_my_permissions`, which follows role
    membership, so `db_datawriter` and `sysadmin` are found), object- and schema-level
    `HAS_PERMS_BY_NAME`, and `WITH GRANT OPTION`.
    - `EXECUTE` is a finding (`procedure_execute`): through ownership chaining, a procedure writes
      whatever the account's table grants say.
    - Each fixed database role owns an empty schema of its own name. Those schemas are skipped;
      without that, `db_datareader` itself read as DDL-capable.
- **TLS:**
  - MySQL `tls` is `require` (the default), `verify-full` or `disable`, with an optional
    `ca_file`.
  - SQL Server `tls` is `verify-full` (the default), `verify-ca` or `disable`. python-tds can only
    encrypt against a CA, so there is deliberately no "trust anything" mode; a self-signed
    server's certificate is passed as `ca_file`.
  - **python-tds's host-name check is replaced.** It is the only check that runs before the
    password is sent, and as shipped it calls `X509.get_extension`, which pyOpenSSL has removed.
    Any certificate that names the host only in its subjectAltName raised `AttributeError`.
    `mssql._validate_host` matches the RFC 6125 way using `cryptography`: SAN DNS names, one
    leftmost wildcard label at most, and IP SANs; the CN is consulted only when there is no SAN.
    It fails closed.
  - A verification failure reports `tls_failed`, not `unreachable`.
- **Discovery only.** Neither dialect has templated reads behind `render_sql` yet:
  - the compiler drops every read capability on these sources with the reason
    `sql_dialect_unsupported`;
  - `read()` raises;
  - a blueprint still compiles, with the source as an integration and the twin complete.

**The HR pilot in three dialects.** `hr/mysql/schema.sql` and `hr/mssql/schema.sql` are the system
`hr/schema.sql` describes, written the way each shop would write it. They keep what the dialect
forces to differ:
- `VARCHAR` / `NVARCHAR` unique columns;
- table-level foreign keys in MySQL, which parses and ignores an inline `REFERENCES`;
- `ENUM` in MySQL, and `CHECK … IN` in SQL Server, which has no enum type;
- `COMMENT` clauses in MySQL, `MS_Description` extended properties in SQL Server;
- the same hostile column comment in both.

Each is seeded from `golden/rows.json`, so every engine holds the same 146 rows.

**Goldens.** `scripts/pilot_discovery_goldens.py --record-mysql URL --record-mssql URL` loads the
pilot into a fresh `aura_pilot_hr` database, creates a read-only `aura_discovery` account, and
captures. The commands were run against MySQL 8.0.46 and SQL Server 2022 (RTM-CU27); re-recording
reproduces the files byte for byte. The script writes:
- `golden/catalog_mysql.json` and `catalog_mssql.json`: the *raw* catalog rows, so the default
  suite exercises the mapping;
- `golden/discovery_mysql.json` and `discovery_mssql.json`: the rendered reports.

**Evidence: `tests/unit/test_adaptation_sql_engines.py` (101 tests)**, covering:
- **Goldens:** each committed report equals the mapping, and the recorded captures are complete
  and carry no seeded row value.
- **Parity with PostgreSQL.**
  - The same relations, columns and positions; the same primary, unique and foreign keys
    (columns and targets); the same 11 grants; and the same profile counts.
  - The same twin elements (entities, attributes, states, roles, relations, permissions,
    metrics).
  - **An identical capability plan (22 selections) and agent set** for MySQL and for SQL Server.
    The only difference in the twin: SQL Server's `leave_type` is a CHECK constraint rather than
    an enum, and it yields the same four states.
  - The hostile comment stays `UntrustedText`.
- **Mapping edges:** type tables, MySQL escaping and introducers, SQL Server OR chains and LIKE
  brackets, grant expansion and own-account exclusion, identity and view defaults, hidden check
  definitions, and both engines' privilege findings.
- **The TLS host check:** 11 certificates generated with `cryptography`, covering SAN over CN, the
  wildcard limits and IP SANs. It fails closed, and it is installed in place of python-tds's
  check.
- **Before any socket is opened:** preflight refusals (endpoint, options, TLS mode, `ca_file`
  type), `read()` refused, and the compile drop reason. The real container offers both kinds.

**Against live servers:** `tests/load/test_adaptation_discovery_engines.py` (opt-in, `-m load`,
with `AURA_TEST_MYSQL_URL` and `AURA_TEST_MSSQL_URL`) passes **12 of 12**, 6 per engine, against
the same MySQL 8.0.46 and SQL Server 2022:
- a live capture equals the recorded raw catalog;
- the connector, through its own connect path, discovers exactly the committed golden;
- the read-only account is healthy and not write-capable;
- an account granted `INSERT` on one table is `table_write`;
- an account without grant-table access, or without `VIEW DEFINITION`, gets `grants_not_visible`
  (and `check_definition_hidden`) with tables still discovered;
- a small budget truncates honestly.

Checked by hand on the same servers:
- `auth_failed`, `unreachable` and `tls_failed`, including `verify-full` against a certificate
  for another name;
- `verify-full` and `verify-ca` succeeding with the server's certificate as `ca_file`;
- MySQL root reported as `admin`, `ddl`, `grant_option` and `table_write`;
- a SQL Server account that may only `EXECUTE` a procedure, with `GRANT … WITH GRANT OPTION`,
  reported as `procedure_execute` and `grant_option`.

## Area 5: identity modes (2026-10-18)

**Before:**
- `ReadConnector.read(template, arguments)` took no identity, so every generated read ran as the
  connector. A `DELEGATED` OpenAPI connector read anonymously; its docstring said so.
- Every generated agent and tool was gated by `project_data` alone. Any holder could pick
  `bp_role_hr_manager`, and the orchestrator could call every generated tool.

**Delegated** (ADR-021 implementation notes have the details):
- **The token's path.** A client sends `X-Aura-Connector-Token: <connector id>=<token>`. The
  value is wrapped in `Credential` at parse time and carried in memory only:
  `TurnRequest.connector_tokens`, then `ToolContext.credentials`, then that one connector's read
  as `CallerIdentity.delegated_token`. Delegated sub-turns carry it too. A service key may not send
  one.
- **Refusals.** A delegated OpenAPI read sends the caller's token as the bearer. It refuses, with
  a re-authentication hint, when:
  - no token was sent, or it is a JWT already expired (30 s leeway) -- in both cases before
    anything is sent;
  - the target answers 401 or 403 -- refused once and never retried.
- **No stand-in, ever.** An anonymous, stored or service credential never replaces the caller's
  token. A delegated connector holding a stored credential is refused outright.
- **The gate** records a delegated connector's contract cases as `not_run: delegated_identity`.
  It has no person, so it holds no token, and it never borrows one.

**Service account:**
- **Role bindings.** `Blueprint.security.role_bindings` (AURA role → discovered ROLE id) is a
  compile input, checked against the twin. It is part of the approved digest.
- **With bindings,** `adaptation/access.py` turns them into capability names the existing gates
  already enforce:
  - a tool that the discovered grants give to any role needs `project_data:<capability>`;
  - a role agent needs `project_role:<role>`.
- **Per request.** The gateway adds a `project_data` holder's bound names from the blueprint this
  worker serves. That covers chat, streaming, live voice, `/tools` and `/agents`, and look-alike
  names are stripped first.
- **Who refuses.** `ToolPolicy` refuses the call and the registry hides the declaration;
  `AgentRegistry.resolve` falls back to the orchestrator.
- **Without bindings nothing changes**, the blueprint digest included.

**Evidence: `tests/unit/test_adaptation_identity.py` (60 tests).**
- **Delegated:**
  - the caller's token is the bearer;
  - a missing, expired or near-expiry token is refused with nothing sent;
  - a 401 or 403 is refused and never retried;
  - a stored credential is refused, and so is a token over HTTP;
  - a service-account connector ignores a caller's token;
  - the JWT expiry reader handles six token shapes.
- **The token's path:** a token reaches only its own connector, and stays redacted in `repr`;
  a generated tool passes the identity to `read`; the kernel carries tokens into the tool
  context; the header parses, and a bad header is refused without echoing the token.
- **Over HTTP**, through the real gateway authenticator: the header reaches the turn, with
  `project_data` from `AURA_PROJECT_DATA_ROLES`, and appears in no response. A malformed header
  is a 422 that names no token.
- **Role bindings:**
  - no bindings gives an identical digest and only `project_data`;
  - bindings are compiled into the signed blueprint and round-trip;
  - bad bindings are refused at compile and in the blueprint;
  - each AURA role gets exactly its bound roles' grants;
  - expansion needs `project_data` and a binding, and self-asserted role names are stripped.
- **Role bindings through the real registry:** a `HR-Manager` caller is refused a tool only the
  viewer role was granted (the viewer is allowed it), and does not even see it. A `project_data`
  holder with no binding sees no role-governed tool. A role agent resolves only for its own role.
  The gate acts with every grant, and the deployment grants from the version it has loaded.
- The 665 existing adaptation, kernel, delegation, v3 and connector tests still pass, with their
  read fakes taking the new parameter.

## Not reached (rest of AP10)

1. **DOCX and Excel.** Both need a parser the lockfile does not carry (`python-docx`, `openpyxl`).
   For now they are discovered as `unsupported_format` issues.
2. **Git beyond a mounted checkout:**
   - GitHub over the API (blocked on verifiable read-only credentials, see Area 2);
   - JavaScript/TypeScript ORMs and routes (Prisma, TypeORM, Express);
   - Django URL confs;
   - prefixes added in another file (`include_router(prefix=)`, `register_blueprint`).
3. **More fetchers behind `CompositeFetcher`:** `drive` and `repository` sources still fall to
   the HTTPS default, as they always did; a fetcher of their own is not built.
4. **MySQL and SQL Server reads.** Discovery is done (Area 4). Templated reads need each
   dialect behind `render_sql`: backtick or bracket quoting, `LIMIT` vs `TOP`/`OFFSET … FETCH`,
   and parameter style. Until then the compiler drops those capabilities with a reason.
5. **Identity, beyond Area 5:**
   - **No client sends the header yet.** The Flutter client and the console have no plumbing
     for `X-Aura-Connector-Token`, and AURA does not obtain a target system's token for the user
     (an OIDC token exchange or login handoff belongs with AP12a's delegated identity handoff).
     Until a client does, a delegated connector's tools refuse with the re-auth hint.
   - The live voice WebSocket carries no connector token. Voice turns get role bindings, but
     not delegated reads.
   - The console has no editor for `role_bindings`; the compile API takes them.
   - Project knowledge is visible to every holder of `project_data`; there are no per-document
     permissions.
   - The gate cannot exercise delegated reads (`not_run: delegated_identity`).
6. **Exit evidence still to produce:**
   - the retrieval eval with the deployed embedding model on the dev stack;
   - the isolation probe against a real Qdrant server (the in-process mode evaluates the same
     filter objects, but it is not the server);
   - a Git connector registered and discovered through the approval flow on the dev stack.
     The goldens are recorded, but the kind is off until `AURA_ADAPTATION_GIT_ROOT` is set and a
     checkout is mounted.

## Residual risks

- **Role bindings restrict what the source restricts, no more.** A capability no discovered role
  governs (an API or file source has no roles) stays open to every `project_data` holder. And the
  whole-project agent stays selectable by every holder: under bindings it simply shows them only
  the tools their roles grant, so a holder with no binding gets an agent with no governed tools.
- **A delegated token's expiry is only pre-checked for JWTs.** An opaque token is sent, and its
  expiry is learned from the target's 401.

- **python-tds's host check is replaced process-wide** (`pytds.tls.validate_host`). It is installed
  only when a SQL Server connector uses `verify-full`, it is stricter than the original, and a
  unit test pins that the attribute is the one replaced. A python-tds upgrade that renames it
  would surface there, not silently.
- **SQL Server discovery is not read-only at the session level.** The account is what activation
  vouches for (no write, DDL, EXECUTE or grant option), and every statement is a constant inside a
  rolled-back transaction. That is the same trust PostgreSQL rests on, minus the
  `READ ONLY` transaction belt.
- MySQL `require` (the default) encrypts but does not verify the certificate, as libpq's
  `sslmode=require`. `verify-full` is available, and the default can be raised once pilots show
  certificates are in place.

- The kind is off until an operator sets `AURA_ADAPTATION_FILES_ROOT` and mounts a directory. The
  production and dev compose files deliberately do neither yet.
- Files are re-read on every tool call: bounded by the file-size cap, but not cached.
- **Ingestion runs inside the deploy call**, after the pointer moves. A large document set
  embedded on the CPU model can make `POST …/deploy` slow. The blueprint is live either way, but
  the HTTP call can time out before the sync summary returns. A background sync with a status
  endpoint is the follow-up if pilots show it.
- **Documents can change after approval.** The approver signs which documents are sources, not
  their bytes. A change is ingested at deploy and reported as `changed_since_discovery`; refusing
  or re-approving it belongs to AP11 drift.
- **The AP8 gate does not evaluate project knowledge yet.** Its throwaway registry builds no
  knowledge tool. The retrieval eval above is a unit test, not a gate suite.
- **A documents-only project gets no agent.** Agents come from compiled data capabilities, so
  documents are ingested but no generated agent holds the tool. It is not a leak, just unused.
- Foreign keys are inferred only from the `_id` naming convention. Other relationships stay
  undiscovered rather than guessed.
- **A repository can only add evidence, but evidence is not nothing.** A hostile repository cannot
  create a target, an agent or a grant. It *can*, through `same_as` and process mining, raise a
  database workflow's confidence enough to carry a read capability (see Area 2). That capability
  is still a read of data the twin already exposes, it is listed on the approval request with its
  evidence, and the repository is chosen by the Super Admin who registers the connector.
- **The checkout is a snapshot.** AURA reads whatever is in the mounted directory. Keeping it
  current, and noticing that it changed, are the operator's job until AP11 drift detection.
- **Code readers cover the common forms, not every form.**
  - Anything computed at runtime is invisible: a table name from a variable, a column type from a
    function call. It is skipped or recorded as an issue, never guessed.
  - Django's implicit key is assumed `bigint` (`BigAutoField`), and `DateTimeField` time-zone
    aware. Both are stated defaults of a candidate schema.
