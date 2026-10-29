"""nginx gives the non-streaming chat route at least the gateway's own worst case (F-22).

One ``POST /api/v3/chat`` can plan and then generate, each bounded by the LLM request timeout
after up to the admission-queue wait. Under the generic ``/api/`` location's 120 s, nginx
answered 504 while the gateway kept generating, and the client's retry with the same
Idempotency-Key got 409 until the first attempt finished.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COMPOSE = ROOT / "deploy" / "docker-compose.prod.yml"
NGINX = (
    ROOT / "deploy" / "nginx" / "templates" / "aura.conf.template",
    ROOT / "deploy" / "nginx" / "host-proxy.conf.example",
)


def _compose_default(name: str) -> float:
    match = re.search(rf"{name}:\s*\$\{{{name}:-(\d+(?:\.\d+)?)\}}", COMPOSE.read_text())
    assert match, f"{name} default not found in docker-compose.prod.yml"
    return float(match.group(1))


def _chat_read_timeouts(text: str) -> list[int]:
    blocks = re.findall(r"location = /api/v3/chat \{(.*?)\n    \}", text, flags=re.S)
    return [int(re.search(r"proxy_read_timeout (\d+)s;", b).group(1)) for b in blocks]


def test_every_server_block_gives_chat_its_worst_case() -> None:
    worst = 2 * (
        _compose_default("AURA_LLM_TIMEOUT_S")
        + _compose_default("AURA_LLM_ADMISSION_QUEUE_TIMEOUT_S")
    )
    template, example = (path.read_text() for path in NGINX)

    assert len(_chat_read_timeouts(template)) == 2, "prod and dev server blocks"
    assert len(_chat_read_timeouts(example)) == 1
    for timeout in _chat_read_timeouts(template) + _chat_read_timeouts(example):
        assert timeout >= worst, f"{timeout}s < plan + generate worst case {worst:.0f}s"
