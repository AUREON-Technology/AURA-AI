# ADR-032: One personal-OS store for the agent and the app

**Status:** Accepted (owner, 2026-10-31): Option A, a server-held per-user data key (envelope
encryption). The design is under [Decision](#decision); the context and options above it are kept
as the record of why.
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

**Option A, decided by the owner on 2026-10-31.** The personal-OS fields of the synced tables
stop being zero-knowledge. They are encrypted with a per-user data key (DEK) that the server
holds, wrapped by a server key-encryption key (KEK). The agent's `create_/list_/update_/
delete_<noun>` tools read and write the same `tasks`, `calendar_events`, `finance_records` and
`health_records` rows the app syncs, and all of a user's devices share one DEK.

### Keys

- **DEK.** 32 bytes from `secrets.token_bytes` (the OS CSPRNG), generated on the server the first
  time a user needs one: the first key fetch or the first agent write. Each DEK has a `kid` of 8
  random hex characters. It is unique per user and not secret.
- **KEK.** The KEK is the existing `AURA_DATA_ENCRYPTION_KEYS` cipher
  (`aura_core/adapters/field_crypto.py`): Fernet, newest key first, every key decrypts.
  - That cipher already encrypts memories and the old `aura_personal_os_records` rows. These are
    the same class of data with the same blast radius.
  - A second secret would add a deploy step and a second rotation procedure. It would not
    separate failures, because both secrets live in the same process environment.
  - Staging and production already refuse to start without this setting.
  - In local development without it, a DEK is stored unwrapped. That is what the field cipher
    already does there.
- **Storage.** The keys live in `aura_user_data_keys`, created by migration `0017`.

  | Column | Holds |
  |---|---|
  | `owner_id` | The owner's `users.auth_subject`. A foreign key with `ON DELETE CASCADE`, so erasing the user row destroys their keys too (crypto-shredding: ciphertext that survives elsewhere, such as in a backup, becomes unreadable). |
  | `kid` | The key id. |
  | `wrapped_key` | The FieldCipher envelope, bound to the context `aura_user_data_keys:<owner>:<kid>`. A wrapped key copied onto another user's row fails authentication. |
  | `kek_id` | Which KEK wrapped it, so a KEK rotation can find the rows still under the old KEK. |
  | `status` | `primary` or `decrypt_only`. A partial unique index allows at most one `primary` per owner. |
  | `created_at` | When the key was created. |

- **Code.** The port is `UserDataKeyStore` (`ports/storage.py`).
  - The keyring (generate, wrap, unwrap, rotate) is `aura_core/adapters/user_data_keys.py`.
  - The table adapter is `postgres_user_data_keys.py`.
  - An in-memory twin keeps the default test run free of infrastructure.

### Ciphertext format

```
gcm2:<kid>:<base64 12-byte IV>:<base64 ciphertext || 16-byte GCM tag>
```

The cipher is AES-256-GCM over the UTF-8 plaintext, with no associated data. This is the
client's existing `gcm:<iv>:<ct>` primitive and encoding, plus a version tag and the key id. The
two formats coexist during the migration:

- `gcm2` values decrypt with the DEK their `kid` names.
- `gcm:` values decrypt only with the legacy key of the device that wrote them. The server never
  has that key, so to the server a `gcm:` value is unreadable: it is never decrypted and never
  handed to the model as text.

The server side uses `cryptography`'s `AESGCM` (`aura_core/adapters/personal_data_crypto.py`).
Fixed test vectors cross-check it against the Dart client in both directions.

### Key endpoint

`GET /api/v3/keys/data` returns `{"format": "gcm2", "primary": kid, "keys": [{kid, key,
created_at}]}`, with `key` in standard base64. It returns every key the user still has, so a
device can read values written under a `decrypt_only` key after a rotation.

- **Who can call it.** An authenticated user principal only. The key returned is always the
  caller's own: the owner is the authenticated subject, and no parameter names a user.
- **Service keys are refused (403)**, even one whose route list would admit the path. A non-human
  caller has no business holding a user's DEK.
- **No caching.** The response carries `Cache-Control: no-store, max-age=0` and
  `Pragma: no-cache`, and nginx already serves these routes with `proxy_cache off`.
- **TLS only.** In staging and production, a request that arrived over plain HTTP is refused with
  403 `tls_required`. The check reads `X-Forwarded-Proto` from the TLS-terminating proxy.
  Release builds of the client already refuse a non-HTTPS backend (`BackendUrlPolicy`).
- **Rate limit.** A per-user budget of 10 calls a minute, applied even when the general per-user
  limiter is disabled.
- **Audit.** Each fetch writes the audit event `data_key.read` with the key ids, never the key.
  Neither the handler nor its error paths log the response.
- **Device attestation is not re-checked per call.** The session that authenticates this call was
  attested when it was exchanged (`services/jwt_auth.py`), and a per-call check would need a
  single-use nonce round trip that no other v3 call makes. The client still attaches
  `DeviceValidationService.getAttestationHeaders()`, as every other client does.

### Server store: the agent writes the synced tables

`PostgresSyncedPersonalOsStore` (`aura_core/adapters/postgres_synced_personal_os.py`) implements
the existing `PersonalOsStore` port over the four synced tables. `container.py` wires it as
`personal_os_store` whenever the memory backend is Postgres, so `aura_core/tools/records.py` is
unaware of the change. The in-memory substrate keeps `InMemoryPersonalOsStore`.

The adapter keeps the contract `/api/v3/sync` defines (`api/v2/sync.py`). The core may not import
that module (ADR-002), so the adapter re-states the same rules in SQL. A real-Postgres test then
checks the result through the sync endpoint itself. The rules:

- **Ownership.** Every statement is scoped to `user_id = users.id`, found through `auth_subject`.
  For a user who has never synced, the `users` row is created the same way `get_or_create_user`
  creates it.
- **Pull cursor.** On insert, `sync_txid` is left to its column default. On update it is set to
  the writing transaction's id, so every device's next pull sees the agent's change.
- **Last write wins.** `client_updated_at` is set to the server clock. A device edit made offline
  before the agent's change loses to it, and one made after it wins, exactly as between two
  devices.
- **Deletes.** A delete writes a tombstone: `deleted_at` is set and every `*_enc` column is
  blanked, as `_drop_tombstone_content` does. Tombstoned rows are invisible to the agent.
- **Health records.** `health_records` holds one row per user per `record_date`. Creating one for
  a date that already has a row merges onto that row, as a sync push does, and the tool reports
  the stored id.
- **Fields the server cannot read.** `*_enc` fields are decrypted with the user's DEK. A field
  that is still legacy `gcm:` is left out of what the agent sees, with a note that it becomes
  readable once that device upgrades.
- **Partial updates.** An update writes only the columns the agent changed, so it never
  overwrites a field it could not read.

The four kinds map onto the existing columns. So that the agent can set fields the app shows, the
tools gained three optional parameters: `completed` (tasks), `occurred_at` (finance) and
`calories` (health).

### Client migration (`aura_client`)

`EncryptionService` now holds the device's legacy key and the user's DEKs.

- **`encryptText`** writes `gcm2` with the primary DEK once one is installed, and legacy `gcm:`
  until then.
- **`decryptText`** reads both formats. If the decrypted value is itself a ciphertext envelope,
  it unwraps that inner layer too. This covers a row pulled before the DEK arrived: it was
  stored as the server's `gcm2` text inside the device's legacy wrapper, and it becomes readable
  as soon as the DEK is installed.

`DataKeyMigration` runs inside `SyncClient.synchronize()`, after the pull and before a second
push:

1. **Fetch the DEK.** `DataKeyClient` fetches it and stores it in secure storage under
   `aura_user_data_keys`. If the device is offline, the server answers 503, or anything else
   fails, the migration is skipped and the app keeps working on the legacy key. It retries on the
   next sync.
2. **Re-encrypt local values.** For every encrypted column of every local table, each raw `gcm:`
   value is decrypted with the legacy key and, if what comes out is `gcm2`, unwrapped once more.
   It is then re-encrypted with the DEK and written back with a compare-and-swap on the old
   value.
3. **Queue the upload.** For each synced row whose plaintext was fully recovered, an upsert with
   the re-encrypted payload is enqueued in the same transaction as the local rewrite.
   - This is what makes the migration resumable. If the app is killed partway, every finished row
     has both its rewrite and its upload queued, and every unfinished row still holds `gcm:` for
     the next run to pick up.
   - A second run finds nothing to do.
4. **Rows from another device.** A value whose inner text is another device's `gcm:` (which this
   device never could read) is re-wrapped under the DEK but **not uploaded**. Uploading it would
   overwrite that device's row with text that cannot be read. When the other device migrates and
   pushes `gcm2`, the next pull replaces it here.
5. **Outbox.** Queued outbox payloads that still carry `gcm:` are re-encrypted too.
6. **Retire the legacy key.** Once no raw `gcm:` value is left, the legacy key is deleted from
   secure storage and from the `.aura_db_key` backup.

**The key-loss wipe no longer fires for these rows.** The `beforeOpen` check now inspects the
outer layer of each raw value, and counts as key loss only a `gcm:` value the legacy key cannot
open. Two cases used to look like key loss and no longer trigger the wipe:

- a row holding another device's ciphertext, which is the multi-device wipe described in the
  Finding above;
- a `gcm2` value whose DEK this device has not fetched yet, since the DEK can always be fetched
  again from the server.

Signing out deletes the DEK along with the rest of the account's local state (`wipeUserData` and
the secure-storage `deleteAll`).

### Moving the old store's data

`scripts/move_personal_os_records.py` copies `aura_personal_os_records` rows into the synced
tables, encrypted with each owner's DEK.

- It keeps each record's id.
- It is idempotent: `ON CONFLICT (id) DO NOTHING`, plus the natural key for health records.
- It supports `--dry-run`.
- It reports counts, never content.
- It does not drop the old table. That table keeps its FieldCipher encryption, and erasure still
  clears it (`api/admin_platform.py`).

### Threat model change

| Who | Before (zero-knowledge) | After (Option A) |
|---|---|---|
| A database dump or backup alone | Ciphertext, and no key anywhere on the server | Ciphertext, plus DEKs wrapped under a KEK that is not in the database: still nothing readable |
| A dump plus `AURA_DATA_ENCRYPTION_KEYS` | No personal-OS data (memories were already readable) | **Every user's task, event, finance and health text.** Once the devices migrate, also chat messages and project and goal text. |
| The running gateway, or whoever controls it | Nothing from the synced tables | Everything above, for any user, on demand |
| A stolen access token (15-minute JWT) or refresh token | Ciphertext, through a sync pull | Ciphertext **and the DEK**: the user's synced data in plaintext |
| A lost or stolen device | The device's legacy key and data | The user's DEK and data. A DEK cannot be revoked per device; the remedy is rotating it (below). |

Nothing may claim zero-knowledge for these fields any more. The docstrings, ADR text and docs
that said so were changed in the same change as this decision.

### Rotation

**KEK.**

1. Prepend a new Fernet key to `AURA_DATA_ENCRYPTION_KEYS` and restart.
2. Run `python scripts/reencrypt_fields.py`. It now also re-wraps `aura_user_data_keys`. Only the
   wrapping changes; the ciphertext in the synced tables is untouched.
3. Drop the old key once the script reports nothing left under it.

**DEK**, for example after a lost device:

1. Call `UserDataKeyStore.rotate(owner)`. In one transaction it demotes the current key to
   `decrypt_only` and creates a new `primary`.
2. Devices receive both keys on their next fetch. A device that meets an unknown `kid` fetches
   again.
3. New writes use the new key. Old rows stay readable under the old one.
4. A `decrypt_only` key may be deleted only after every row under it has been re-encrypted. There
   is no tool for that yet, so old keys are kept.

### Residual risks

- **Server compromise exposes the content**, as the threat-model table shows. This is the
  accepted cost.
- **Ciphertext is not bound to its row.** `gcm2` has no associated data tying a value to its row
  and column. Someone with database write access but no key can move one of a user's ciphertexts
  into another of that same user's rows. Moving it to another user's row fails, because the DEKs
  differ.
- **Health dates can split across time zones.** An agent-created health record for a date is
  stored at 00:00 UTC, while a device elsewhere stores its local midnight. The two can show as
  separate days until one is edited.
- **A device that never upgrades never re-encrypts its `gcm:` rows.** They stay unreadable to the
  server, the agent and other devices.
- **The migration's re-push can overwrite a recent plaintext change.** The re-push carries the
  device's current values with a fresh client clock. A plaintext field such as priority, changed
  elsewhere between that device's last pull and its migration, is overwritten. Running the
  migration right after a pull keeps that window to one sync.
- **Projects and goals have no agent CRUD tools.** The client migrates their `*_enc` fields, but
  `GoalsPort` still reads only their plaintext columns.

### Proof

The design above is built. It is tested by the unit tests, the client tests and a real-Postgres
integration test (`test_personal_os_sync_integration.py`).

It is **not yet proven in production.** Migration `0017` reaches production through
`scripts/railway-predeploy.sh`. After the merge, `/health/ready` must report `schema.revision
0017`; until it does, the key endpoint and the agent's personal-OS tools fail there.
