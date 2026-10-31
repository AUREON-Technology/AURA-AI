"""per-owner corpus revisions, so every gateway worker's lexical index converges

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-31

The BM25 index is built in each gateway worker's process. An upload, a new version, a delete or
an erasure updated only the index of the worker that served it, so the document was invisible to
lexical search on the other workers, and a deleted one stayed in their indexes, until restart
(audit F-14). ``aura_corpus_revisions`` holds one counter per owner key ('global' for owner-less
content). ``PostgresDocumentStore`` advances it in the same transaction as every change to that
owner's active chunks, and each worker reads it before a lexical search to know which of its
shards to refresh.

The statement is the adapter's own, verbatim and ``IF NOT EXISTS``, like 0016's: the adapter
still creates the table on first use, so this changes nothing on a database it already reached.
An absent row reads as revision 0, so no backfill is needed: a worker always refreshes a shard it
has not checked yet.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UPGRADE_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS aura_corpus_revisions (
        owner_key   TEXT    PRIMARY KEY,
        revision    BIGINT  NOT NULL DEFAULT 0
    )
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    # Derived data only: dropping it makes every worker refresh each shard once on its next
    # search, nothing more.
    op.execute("DROP TABLE IF EXISTS aura_corpus_revisions")
