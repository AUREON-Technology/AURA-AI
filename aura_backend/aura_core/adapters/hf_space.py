"""The inference Space on Hugging Face, through ``huggingface_hub`` (ADR-037, stage 3).

``HfSpaceHost`` implements ``SpaceApi`` with ``HfApi`` (already locked, imported inside each
call) on a worker thread, because the Hub client is synchronous. The token is read from the
vault on every call (``token_source``) and passed per call, never set on a shared client or in
the environment, and never logged.

Every operation was checked against ``huggingface_hub`` 1.28's own signatures:
``get_space_runtime``, ``get_space_variables`` / ``add_space_variable`` /
``delete_space_variable``, ``get_space_secrets`` (names and dates only: the Hub never returns a
secret's value) / ``add_space_secret`` / ``delete_space_secret``, ``pause_space``,
``restart_space(factory_reboot=)``, ``request_space_hardware``, ``set_space_sleep_time`` and
``fetch_space_logs`` (no-follow, which drains what is buffered and returns).

Vendor messages are never passed through: a Hub error can echo the request, and so a value.

``InMemorySpace`` is the same contract with real state, for the default test suite and a local
gateway.
"""

from __future__ import annotations

import asyncio
import itertools
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping, Sequence

from ..domain.errors import DependencyUnavailableError, ValidationError
from ..ports.control_plane import DeploymentHealth, SpaceSecretInfo, SpaceStatus

__all__ = ["HfSpaceHost", "InMemorySpace", "space_health_of", "SPACE_LOG_KINDS"]

SPACE_LOG_KINDS = ("run", "build")

_FAILED = {"BUILD_ERROR", "RUNTIME_ERROR", "CONFIG_ERROR", "NO_APP_FILE", "DELETING"}
_HEALTHY = {"RUNNING"}
#: Paused or stopped by someone: the Space no longer runs the change, so it is not judged.
_SUPERSEDED = {"PAUSED", "STOPPED"}


def space_health_of(stage: str | None) -> DeploymentHealth:
    """BUILDING, RUNNING_BUILDING, APP_STARTING, RUNNING_APP_STARTING: still pending."""
    word = (stage or "").upper()
    if word in _HEALTHY:
        return DeploymentHealth.HEALTHY
    if word in _FAILED:
        return DeploymentHealth.FAILED
    if word in _SUPERSEDED:
        return DeploymentHealth.SUPERSEDED
    return DeploymentHealth.PENDING


def _value(item: Any) -> str:
    return str(getattr(item, "value", item) or "")


class HfSpaceHost:
    """``token_source`` returns the stored HF token (``ControlService.token``)."""

    def __init__(self, *, token_source: Callable[[], Awaitable[str]]) -> None:
        self._token_source = token_source

    async def _run(self, method: str, *args: Any, **kwargs: Any) -> Any:
        token = await self._token_source()

        def call() -> Any:
            from huggingface_hub import HfApi

            return getattr(HfApi(), method)(*args, token=token, **kwargs)

        try:
            return await asyncio.to_thread(call)
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (401, 403):
                raise ValidationError(
                    "Hugging Face refused the token for this Space", details={"reason": "hf_token_refused"},
                ) from exc
            if status == 404:
                raise ValidationError(
                    "Hugging Face has no such Space for this token", details={"reason": "space_not_found"},
                ) from exc
            if status is not None and 400 <= status < 500:
                raise ValidationError(
                    f"Hugging Face refused the request (HTTP {status})", details={"reason": "hf_refused"},
                ) from exc
            raise DependencyUnavailableError(
                "Hugging Face could not be reached", dependency="huggingface",
                details={"error": type(exc).__name__},
            ) from exc

    async def status(self, space_id: str) -> SpaceStatus:
        runtime = await self._run("get_space_runtime", space_id)
        return SpaceStatus(
            stage=_value(runtime.stage),
            hardware=_value(runtime.hardware) or None,
            requested_hardware=_value(runtime.requested_hardware) or None,
            sleep_time=runtime.sleep_time,
        )

    async def variables(self, space_id: str) -> Mapping[str, str]:
        found = await self._run("get_space_variables", space_id)
        return {str(k): str(getattr(v, "value", "") or "") for k, v in (found or {}).items()}

    async def set_variables(self, space_id: str, values: Mapping[str, str | None]) -> None:
        for name, value in values.items():
            if value is None:
                await self._run("delete_space_variable", space_id, name)
            else:
                await self._run("add_space_variable", space_id, name, value)

    async def secrets(self, space_id: str) -> Sequence[SpaceSecretInfo]:
        found = await self._run("get_space_secrets", space_id)
        return sorted(
            (SpaceSecretInfo(name=str(k), updated_at=getattr(v, "updated_at", None)) for k, v in (found or {}).items()),
            key=lambda s: s.name,
        )

    async def set_secrets(self, space_id: str, values: Mapping[str, str | None]) -> None:
        for name, value in values.items():
            if value is None:
                await self._run("delete_space_secret", space_id, name)
            else:
                await self._run("add_space_secret", space_id, name, value)

    async def pause(self, space_id: str) -> None:
        await self._run("pause_space", space_id)

    async def restart(self, space_id: str, *, factory: bool = False) -> None:
        await self._run("restart_space", space_id, factory_reboot=factory)

    async def request_hardware(self, space_id: str, hardware: str) -> None:
        await self._run("request_space_hardware", space_id, hardware)

    async def set_sleep_time(self, space_id: str, seconds: int) -> None:
        await self._run("set_space_sleep_time", space_id, seconds)

    async def logs(self, space_id: str, *, kind: str = "run", limit: int = 200) -> Sequence[str]:
        if kind not in SPACE_LOG_KINDS:
            raise ValidationError("Space logs are 'run' or 'build'")
        token = await self._token_source()

        def drain() -> list[str]:
            from huggingface_hub import HfApi

            lines: list[str] = []
            for line in HfApi().fetch_space_logs(space_id, build=kind == "build", follow=False, token=token):
                lines.append(str(line).rstrip("\n"))
                if len(lines) > limit * 4:
                    del lines[: len(lines) - limit]
            return lines[-limit:]

        try:
            lines = await asyncio.to_thread(drain)
        except Exception as exc:
            raise DependencyUnavailableError(
                "the Space's logs could not be read", dependency="huggingface", details={"error": type(exc).__name__},
            ) from exc
        # The HF token itself must never come back, even if a log line printed it.
        return [line.replace(token, "[redacted]") if token else line for line in lines]

    # -- DeploymentPlatform: what the reconciler needs -------------------------------------------
    async def deployment_health(self, target: str, deployment_id: str) -> DeploymentHealth:
        return space_health_of((await self.status(target)).stage)

    async def rollback(self, target: str, to_deployment_id: str) -> str:
        await self.restart(target)
        return f"space-restart@{datetime.now(timezone.utc).isoformat()}"

    async def restore_variables(self, target: str, values: Mapping[str, str | None]) -> None:
        await self.set_variables(target, values)


