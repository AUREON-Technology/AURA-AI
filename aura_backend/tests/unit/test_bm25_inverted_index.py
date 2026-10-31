"""The inverted BM25 index scores exactly what the forward scan did (audit F-30 / AR-13).

The index was rewritten from "for every query term, visit every document's Counter" to posting
lists sharded by owner. That is only a performance change if every score is unchanged, so these
tests compare it against the forward scan it replaced (kept verbatim in ``_forward_bm25.py``) on
a seeded random corpus, through adds, removals and re-adds -- exact float equality, not approx.
"""

from __future__ import annotations

import random

import pytest

from aura_core.domain import DocumentId, UserId
from aura_core.knowledge import BM25Index, tokenize

from ._forward_bm25 import ForwardScanBM25

_GROUPS = ("alice", "bob", "carol", "__global__")


def _corpus(seed: int, size: int) -> list[tuple[str, str, str]]:
    rng = random.Random(seed)
    # Small vocabulary with a heavy head, so terms repeat within and across documents and some
    # appear in over half the corpus (the IDF floor case).
    vocabulary = [f"w{i}" for i in range(60)] + ["aura-4419", "v2.1.0", "the", "of"]
    weights = [1.0 / (i + 1) for i in range(len(vocabulary))]
    docs = []
    for i in range(size):
        words = rng.choices(vocabulary, weights=weights, k=rng.randint(1, 40))
        docs.append((f"d{i:03d}", " ".join(words), rng.choice(_GROUPS)))
    return docs


def _expected(reference: ForwardScanBM25, query: str, ids: set[str], limit: int):
    """The forward scan's answer, with the inverted index's documented doc_id tie-break."""
    everything = reference.search(query, limit=10**9, allowed_ids=ids)
    return sorted(everything, key=lambda item: (-item[1], item[0]))[:limit]


def _queries(seed: int) -> list[str]:
    rng = random.Random(seed)
    terms = [f"w{i}" for i in range(70)] + ["aura-4419", "v2.1.0", "unseen"]
    return [" ".join(rng.sample(terms, rng.randint(1, 8))) for _ in range(40)]


class TestParityWithTheForwardScan:
    @pytest.fixture
    def pair(self):
        docs = _corpus(seed=7, size=300)
        inverted, reference = BM25Index(), ForwardScanBM25()
        groups: dict[str, str] = {}
        for doc_id, text, group in docs:
            inverted.add(doc_id, text, group=group)
            reference.add(doc_id, text)
            if tokenize(text):  # an all-stopword text is not indexed by either
                groups[doc_id] = group
        return inverted, reference, groups

    @staticmethod
    def _assert_parity(inverted, reference, groups) -> None:
        selections = [None, ("alice",), ("bob", "__global__"), ("alice", "bob", "carol", "__global__")]
        for query in _queries(seed=11):
            for selection in selections:
                ids = {d for d, g in groups.items() if selection is None or g in selection}
                for limit in (1, 5, 24):
                    assert inverted.search(query, limit=limit, groups=selection) == _expected(
                        reference, query, ids, limit
                    ), (query, selection, limit)

    def test_scores_and_order_match_exactly(self, pair) -> None:
        self._assert_parity(*pair)

    def test_parity_holds_through_removal_and_regrouping(self, pair) -> None:
        inverted, reference, groups = pair
        rng = random.Random(3)
        for doc_id in rng.sample(sorted(groups), 120):
            inverted.remove(doc_id)
            reference.remove(doc_id)
            del groups[doc_id]
        # Re-adding an id under a different group moves it; its old postings must not linger.
        for doc_id, text, _group in _corpus(seed=99, size=60):
            group = rng.choice(_GROUPS)
            inverted.add(doc_id, text, group=group)
            reference.add(doc_id, text)
            if tokenize(text):
                groups[doc_id] = group
            else:  # re-adding as empty text removes the id from both
                groups.pop(doc_id, None)

        assert inverted.size == reference.size == len(groups)
        assert inverted.average_length == reference.average_length
        self._assert_parity(inverted, reference, groups)

    def test_allowed_ids_still_restricts(self, pair) -> None:
        inverted, reference, groups = pair
        ids = set(sorted(groups)[::3])
        for query in _queries(seed=5):
            assert inverted.search(query, limit=10, allowed_ids=ids) == _expected(
                reference, query, ids, 10
            )


