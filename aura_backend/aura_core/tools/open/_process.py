"""Child-process helpers shared by the open tools that run project code.

Two things the bare ``subprocess.run`` calls these classes started with got wrong:

* The child inherited the gateway's whole environment -- provider API keys, the JWT secret,
  database URLs -- and the code it runs (a generated or user-supplied pytest file, the module under
  test) is exactly the code that must not be able to read them. :func:`scrubbed_env` passes a short
  allowlist instead.
* A timeout surfaced as an exception from some call sites and not others.

Stdlib only: this module sits in the ``tools`` engine layer.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence

#: Variables a Python/pytest child needs to start at all. Everything else is dropped.
_ENV_ALLOWLIST: tuple[str, ...] = (
    "PATH",
    "SYSTEMROOT",
    "SystemRoot",
    "WINDIR",
    "COMSPEC",
    "TEMP",
    "TMP",
    "TMPDIR",
    "LANG",
    "LC_ALL",
)


def scrubbed_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """A minimal environment for a child that runs untrusted project code."""
    env = {k: v for k in _ENV_ALLOWLIST if (v := os.environ.get(k)) is not None}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if extra:
        env.update(extra)
    return env


def run_bounded(
    argv: Sequence[str],
    *,
    cwd: str | os.PathLike[str] | None,
    timeout_s: float,
    extra_env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ``argv`` (never through a shell) with a scrubbed environment and a hard timeout.

    ``subprocess.TimeoutExpired`` and ``FileNotFoundError`` propagate: callers translate them into
    the result shape their own contract promises.
    """
    return subprocess.run(
        list(argv),
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        env=scrubbed_env(extra_env),
    )


#: pytest children run user code; third-party plugins auto-loading into them is surface we do not need.
PYTEST_ENV: dict[str, str] = {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
