"""The `/api/v3/sync` push payload contract, server half (audit F-13).

The client sent memories as ``{"content_enc": ...}``; the column is ``fact_enc NOT NULL``. The
server ignores unknown payload keys, so nothing complained -- every saved memory was simply a row
the server could never insert. The fix is only durable if the two sides cannot drift again
unnoticed, so the contract is one checked-in file both suites assert against:

* here: the file equals what the models say (``api.v2.sync.payload_contract``), so a schema
  change fails this test until the file is regenerated;
* ``aura_client/test/sync_payload_contract_test.dart``: every ``SyncPayloads`` builder sends only
  keys the file lists for its table, and every key the file requires.

Regenerate after a deliberate schema change with::

    python -c "import json; from api.v2.sync import payload_contract; \\
      print(json.dumps(payload_contract(), indent=2, sort_keys=True))" \\
      > ../aura_client/test/fixtures/sync_payload_contract.json
"""

from __future__ import annotations

import json
from pathlib import Path

from api.v2.sync import payload_contract

_FIXTURE = Path(__file__).resolve().parents[3] / "aura_client" / "test" / "fixtures" / "sync_payload_contract.json"


def test_the_checked_in_contract_matches_the_models() -> None:
    assert _FIXTURE.exists(), f"missing {_FIXTURE}"
    expected = payload_contract()
    assert json.loads(_FIXTURE.read_text(encoding="utf-8")) == expected, (
        "the sync payload contract the client is tested against no longer matches the models; "
        "regenerate it (see this module's docstring) and fix the client payloads it then fails:\n"
        + json.dumps(expected, indent=2, sort_keys=True)
    )


def test_memories_require_fact_enc_not_content_enc() -> None:
    """The concrete drift F-13 found."""
    memories = payload_contract()["memories"]
    assert "fact_enc" in memories["required"]
    assert "content_enc" not in memories["columns"]


def test_server_owned_columns_are_not_in_the_contract() -> None:
    for table, spec in payload_contract().items():
        for owned in ("id", "user_id", "updated_at", "client_updated_at", "deleted_at", "sync_txid"):
            assert owned not in spec["columns"], (table, owned)
