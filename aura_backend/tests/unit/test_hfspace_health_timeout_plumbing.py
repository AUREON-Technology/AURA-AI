"""A setting the deployment never passes through is not a setting.

`AURA_PROVIDER_<NAME>_HEALTH_TIMEOUT_S` exists because a Hugging Face Space scales to zero and
the 5-second liveness probe cannot observe it waking (EV-28: 20 consecutive probe failures at a
flat ~5150 ms, then 11 successes once the Space had started). Adding the knob to
`ProviderConfig` was the first half. The second half is that `deploy/docker-compose.prod.yml`
enumerates every `AURA_PROVIDER_HFSPACE_*` variable explicitly -- Compose does not forward an
environment variable the service did not name -- so a knob added to the code and not to that
list is silently inert on the one deploy target that needs it.

That is the failure this file guards, and it is the same one
`test_health_probe_timeout.py::test_build_provider_threads_it_through` guards one layer down:
a setting that reads as configured and does nothing is worse than no setting, because the
operator believes the problem is addressed.

Scope note: this covers the VPS Compose target only. The Railway Control Plane takes its
environment from the Railway dashboard, which no file in this repository can assert against --
setting it there stays an owner action, recorded in the readiness matrix rather than here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_COMPOSE = Path(__file__).resolve().parents[3] / "deploy" / "docker-compose.prod.yml"

_HFSPACE_VAR = re.compile(r"^\s+(AURA_PROVIDER_HFSPACE_[A-Z0-9_]+):", re.M)


@pytest.fixture(scope="module")
def compose_text() -> str:
    assert _COMPOSE.is_file(), f"{_COMPOSE} is missing; this check would pass vacuously"
    return _COMPOSE.read_text(encoding="utf-8")


def test_the_health_timeout_is_passed_through(compose_text: str) -> None:
    declared = set(_HFSPACE_VAR.findall(compose_text))
    assert declared, "no AURA_PROVIDER_HFSPACE_* variables found; has the file been restructured?"
    assert "AURA_PROVIDER_HFSPACE_HEALTH_TIMEOUT_S" in declared, (
        "the Space's liveness-probe timeout is not forwarded by docker-compose.prod.yml, so "
        "setting it in .env would do nothing. Compose only forwards variables a service names."
    )


def test_every_service_that_configures_the_space_also_sets_it(compose_text: str) -> None:
    """Both the gateway and the worker construct providers, so both need it.

    Counted rather than merely present: a variable added to one service's env block and not the
    other produces a deployment where the same provider probes with two different deadlines
    depending on which container asked.
    """
    request_timeouts = compose_text.count("AURA_PROVIDER_HFSPACE_TIMEOUT_S:")
    health_timeouts = compose_text.count("AURA_PROVIDER_HFSPACE_HEALTH_TIMEOUT_S:")

    assert health_timeouts == request_timeouts, (
        f"{request_timeouts} service(s) configure the Space's request timeout but "
        f"{health_timeouts} configure its health timeout -- they must move together"
    )


def test_the_default_is_longer_than_the_probes_own_default(compose_text: str) -> None:
    """5s is the code default and is correct for an always-on endpoint. This target's provider
    is not always on, which is the entire reason the knob exists, so a pass-through that
    defaulted back to 5 would forward the setting and still not fix anything."""
    match = re.search(
        r"AURA_PROVIDER_HFSPACE_HEALTH_TIMEOUT_S:\s*\$\{AURA_PROVIDER_HFSPACE_HEALTH_TIMEOUT_S:-(\d+(?:\.\d+)?)\}",
        compose_text,
    )
    assert match, "could not read the forwarded default; restate this check against the new shape"
    assert float(match.group(1)) > 5.0, (
        "the Compose default is no longer than the code default, so the VPS target still cannot "
        "observe a cold Space waking"
    )
