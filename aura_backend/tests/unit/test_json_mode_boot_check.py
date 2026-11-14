"""A deployment where no provider declares JSON mode is told so at boot (audit FSA-092).

``ProviderRouter`` excludes every provider left at the default ``SUPPORTS_JSON_MODE=false`` from a
structured-output request (``no_json_mode`` in the routing decision), which is right -- sending
``response_format`` to a model that ignores it yields unparseable output. But with every slot at
the default, the first planner call that asks for a schema finds no eligible provider, and nothing
said so before that moment.
"""

from __future__ import annotations

import logging

from aura_core.config.settings import LLMSettings, ProviderConfig
from aura_core.llm.registry import build_providers


def _settings(*configs: ProviderConfig) -> LLMSettings:
    return LLMSettings(providers=tuple(configs))


def test_no_json_capable_provider_is_a_boot_warning(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="aura.llm"):
        build_providers(_settings(ProviderConfig(name="echo", kind="echo", model="echo")))
    assert any("SUPPORTS_JSON_MODE" in record.getMessage() for record in caplog.records), (
        [r.getMessage() for r in caplog.records]
    )


def test_one_json_capable_provider_is_enough(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="aura.llm"):
        build_providers(_settings(
            ProviderConfig(name="echo", kind="echo", model="echo"),
            ProviderConfig(name="json", kind="echo", model="echo", supports_json_mode=True),
        ))
    assert not any("SUPPORTS_JSON_MODE" in record.getMessage() for record in caplog.records)
