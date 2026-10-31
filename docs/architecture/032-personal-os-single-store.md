# ADR-032: One personal-OS store for the agent and the app

**Status:** Proposed. Blocked on a decision by the owner (2026-10-31).
**Relates to:** CB-28 / audit F-18, roadmap M11, ADR-006 (trust channels and confirmation).

## Context

The agent and the app keep personal-OS items (tasks, events, expenses, health logs) in two
places:

- **The app's synced tables** are `tasks`, `calendar_events`, `finance_records`,
  `health_records`, `projects` and `goals` (`aura_backend/infrastructure/db/models.py`). The
  client reaches them through `/api/v3/sync` (`api/v2/sync.py`, with the txid cursor from
  migration 0014).
- **The agent's store** is `aura_personal_os_records`, behind `PersonalOsStore`. The
  `create_/list_/update_/delete_<noun>` tools in `aura_core/tools/records.py` write there. No
  client code and no route reads it.

As a result, a task the agent creates never reaches the Tasks tab. The agent also cannot see the
user's real tasks.

The owner chose to make the agent write to the synced tables. That would give one store and
retire `aura_personal_os_records`. This ADR records why that cannot be done as stated, and lists
the decisions that would unblock it.

## Finding: the synced tables are end-to-end encrypted with a key that only the device holds

- **How the key is created.** `aura_client/lib/core/security/encryption_service.dart`
  (`EncryptionService.init`) generates a random 32-byte AES key on the device
  (`encrypt.Key.fromSecureRandom(32)`).
- **Where the key is kept.** It is stored only on that device:
  - FlutterSecureStorage, under `aura_db_encryption_key`.
  - A local backup: the `.aura_db_key` file in the app-support directory on IO platforms, or a
    second secure-storage entry on the web (`encryption_runtime_{io,web}.dart`).
  - If secure storage fails, a key derived on the device (PBKDF2 over a local salt and install
    id).

  No code path uploads the key, wraps it or escrows it.
- **How fields are encrypted.** `encryptText` produces `gcm:<iv_b64>:<ciphertext+tag_b64>`
  (AES-256-GCM). The text fields are written this way in `core/sync/sync_payloads.dart`:
  `title_enc`, `description_enc`, `content_enc`, `fact_enc`, `citations_enc` and
  `tool_calls_enc`. On pull, `core/sync/sync_client.dart` decrypts them with `decryptText`.
- **The server never decrypts.** The sync endpoint stores `*_enc` as opaque text. Its only
  handling of those columns is to blank them on a tombstone (`_drop_tombstone_content`). Several
  backend modules state this contract explicitly: `domain/personal_os.py`, `domain/goals.py`,
  `triggers/briefing.py`, `context/engine.py`, `adapters/postgres_goals.py` and
  `adapters/field_crypto.py`.
- **Writing plaintext would invert the guarantee, not weaken it.** `EncryptionException`
  exists so the client fails closed rather than sending plaintext in a `*_enc` field.
  `decryptText` passes text it cannot parse through unchanged, so a plaintext title written by
  the server *would* display. Doing that would break the property the architecture promises, so
  it is not an option to take quietly.

So the server can neither produce ciphertext the client can decrypt nor read the user's real
items. This rules out the owner's chosen design ("agent tools write the same synced tables
through the sync layer, and the agent sees the user's items") unless one of the options below
is accepted first.

A side observation, read from the code and not checked at runtime: the key is per device and
nothing distributes it. A second device that pulls another device's rows would see `gcm:`
blobs it cannot decrypt. The `beforeOpen` integrity check in `core/database/database.dart`
treats that as key loss and clears the local content tables. This bears on option A below.

## Options

### A. Server-held or escrowed key (envelope encryption)

Each user gets a data key that the server can unwrap. For example, the server holds a
per-user DEK wrapped by a KMS or `AURA_DATA_ENCRYPTION_KEYS`-style master key, and the client
fetches the DEK after login and uses it in place of its local key.

- **Gains:** the owner's design becomes fully implementable: one store, the agent reads and
  writes real items, and they sync normally. It also fixes multi-device sync, which today has no
  way to share a key.
- **Costs:** the app is no longer zero-knowledge. This becomes the same model as `field_crypto`
  (encrypted at rest, readable by the running server).
- **Work required:**
  - A client key migration that re-encrypts every local row and re-pushes it.
  - A key-rotation story.
  - Changes to the copy and docs that promise zero-knowledge.
  - Updates to every `*_enc` comment and ADR that states the contract.
- **Size:** the largest change, touching the client, backend and security posture.

### B. Proposals only: the agent proposes and the client materialises and encrypts

The agent stops writing directly. The `propose_*` tools already exist and already work end to
end:

1. The kernel emits an `actions` SSE frame (`kernel.py`, `actions_from_tool_results`).
2. The client shows `PendingActionsCard`.
3. On approval, `PendingActionsExecutor` writes through the normal repositories, which encrypt
   locally and sync.

Under this option, `create_*` is retired in favour of `propose_*`. `update_*` and `delete_*`
would need new action types (only `add_*` and `log_health` exist today).

For the agent to see the user's items, the client would send a decrypted, bounded summary of
relevant items with the turn. It would travel on the DATA channel as caller context (ADR-029),
so it is used for that turn only and never stored server-side.

- **Gains:** keeps zero-knowledge. One store (the synced tables); the old store can be retired.
  A human approves every write, which fits ADR-006.
- **Costs:**
  - The agent cannot act while the app is closed, so there are no background or proactive
    writes (this affects M11 proactivity).
  - Each read costs one round trip of client-supplied context, and the agent sees only what the
    client chose to send.
  - Agent-initiated update and delete need client work.
- **Size:** moderate, mostly in the client. It needs a Flutter `analyze` and `test` pass.

### C. Plaintext staging column (`source = agent`)

Add nullable plaintext columns, for example `agent_title` and `agent_description`, plus a
`source` column to the synced tables. The server writes plaintext there through the sync write
path, so the txid, cursor and ownership rules all apply. On pull, the client encrypts the text
into `title_enc` and pushes back with the plaintext cleared.

- **Gains:** agent writes appear in the app on the next sync, even when the app was closed at
  the time.
- **Costs:**
  - The server holds plaintext for agent-authored items until a device next syncs. That is no
    worse than today, where `aura_personal_os_records` holds the same text (sealed only by the
    server's own key).
  - The agent still cannot read user-authored items, so it is only half of the owner's goal.
  - Two write shapes for one row.
  - Every client version must understand the new columns, or agent items render blank.
- **Size:** moderate across the backend, the client and a migration.

### D. App reads both stores

Keep `aura_personal_os_records`, add a read and sync endpoint for it, and have the client merge
those rows into its tabs.

- **Gains:** smallest change to the security model.
- **Costs:** two stores forever. The agent still cannot see user-authored items, and edits made
  in the app to agent items need a second write path. This is what the owner already rejected.

## Recommendation (for the owner to decide)

Two routes fit the owner's goal:

- **B**, if zero-knowledge is a product commitment. Optionally add C later for writes made while
  the app is closed.
- **A**, if "the agent can see and change my real items, even in the background" matters more
  than zero-knowledge. A also fixes the latent multi-device key gap.

Do not write plaintext into `*_enc` under any option.

## Decision

Not taken. CB-28 stays open until the owner picks an option. Implementation (re-pointing
`records.py`, moving the data out of `aura_personal_os_records`, sync-path tests against
Postgres) follows that choice.
