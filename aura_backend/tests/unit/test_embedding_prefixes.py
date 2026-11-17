"""E5 prefixes for the multilingual embedder (docs/plans/arabic-language_plan.md, stage 4).

``intfloat/multilingual-e5-*`` is trained with ``query: `` / ``passage: `` in front of the text and
loses recall without them, so an E5 model gets them by default. The document prefix changes every
stored vector, so it is part of the index identity; a deployment without one keeps exactly the
identity it had, so nothing is rebuilt by accident.
"""

from __future__ import annotations

import pytest

from aura_core.config.settings import LLMSettings
from aura_core.llm.embeddings import SentenceTransformerProvider


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in ("AURA_EMBEDDING_MODEL", "AURA_EMBEDDING_QUERY_PREFIX", "AURA_EMBEDDING_DOCUMENT_PREFIX"):
        monkeypatch.delenv(key, raising=False)


def test_an_e5_model_gets_its_own_prefixes(monkeypatch) -> None:
    monkeypatch.setenv("AURA_EMBEDDING_MODEL", "intfloat/multilingual-e5-small")
    settings = LLMSettings.from_env()
    assert (settings.embedding_query_prefix, settings.embedding_document_prefix) == ("query: ", "passage: ")


@pytest.mark.parametrize("model", ["BAAI/bge-small-en-v1.5", "hash-embed-v1", "sentence-transformers/all-MiniLM-L6-v2"])
def test_other_models_get_none(monkeypatch, model: str) -> None:
    monkeypatch.setenv("AURA_EMBEDDING_MODEL", model)
    settings = LLMSettings.from_env()
    assert (settings.embedding_query_prefix, settings.embedding_document_prefix) == ("", "")


def test_an_explicit_value_wins_even_when_empty(monkeypatch) -> None:
    monkeypatch.setenv("AURA_EMBEDDING_MODEL", "intfloat/multilingual-e5-small")
    monkeypatch.setenv("AURA_EMBEDDING_DOCUMENT_PREFIX", "")
    monkeypatch.setenv("AURA_EMBEDDING_QUERY_PREFIX", "q: ")
    settings = LLMSettings.from_env()
    assert (settings.embedding_query_prefix, settings.embedding_document_prefix) == ("q: ", "")


def test_the_document_prefix_is_part_of_the_index_identity() -> None:
    plain = SentenceTransformerProvider(model="BAAI/bge-small-en-v1.5")
    assert plain.model_id == "BAAI/bge-small-en-v1.5"  # unchanged: no rebuild for existing installs
    e5 = SentenceTransformerProvider(model="intfloat/multilingual-e5-small", document_prefix="passage: ")
    other = SentenceTransformerProvider(model="intfloat/multilingual-e5-small", document_prefix="doc: ")
    assert e5.model_id.startswith("intfloat/multilingual-e5-small#doc-prefix-")
    assert e5.model_id != other.model_id
    # Only the document prefix changes stored vectors; a query prefix does not.
    assert SentenceTransformerProvider(model="m", query_prefix="query: ").model_id == "m"