class InMemorySpace:
    """A Space with real state. A test moves the stage with ``set_stage``."""

    def __init__(self) -> None:
        self.stage: dict[str, str] = {}
        self.hardware: dict[str, str] = {}
        self.sleep: dict[str, int | None] = {}
        self.vars: dict[str, dict[str, str]] = {}
        self.secret_values: dict[str, dict[str, str]] = {}
        self.secret_dates: dict[str, dict[str, datetime]] = {}
        self.log_lines: dict[str, list[str]] = {}
        self.events: list[tuple[str, str]] = []
        self._ids = itertools.count(1)

    def add_space(self, space_id: str, *, stage: str = "RUNNING", hardware: str = "l4x1",
                  sleep_time: int | None = 3600, variables: Mapping[str, str] | None = None,
                  secrets: Mapping[str, str] | None = None) -> None:
        self.stage[space_id] = stage
        self.hardware[space_id] = hardware
        self.sleep[space_id] = sleep_time
        self.vars[space_id] = dict(variables or {})
        self.secret_values[space_id] = dict(secrets or {})
        now = datetime.now(timezone.utc)
        self.secret_dates[space_id] = {k: now for k in (secrets or {})}

    def set_stage(self, space_id: str, stage: str) -> None:
        self.stage[space_id] = stage

    def _space(self, space_id: str) -> None:
        if space_id not in self.stage:
            raise ValidationError("Hugging Face has no such Space for this token", details={"reason": "space_not_found"})

    def _restarted(self, space_id: str, why: str) -> None:
        self.events.append((why, space_id))
        self.stage[space_id] = "APP_STARTING"

    async def status(self, space_id: str) -> SpaceStatus:
        self._space(space_id)
        return SpaceStatus(stage=self.stage[space_id], hardware=self.hardware[space_id],
                           requested_hardware=self.hardware[space_id], sleep_time=self.sleep[space_id])

    async def variables(self, space_id: str) -> Mapping[str, str]:
        self._space(space_id)
        return dict(self.vars[space_id])

    async def set_variables(self, space_id: str, values: Mapping[str, str | None]) -> None:
        self._space(space_id)
        for name, value in values.items():
            if value is None:
                self.vars[space_id].pop(name, None)
            else:
                self.vars[space_id][name] = value
        self._restarted(space_id, "variables")

    async def secrets(self, space_id: str) -> Sequence[SpaceSecretInfo]:
        self._space(space_id)
        return [SpaceSecretInfo(name=k, updated_at=self.secret_dates[space_id].get(k))
                for k in sorted(self.secret_values[space_id])]

    async def set_secrets(self, space_id: str, values: Mapping[str, str | None]) -> None:
        self._space(space_id)
        for name, value in values.items():
            if value is None:
                self.secret_values[space_id].pop(name, None)
                self.secret_dates[space_id].pop(name, None)
            else:
                self.secret_values[space_id][name] = value
                self.secret_dates[space_id][name] = datetime.now(timezone.utc)
        self._restarted(space_id, "secrets")

    async def pause(self, space_id: str) -> None:
        self._space(space_id)
        self.events.append(("pause", space_id))
        self.stage[space_id] = "PAUSED"

    async def restart(self, space_id: str, *, factory: bool = False) -> None:
        self._space(space_id)
        self._restarted(space_id, "factory" if factory else "restart")

    async def request_hardware(self, space_id: str, hardware: str) -> None:
        self._space(space_id)
        self.hardware[space_id] = hardware
        self._restarted(space_id, f"hardware:{hardware}")

    async def set_sleep_time(self, space_id: str, seconds: int) -> None:
        self._space(space_id)
        self.sleep[space_id] = seconds
        self.events.append((f"sleep:{seconds}", space_id))

    async def logs(self, space_id: str, *, kind: str = "run", limit: int = 200) -> Sequence[str]:
        if kind not in SPACE_LOG_KINDS:
            raise ValidationError("Space logs are 'run' or 'build'")
        self._space(space_id)
        return list(self.log_lines.get(f"{kind}:{space_id}", []))[-limit:]

    async def deployment_health(self, target: str, deployment_id: str) -> DeploymentHealth:
        return space_health_of(self.stage.get(target))

    async def rollback(self, target: str, to_deployment_id: str) -> str:
        await self.restart(target)
        return f"space-restart-{next(self._ids)}"

    async def restore_variables(self, target: str, values: Mapping[str, str | None]) -> None:
        await self.set_variables(target, values)
