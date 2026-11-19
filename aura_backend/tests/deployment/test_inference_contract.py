"""Pins the Control Plane's and Inference Plane's duplicated model config against each other.

Task requirement (docs/deployment/architecture.md §6): the two planes necessarily each carry
their own copy of some model metadata (one process's environment can't read the other's), so a
mismatch has to be caught in CI rather than discovered as a production truncation bug. This
needs no Docker, network, or model -- it only parses the two config sources as text, matching
the rest of the default `pytest` suite's no-infrastructure constraint.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE = REPO_ROOT / "deploy" / "docker-compose.prod.yml"
ENTRYPOINT_FILE = REPO_ROOT / "deploy" / "spaces" / "inference-vllm" / "entrypoint.sh"


def _default_int(text: str, pattern: str) -> int:
    match = re.search(pattern, text)
    assert match, f"pattern {pattern!r} not found"
    return int(match.group(1))


def test_context_window_matches_vllm_max_model_len() -> None:
    """AURA_PROVIDER_HFSPACE_CONTEXT_WINDOW must match VLLM_MAX_MODEL_LEN's default.

    The Control Plane uses this to size prompts against what the Space can actually accept
    (ProviderCapabilities.context_window); the Space uses it to size vLLM's KV cache. If these
    drift apart, either the router over-fills the prompt (a runtime 400 from vLLM) or
    under-uses context the Space could actually serve.
    """
    compose_default = _default_int(
        COMPOSE_FILE.read_text(encoding="utf-8"),
        r"AURA_PROVIDER_HFSPACE_CONTEXT_WINDOW:-(\d+)",
    )
    entrypoint_default = _default_int(
        ENTRYPOINT_FILE.read_text(encoding="utf-8"),
        r'VLLM_MAX_MODEL_LEN:-(\d+)',
    )
    assert compose_default == entrypoint_default, (
        "Control Plane's AURA_PROVIDER_HFSPACE_CONTEXT_WINDOW default "
        f"({compose_default}) no longer matches the Inference Plane's VLLM_MAX_MODEL_LEN "
        f"default ({entrypoint_default}) -- update deploy/docker-compose.prod.yml or "
        "deploy/spaces/inference-vllm/entrypoint.sh so they agree, per "
        "docs/deployment/architecture.md §6."
    )


def test_entrypoint_still_refuses_to_boot_unauthenticated() -> None:
    """Guards the one security property docs/deployment/inference-plane.md §5 depends on."""
    text = ENTRYPOINT_FILE.read_text(encoding="utf-8")
    assert 'VLLM_API_KEY:-' not in text or "FATAL: VLLM_API_KEY is not set" in text
    assert "--api-key" in text


def test_vision_is_opt_in_and_never_widens_what_the_space_will_fetch() -> None:
    """ADR-031 on the Inference Plane: image input is off unless VLLM_MAX_IMAGES_PER_PROMPT is
    set, and turning it on never lets vLLM read local files, take per-request processor
    overrides or raw embeddings, or fetch remote URLs -- the gateway sends base64 only, so any of
    those would be attack surface with no caller."""
    text = ENTRYPOINT_FILE.read_text(encoding="utf-8")
    assert re.search(r'VLLM_MAX_IMAGES_PER_PROMPT:-0\}', text), "image input must default to off"
    for flag in ("--allowed-local-media-path", "--trust-request-mm-kwargs", "--enable-mm-embeds"):
        assert flag not in text.replace(f"(no {flag})", ""), f"{flag} must never be passed"
    images_block = text[text.index('if [ "$MAX_IMAGES" -gt 0 ]'):]
    assert "--allowed-media-domains media.invalid" in images_block
    assert "VLLM_MEDIA_URL_ALLOW_REDIRECTS=0" in images_block
    assert "--limit-mm-per-prompt" in images_block


def test_the_weights_can_be_pinned() -> None:
    """A Space serving whatever `main` is today answers with weights nobody reviewed."""
    text = ENTRYPOINT_FILE.read_text(encoding="utf-8")
    assert "VLLM_MODEL_REVISION" in text and '--revision "$MODEL_REVISION"' in text


def test_deploy_keeps_the_space_on_the_owner_gpu_and_awake() -> None:
    """SaaS P1-G (owner decision 2026-11-18): the sensitive-capable source is one L4, always on.

    The deploy asks Hugging Face for both on every run, so a Space someone set back to sleep,
    or to another GPU, in the Space's settings returns to the decided state on the next deploy.
    """
    import yaml

    workflow = REPO_ROOT / ".github" / "workflows" / "inference-plane-deploy.yml"
    deploy = yaml.safe_load(workflow.read_text())["jobs"]["deploy"]
    script = "\n".join(str(step.get("run", "")) for step in deploy["steps"])
    env = {k: v for step in deploy["steps"] for k, v in (step.get("env") or {}).items()}
    assert "request_space_hardware(" in script
    assert re.search(r"sleep_time\s*=\s*-1", script), "the Space must never sleep (-1)"
    assert "HF_SPACE_HARDWARE" in env and "'l4x1'" in env["HF_SPACE_HARDWARE"], env.get("HF_SPACE_HARDWARE")
