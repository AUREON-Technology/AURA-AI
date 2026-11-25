"""``SettingsValidator`` adapters: would a gateway boot with these variables? (ADR-037, stage 2)

``SubprocessSettingsValidator`` loads ``AuraSettings`` and runs ``validate()`` in a **child
process** whose environment is exactly the candidate: the same code the redeployed gateway will
run (a redeploy reuses the image), against the variables it will get. A child process because
settings are read from ``os.environ`` and cached, so trying a candidate in this process would
change the running gateway's own view. The child makes no network call: settings loading reads
the environment only.

It also repeats the gateway's one boot check that lives outside ``aura_core``
(``aura_service._require_jwt_secret``): staging and production refuse to start without a JWT
signing secret. That rule is copied, not imported, because ``aura_core`` may not import the
gateway module.

The reason it returns is the ``ConfigurationError`` message, which names settings, not values;
any candidate value that still appears in it is masked before it leaves this module.

``StaticSettingsValidator`` refuses environments by rule, for the default test suite.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Callable, Mapping

from ..domain.control import is_secret_variable

__all__ = ["StaticSettingsValidator", "SubprocessSettingsValidator"]

_BACKEND_ROOT = Path(__file__).resolve().parents[2]

_PROBE = r"""
import sys
try:
    from aura_core.config import load_settings
    settings = load_settings(refresh=True)
    if settings.environment.is_production_like:
        import os
        if not any((os.environ.get(n) or "").strip() for n in ("AURA_JWT_SECRET", "JWT_SECRET", "AURA_JWT_SECRET_FILE")):
            raise SystemExit("REFUSED: no JWT signing secret is configured (AURA_JWT_SECRET or JWT_SECRET); "
                             "staging/production refuses to start without one")
except SystemExit:
    raise
except Exception as exc:
    problems = (getattr(exc, "details", None) or {}).get("problems") or []
    text = str(exc) or type(exc).__name__
    raise SystemExit("REFUSED: " + text + (": " + "; ".join(map(str, problems)) if problems else ""))
print("OK")
"""

#: What the child keeps from this process: enough to find Python and its packages, nothing that
#: configures AURA (the candidate supplies all of that).
_KEEP = ("PATH", "HOME", "LANG", "LC_ALL", "PYTHONPATH", "VIRTUAL_ENV", "SSL_CERT_FILE", "TZ")
_TIMEOUT_S = 60.0
_MAX_REASON = 1500


def _mask(text: str, environ: Mapping[str, str]) -> str:
    secrets = {v for k, v in environ.items() if v and len(v) >= 4 and is_secret_variable(k, v)}
    for value in sorted(secrets, key=len, reverse=True):
        text = text.replace(value, "[value]")
    return text


class SubprocessSettingsValidator:
    def __init__(self, *, python: str | None = None, cwd: Path | None = None, timeout_s: float = _TIMEOUT_S) -> None:
        self._python = python or sys.executable
        self._cwd = cwd or _BACKEND_ROOT
        self._timeout_s = timeout_s

    async def validate(self, environ: Mapping[str, str]) -> str | None:
        env = {k: os.environ[k] for k in _KEEP if k in os.environ}
        env.update({str(k): str(v) for k, v in environ.items()})
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        process = await asyncio.create_subprocess_exec(
            self._python, "-c", _PROBE,
            cwd=str(self._cwd), env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(process.communicate(), timeout=self._timeout_s)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return "the settings check did not finish in time"
        if process.returncode == 0 and b"OK" in out:
            return None
        text = err.decode("utf-8", "replace")
        at = text.rfind("REFUSED: ")
        reason = " ".join(text[at + len("REFUSED: "):].split()) if at >= 0 else "the settings could not be loaded"
        return _mask(reason, environ)[:_MAX_REASON]


class StaticSettingsValidator:
    """Refuses an environment when any registered rule returns a reason."""

    def __init__(self) -> None:
        self._rules: list[Callable[[Mapping[str, str]], str | None]] = []
        self.calls: list[dict[str, str]] = []

    def refuse_when(self, rule: Callable[[Mapping[str, str]], str | None]) -> None:
        self._rules.append(rule)

    async def validate(self, environ: Mapping[str, str]) -> str | None:
        self.calls.append(dict(environ))
        for rule in self._rules:
            reason = rule(environ)
            if reason:
                return reason
        return None