class TestShards:
    def test_a_query_reads_only_the_groups_it_names(self) -> None:
        index = BM25Index()
        index.add("a", "AURA-4419 reimbursement backlog", group="alice")
        index.add("b", "AURA-4419 reimbursement backlog", group="bob")

        assert [d for d, _ in index.search("AURA-4419", groups=["alice"])] == ["a"]
        assert [d for d, _ in index.search("AURA-4419", groups=["carol"])] == []
        assert [d for d, _ in index.search("AURA-4419", groups=[])] == []
        assert {d for d, _ in index.search("AURA-4419")} == {"a", "b"}

    def test_statistics_stay_corpus_wide(self) -> None:
        """Grouping prunes which documents are scored, never how: a term common in another
        tenant's documents is still common, exactly as in the single forward index."""
        grouped, flat = BM25Index(), BM25Index()
        for i in range(10):
            grouped.add(f"b{i}", "leave policy", group="bob")
            flat.add(f"b{i}", "leave policy")
        grouped.add("a", "leave rules", group="alice")
        flat.add("a", "leave rules")

        assert grouped.search("leave rules", groups=["alice"]) == flat.search(
            "leave rules", allowed_ids={"a"}
        )

    def test_removing_a_groups_last_document_drops_the_shard(self) -> None:
        index = BM25Index()
        index.add("a", "annual leave", group="alice")
        index.remove("a")
        index.remove("a")  # idempotent

        assert index.size == 0
        assert index.group_of("a") is None
        assert index._postings == {}  # noqa: SLF001 -- no empty shard left behind
        assert index.search("annual leave") == []

    def test_readding_moves_a_document_between_groups(self) -> None:
        index = BM25Index()
        index.add("a", "annual leave", group="alice")
        index.add("a", "annual leave", group="bob")

        assert index.group_of("a") == "bob"
        assert index.search("annual", groups=["alice"]) == []
        assert [d for d, _ in index.search("annual", groups=["bob"])] == ["a"]

    def test_group_membership_is_what_a_shard_refresh_diffs_against(self) -> None:
        """Membership includes documents with no indexable tokens, so a cross-worker refresh
        does not see them as missing and fetch them again every time (F-14)."""
        index = BM25Index()
        index.add("a", "annual leave", group="alice")
        index.add("t", "!!! ---", group="alice")  # nothing the tokenizer keeps
        index.add("b", "annual leave", group="bob")

        assert index.ids_in("alice") == {"a", "t"}
        assert index.size == 2  # the tokenless document does not count towards BM25's N
        index.remove("t")
        index.add("a", "annual leave", group="bob")
        assert index.ids_in("alice") == frozenset()
        assert index.ids_in("bob") == {"a", "b"}
        index.clear()
        assert index.ids_in("bob") == frozenset()


class TestEngineGroupsByOwner:
    async def test_no_identity_reads_only_global_chunks(self, knowledge) -> None:
        await knowledge.ingest(
            content="Ticket AURA-4419 is owned by Alice.", title="Alice notes",
            document_id=DocumentId("alice-notes"), owner_id=UserId("alice"),
        )
        await knowledge.ingest(
            content="Ticket AURA-4419 is tracked publicly.", title="Public notes",
            document_id=DocumentId("public-notes"), owner_id=None,
        )

        hits = await knowledge._lexical_search("AURA-4419", None, True)  # noqa: SLF001
        assert [h.split(":")[0] for h, _ in hits] == ["public-notes"]
        assert await knowledge._lexical_search("AURA-4419", None, False) == []  # noqa: SLF001

        own = await knowledge._lexical_search("AURA-4419", UserId("alice"), False)  # noqa: SLF001
        assert [h.split(":")[0] for h, _ in own] == ["alice-notes"]

    async def test_a_deleted_document_leaves_the_lexical_index(self, knowledge, user) -> None:
        await knowledge.ingest(
            content="Ticket AURA-4419 tracks the backlog.", title="Notes",
            document_id=DocumentId("notes"), owner_id=user,
        )
        assert knowledge.lexical_size == 1

        await knowledge.delete_document(DocumentId("notes"), owner_id=user)

        assert knowledge.lexical_size == 0
        assert await knowledge._lexical_search("AURA-4419", user, True) == []  # noqa: SLF001
