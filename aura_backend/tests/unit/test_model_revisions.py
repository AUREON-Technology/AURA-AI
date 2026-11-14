"""Every weight the platform loads by name can be pinned to a revision, and the pin is visible
(audit FSA-110 / B-03 / D-11 / E1-04).

The embedder (`bge-small`), the cross-encoder and the Whisper model loaded by name from the
Hugging Face Hub, so an upstream push changed what indexes, reranks and transcribes with no
record. Providers already had `*_MODEL_REVISION` (`ProviderCapabilities.model_revision`); these
three did not. Now each takes a revision from settings, hands it to its loader, and folds it into
the id it reports -- for the embedder that id *is* the index identity, so a new revision is a
new physical index built and promoted like any other embedder change (ADR-007), never a silent
rewrite of the live one. Whisper is also warmed at startup in production-like environments
instead of being fetched on the first transcription of every fresh container.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from aura_core.config.settings import KnowledgeSettings, LLMSettings, VoiceSettings


class TestTheSettingsCarryTheRevisions:
    def test_each_revision_is_read_from_its_variable(self, monkeypatch) -> None:
        monkeypatch.setenv("AURA_EMBEDDING_MODEL_REVISION", "emb-sha")
        monkeypatch.setenv("AURA_KNOWLEDGE_RERANK_MODEL_REVISION", "rr-sha")
        monkeypatch.setenv("AURA_VOICE_STT_MODEL_REVISION", "stt-sha")
        assert LLMSettings.from_env().embedding_model_revision == "emb-sha"
        assert KnowledgeSettings.from_env().rerank_model_revision == "rr-sha"
        assert VoiceSettings.from_env().stt_model_revision == "stt-sha"

    def test_unset_means_unpinned_as_before(self, monkeypatch) -> None:
        for key in ("AURA_EMBEDDING_MODEL_REVISION", "AURA_KNOWLEDGE_RERANK_MODEL_REVISION", "AURA_VOICE_STT_MODEL_REVISION"):
            monkeypatch.delenv(key, raising=False)
        assert LLMSettings.from_env().embedding_model_revision == ""
        assert KnowledgeSettings.from_env().rerank_model_revision == ""
        assert VoiceSettings.from_env().stt_model_revision == ""


class _FakeSentenceTransformer:
    calls: list[tuple[tuple, dict]] = []

    def __init__(self, *args, **kwargs) -> None:
        type(self).calls.append((args, kwargs))

    def get_sentence_embedding_dimension(self) -> int:
        return 384


class _FakeCrossEncoder:
    calls: list[tuple[tuple, dict]] = []

    def __init__(self, *args, **kwargs) -> None:
        type(self).calls.append((args, kwargs))


class _FakeWhisperModel:
    calls: list[tuple[tuple, dict]] = []

    def __init__(self, *args, **kwargs) -> None:
        type(self).calls.append((args, kwargs))


class TestTheEmbedderPinsAndReportsItsRevision:
    def test_the_revision_is_part_of_the_model_id_hence_of_the_index_identity(self) -> None:
        from aura_core.llm.embeddings import SentenceTransformerProvider

        assert SentenceTransformerProvider(model="BAAI/bge-small-en-v1.5", revision="abc123").model_id == (
            "BAAI/bge-small-en-v1.5@abc123"
        )
        assert SentenceTransformerProvider(model="BAAI/bge-small-en-v1.5").model_id == "BAAI/bge-small-en-v1.5", (
            "an unpinned embedder keeps its id, so existing indexes keep their identity"
        )

    async def test_the_loader_receives_the_revision(self, monkeypatch) -> None:
        sentence_transformers = pytest.importorskip("sentence_transformers")
        from aura_core.llm.embeddings import SentenceTransformerProvider

        _FakeSentenceTransformer.calls = []
        monkeypatch.setattr(sentence_transformers, "SentenceTransformer", _FakeSentenceTransformer)
        provider = SentenceTransformerProvider(model="BAAI/bge-small-en-v1.5", revision="abc123")
        await provider._ensure_model()
        (args, kwargs), = _FakeSentenceTransformer.calls
        assert args[0] == "BAAI/bge-small-en-v1.5" and kwargs.get("revision") == "abc123"


class TestTheRerankerPinsItsRevision:
    async def test_the_loader_receives_the_revision(self, monkeypatch) -> None:
        sentence_transformers = pytest.importorskip("sentence_transformers")
        from aura_core.knowledge.reranking import CrossEncoderReranker

        _FakeCrossEncoder.calls = []
        monkeypatch.setattr(sentence_transformers, "CrossEncoder", _FakeCrossEncoder)
        reranker = CrossEncoderReranker("cross-encoder/ms-marco-MiniLM-L-6-v2", revision="rr1")
        await reranker._ensure_model()
        (args, kwargs), = _FakeCrossEncoder.calls
        assert kwargs.get("revision") == "rr1"


class TestWhisperPinsWarmsAndReportsItsRevision:
    async def test_the_loader_receives_the_revision_and_the_name_shows_it(self, monkeypatch) -> None:
        faster_whisper = pytest.importorskip("faster_whisper")
        from aura_core.adapters.whisper_stt import FasterWhisperEngine

        _FakeWhisperModel.calls = []
        monkeypatch.setattr(faster_whisper, "WhisperModel", _FakeWhisperModel)
        engine = FasterWhisperEngine(model_size="tiny.en", revision="w1")
        assert engine.name == "faster-whisper-tiny.en@w1"
        await engine.warm()
        (args, kwargs), = _FakeWhisperModel.calls
        assert args[0] == "tiny.en" and kwargs.get("revision") == "w1"
        await engine.warm()
        assert len(_FakeWhisperModel.calls) == 1, "warming twice loads once"


class _WarmableStt:
    name = "warmable"

    def __init__(self) -> None:
        self.warmed = 0

    async def warm(self) -> None:
        self.warmed += 1

    async def transcribe(self, audio: bytes, *, language: str | None = None) -> str:
        return ""


class TestTheContainerWiresTheRevisions:
    def test_the_embedder_and_index_identity_carry_the_revision(self, monkeypatch) -> None:
        from aura_core.config import load_settings
        from aura_core.container import Container

        monkeypatch.setenv("AURA_EMBEDDING_PROVIDER", "sentence-transformers")
        monkeypatch.setenv("AURA_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
        monkeypatch.setenv("AURA_EMBEDDING_MODEL_REVISION", "abc123")
        container = Container(load_settings(refresh=True, validate=False))
        assert container.embedder.model_id.endswith("@abc123")
        assert container.index_manager.identity_for_current_embedder().embedding_model_id.endswith("@abc123")

    def test_the_stt_engine_carries_the_revision(self, monkeypatch) -> None:
        pytest.importorskip("faster_whisper")
        from aura_core.config import load_settings
        from aura_core.container import Container

        monkeypatch.setenv("AURA_VOICE_STT_MODEL_REVISION", "w1")
        container = Container(load_settings(refresh=True, validate=False))
        assert container.stt_engine is not None and container.stt_engine.name.endswith("@w1")

    async def test_startup_warms_whisper_in_a_production_like_environment(self, monkeypatch) -> None:
        from aura_core.config import load_settings
        from aura_core.container import Container

        monkeypatch.setenv("AURA_VOICE_STT_WARM_ON_STARTUP", "true")
        container = Container(load_settings(refresh=True, validate=False))
        stt = _WarmableStt()
        container.stt_engine = stt
        await container.startup()
        await asyncio.sleep(0)
        try:
            assert stt.warmed == 1
        finally:
            await container.shutdown()

    async def test_startup_does_not_warm_by_default_outside_production(self, monkeypatch) -> None:
        """The default suite must stay network-free: a local container never pulls weights."""
        from aura_core.config import load_settings
        from aura_core.container import Container

        monkeypatch.delenv("AURA_VOICE_STT_WARM_ON_STARTUP", raising=False)
        container = Container(load_settings(refresh=True, validate=False))
        stt = _WarmableStt()
        container.stt_engine = stt
        await container.startup()
        await asyncio.sleep(0)
        try:
            assert stt.warmed == 0
        finally:
            await container.shutdown()

    def test_readiness_reports_the_embedder_revision(self, monkeypatch) -> None:
        from aura_core.config import load_settings
        from aura_core.container import Container

        monkeypatch.setenv("AURA_EMBEDDING_PROVIDER", "sentence-transformers")
        monkeypatch.setenv("AURA_EMBEDDING_MODEL_REVISION", "abc123")
        settings = load_settings(refresh=True, validate=False)
        container = Container(settings)
        assert container.settings.llm.embedding_model_revision == "abc123"
