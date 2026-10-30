"""ADR-031: an image request reaches only an image-capable, non-third-party provider, as base64.

Covers the provider half of image understanding: the router's refusal rules, the exact wire
shapes for the OpenAI-compatible (vLLM, llama.cpp, Ollama ``/v1``) and native Ollama adapters,
and the guarantee that a text-only request is byte-identical to one made before images existed.
"""

from __future__ import annotations

import base64
import json

import pytest

from aura_core.domain import CompletionRequest, Conversation, Message, ProviderCapabilities
from aura_core.domain.errors import AllProvidersFailedError
from aura_core.domain.images import ImageInput
from aura_core.domain.llm import DataClassification, Modality, ModelPool, SamplingParams
from aura_core.llm import ProviderRouter
from aura_core.llm.providers.echo import EchoProvider
from aura_core.llm.providers.ollama import OllamaProvider
from aura_core.llm.providers.openai_compatible import OpenAICompatibleProvider

PNG = ImageInput(media_type="image/png", data=b"\x89PNG\r\n\x1a\nfake-png", width=640, height=480)
JPEG = ImageInput(media_type="image/jpeg", data=b"\xff\xd8\xfffake-jpeg", width=32, height=32)


def _caps(**overrides) -> ProviderCapabilities:
    base = dict(model_id="m", context_window=8192, max_output_tokens=512, is_local=True)
    base.update(overrides)
    return ProviderCapabilities(**base)


def _request(*images: ImageInput, **overrides) -> CompletionRequest:
    base = dict(
        conversation=Conversation.of([Message.system("sys"), Message.user("What is this?")]),
        sampling=SamplingParams(temperature=0.0, max_output_tokens=64),
        images=images,
        data_classification=DataClassification.SENSITIVE,
    )
    base.update(overrides)
    return CompletionRequest(**base)


class TestRouting:
    def test_images_alone_exclude_a_text_only_model_even_without_a_hint(self) -> None:
        text = EchoProvider(name="text", capabilities=_caps())
        vision = EchoProvider(name="vision", capabilities=_caps(
            pools=(ModelPool.VISION,), modalities=(Modality.TEXT, Modality.IMAGE)))
        router = ProviderRouter([text, vision])

        plan = router.plan(_request(PNG))

        assert plan.candidates == ("vision",)
        assert plan.excluded["text"] == "modality_unsupported"
        # A text request is untouched by the image rules.
        assert set(router.plan(_request()).candidates) == {"text", "vision"}

    def test_a_third_party_provider_never_receives_an_image(self) -> None:
        # Even one misconfigured to accept SENSITIVE content and declaring the image modality.
        hosted = EchoProvider(name="hosted", capabilities=_caps(
            modalities=(Modality.TEXT, Modality.IMAGE), third_party=True, is_local=False,
            max_data_classification=DataClassification.SENSITIVE))
        router = ProviderRouter([hosted])

        plan = router.plan(_request(PNG))

        assert plan.candidates == ()
        assert plan.excluded["hosted"] == "third_party_image"

    async def test_no_eligible_provider_is_an_error_not_a_text_answer(self) -> None:
        router = ProviderRouter([EchoProvider(name="text", capabilities=_caps())])
        with pytest.raises(AllProvidersFailedError):
            await router.complete(_request(PNG))

    def test_image_tokens_count_against_the_context_window(self) -> None:
        huge = ImageInput(media_type="image/png", data=b"x", width=4096, height=4096)
        small_window = EchoProvider(name="small", capabilities=_caps(
            context_window=2048, max_output_tokens=256, modalities=(Modality.TEXT, Modality.IMAGE)))
        router = ProviderRouter([small_window])

        assert router.plan(_request(huge)).excluded["small"] == "context_window_too_small"
        assert router.plan(_request(JPEG)).candidates == ("small",)


class TestWireFormat:
    def _openai(self) -> OpenAICompatibleProvider:
        return OpenAICompatibleProvider(name="vllm", base_url="http://vllm:8000/v1", model="qwen-vl")

    def test_openai_compatible_sends_data_urls_as_content_parts(self) -> None:
        payload = self._openai()._payload(_request(PNG, JPEG), stream=False)

        user = [m for m in payload["messages"] if m["role"] == "user"][-1]
        assert user["content"][0] == {"type": "text", "text": "What is this?"}
        urls = [part["image_url"]["url"] for part in user["content"][1:]]
        assert urls == [
            "data:image/png;base64," + base64.b64encode(PNG.data).decode(),
            "data:image/jpeg;base64," + base64.b64encode(JPEG.data).decode(),
        ]
        # Never a URL a model server would fetch (SSRF), and the system message is untouched.
        assert not any(u.startswith("http") for u in urls)
        assert payload["messages"][0] == {"role": "system", "content": "sys"}

    def test_ollama_native_sends_bare_base64_beside_the_text(self) -> None:
        payload = OllamaProvider(model="qwen3-vl:4b")._payload(_request(PNG), stream=False)

        user = [m for m in payload["messages"] if m["role"] == "user"][-1]
        assert user["content"] == "What is this?"
        assert user["images"] == [base64.b64encode(PNG.data).decode()]

    def test_a_text_request_is_byte_identical_to_before_images_existed(self) -> None:
        provider = self._openai()
        text_only = provider._payload(_request(), stream=False)
        assert json.dumps(text_only, sort_keys=True) == json.dumps(
            provider._payload(_request(images=()), stream=False), sort_keys=True
        )
        assert all(isinstance(m["content"], str) for m in text_only["messages"])
        ollama = OllamaProvider(model="m")._payload(_request(), stream=False)
        assert all("images" not in m for m in ollama["messages"])


class TestEcho:
    async def test_echo_describes_the_images_it_was_shown(self) -> None:
        response = await EchoProvider().complete(_request(PNG))
        assert f"[image 1: 640x480 image/png sha256:{PNG.sha256[:12]}]" in response.text
