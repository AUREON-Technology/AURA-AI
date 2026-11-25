"""Lifecycle and capacity policy for durable engineer-agent workspaces.

Sits between the tool layer and two ports (``SandboxRunner``, ``WorkspaceStore``) because the
policy needs both and neither should know about the other: the runner does Docker mechanics and
has no idea which workspace was used least recently; the store knows that and cannot start a
container. Keeping the decision here also means it is testable on the in-memory substrate, which
is where every rule below is actually exercised.

**The capacity argument, since it is the reason this design looks the way it does.** A workspace's
content is its checkout on disk, not a running process. So between turns the container is stopped
rather than deleted, and a stopped container holds no memory. Peak memory is then
``max_running * memory_limit`` -- two containers at 512m on the defaults, about 1 GB -- and it
stays there whether the host holds two workspaces or twenty. Without that, four resident
workspaces would be ~2 GB of a single-VPS budget that is already ~16 GB against a 16 GB floor,
competing directly with the inference tier for the same RAM.

The corollary is that :meth:`WorkspaceManager.pause` is not an optimisation to be skipped when
convenient. If workspaces stayed hot, every number above is wrong.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from ..domain.engineering import AgentWorkspace, WorkspaceStatus
from ..domain.errors import ToolExecutionError
from ..domain.identifiers import UserId, WorkspaceId
from ..observability.logging import get_logger

__all__ = ["WorkspaceManager", "WorkspaceCapacityError", "OpenWorkspace", "available_memory_mb"]

logger = get_logger("tools.workspaces")

#: Read once per admission decision. Inside a Linux container this file reports the *host's*
#: memory, not a cgroup-scoped view, which is exactly what is wanted: the question being asked is
#: whether this host can afford another container, not what this one is allowed to use.
_MEMINFO = "/proc/meminfo"


class WorkspaceCapacityError(ToolExecutionError):
    """The host cannot afford another workspace right now.

    A refusal, deliberately, rather than starting one anyway and letting the kernel's OOM killer
    choose the victim -- on this deployment the largest process is the model server, so "start it
    and see" means an unrelated user's chat dies to free room for a build. A refused tool call is
    something the agent can report and a person can act on.
    """


@dataclass(frozen=True, slots=True)
class OpenWorkspace:
    """A workspace that is running right now, with the handle needed to work in it."""

    record: AgentWorkspace
    handle: Any
    evicted: tuple[str, ...] = ()
    """Repositories paused or removed to make room, so the tool layer can say so out loud rather
    than leaving a workspace to vanish unexplained between turns."""


def workspace_key(user_id: UserId, repo: str) -> str:
    """Stable id for a ``(user, repo)`` pair.

    Hashed rather than concatenated because it becomes a Docker label value and a directory name,
    and a repository name is user-supplied text containing slashes. Truncated to 32 hex
    characters: this is a name for local bookkeeping, not a security boundary, and collision at
    128 bits of a SHA-256 is not a real concern.
    """
    digest = hashlib.sha256(f"{user_id.value}\x1f{repo}".encode("utf-8")).hexdigest()
    return digest[:32]


def available_memory_mb(path: str = _MEMINFO) -> float | None:
    """Host memory available for a new container, in MiB, or ``None`` if it cannot be read.

    ``MemAvailable`` rather than ``MemFree``: free memory on a busy host is near zero because the
    page cache legitimately uses the rest, and refusing on that basis would refuse everything,
    forever. ``None`` on a platform without this file means "unknown", and the caller admits
    rather than blocks -- a missing metric is not evidence of pressure.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    return float(line.split()[1]) / 1024.0
    except (OSError, ValueError, IndexError):
        return None
    return None


def _parse_memory_limit_mb(limit: str) -> float:
    """Docker's memory-limit syntax ("512m", "2g", "1073741824") as MiB."""
    text = limit.strip().lower()
    if not text:
        return 0.0
    multipliers = {"b": 1 / (1024 * 1024), "k": 1 / 1024, "m": 1.0, "g": 1024.0}
    suffix = text[-1]
    try:
        if suffix in multipliers:
            return float(text[:-1]) * multipliers[suffix]
        return float(text) / (1024 * 1024)
    except ValueError:
        return 0.0


class WorkspaceManager:
    """Opens, pauses and reaps durable workspaces within a fixed capacity budget."""

    def __init__(
        self,
        *,
        runner: Any,
        store: Any,
        host_root: str = "",
        memory_limit: str = "512m",
        max_workspaces: int = 4,
        max_running: int = 2,
        ttl_hours: float = 72.0,
        hot_idle_s: float = 900.0,
        disk_quota_mb: int = 2048,
        min_free_memory_mb: int = 1024,
        clock: Any = None,
        metrics: Any = None,
    ) -> None:
        self._runner = runner
        self._store = store
        self._host_root = host_root
        self._memory_limit_mb = _parse_memory_limit_mb(memory_limit)
        self._max_workspaces = max_workspaces
        self._max_running = max_running
        self._ttl_hours = ttl_hours
        self._hot_idle_s = hot_idle_s
        self._disk_quota_mb = disk_quota_mb
        self._min_free_memory_mb = min_free_memory_mb
        self._clock = clock
        self._metrics = metrics
        # Serialises admission. Two turns opening a workspace concurrently would each see room
        # for one more and both take it, which is how a capacity ceiling stops being one.
        self._lock = asyncio.Lock()

    def _now(self) -> datetime:
        if self._clock is not None and hasattr(self._clock, "now"):
            return self._clock.now()
        return datetime.now(timezone.utc)

    async def open(self, user_id: UserId, repo: str) -> OpenWorkspace:
        """Resume or create this user's workspace for ``repo`` and return it running.

        Refuses with :class:`WorkspaceCapacityError` when the host cannot afford it. Never
        silently degrades: an agent that is told "no" can say so, whereas one handed a workspace
        that then dies mid-build cannot explain what happened.
        """
        async with self._lock:
            now = self._now()
            existing = await self._store.get(user_id, repo)
            evicted: list[str] = []

            if existing is not None and existing.status == WorkspaceStatus.HOT:
                # Already running: no admission decision to make, so do not spend one. Touching
                # is what keeps it out of the reaper's way.
                await self._store.touch(existing.id, at=now)
                handle = await self._runner.open_workspace(existing.id.value)
                return OpenWorkspace(record=existing, handle=handle)

            evicted += await self._make_room_to_run(now, keep=existing.id if existing else None)
            if existing is None:
                evicted += await self._make_room_to_exist(now)
            self._check_memory()

            if existing is None:
                record = AgentWorkspace(
                    id=WorkspaceId(workspace_key(user_id, repo)),
                    user_id=user_id,
                    repo=repo,
                    workspace_path=f"{self._host_root.rstrip('/')}/{workspace_key(user_id, repo)}"
                    if self._host_root
                    else workspace_key(user_id, repo),
                    status=WorkspaceStatus.HOT,
                    created_at=now,
                    last_active_at=now,
                )
                await self._store.upsert(record)
            else:
                record = existing
                await self._store.touch(record.id, at=now, status=WorkspaceStatus.HOT)
                record = await self._store.get_by_id(record.id) or record

            handle = await self._runner.open_workspace(record.id.value)
            await self._publish_gauges()
            return OpenWorkspace(record=record, handle=handle, evicted=tuple(evicted))

    def _check_memory(self) -> None:
        available = available_memory_mb()
        if available is None:
            return
        needed = self._min_free_memory_mb + self._memory_limit_mb
        if available < needed:
            refusal = WorkspaceCapacityError(
                "not enough free memory on the host to start another workspace "
                f"({available:.0f} MiB available, {needed:.0f} MiB needed). Nothing was started; "
                "try again once the host is less busy, or ask an operator to raise "
                "AURA_SANDBOX_MIN_FREE_MEMORY_MB if this is a false alarm.",
                tool="open_workspace",
            )
            # The message above is for the agent and the operator's logs. A person asking over
            # HTTP is told the reason without the host's memory figures or its configuration.
            refusal.public_message = (
                "the server does not have enough free memory to start another workspace right "
                "now. Nothing was started; try again shortly."
            )
            raise refusal

    async def _make_room_to_run(
        self, now: datetime, *, keep: WorkspaceId | None
    ) -> list[str]:
        """Pause least-recently-used hot workspaces until one more may run."""
        if self._max_running < 1:
            raise WorkspaceCapacityError(
                "no workspace may run on this deployment (the running limit is 0)",
                tool="open_workspace",
            )
        evicted: list[str] = []
        while True:
            hot = [
                w for w in await self._store.list_all()
                if w.status == WorkspaceStatus.HOT and (keep is None or w.id != keep)
            ]
            if len(hot) < self._max_running or not hot:
                return evicted
            # list_all is most-recently-active first, so the last entry is the LRU one.
            victim = hot[-1]
            await self._pause(victim, now)
            evicted.append(f"paused {victim.repo}")
            if len(evicted) > self._max_workspaces + self._max_running:
                # Pausing is not changing the store's view of the victim: a store bug, and a loop
                # that would otherwise spin forever holding the admission lock for every user.
                raise WorkspaceCapacityError(
                    "could not free a running slot: paused workspaces still read as running",
                    tool="open_workspace",
                )

    async def _make_room_to_exist(self, now: datetime) -> list[str]:
        """Destroy least-recently-used workspaces until one more may exist.

        This one deletes a real checkout, so it is deliberately the last resort and is always
        reported: a user whose workspace disappeared deserves to be told which, rather than
        discovering the branch they were working on is gone.
        """
        if self._max_workspaces < 1:
            raise WorkspaceCapacityError(
                "no workspace may exist on this deployment (the workspace limit is 0)",
                tool="open_workspace",
            )
        evicted: list[str] = []
        while True:
            everything = list(await self._store.list_all())
            if len(everything) < self._max_workspaces or not everything:
                return evicted
            victim = everything[-1]
            await self.destroy(victim)
            evicted.append(f"removed {victim.repo} (workspace limit reached)")
            if len(evicted) > self._max_workspaces + 1:
                raise WorkspaceCapacityError(
                    "could not make room for another workspace: removed ones still read as present",
                    tool="open_workspace",
                )

    async def _pause(self, workspace: AgentWorkspace, now: datetime) -> None:
        handle = await self._runner.open_workspace(workspace.id.value)
        await self._runner.pause_workspace(handle)
        # Status moves, last_active_at does not: pausing is housekeeping, not use, and refreshing
        # the timestamp here would keep an abandoned workspace alive forever.
        await self._store.touch(
            workspace.id, at=workspace.last_active_at, status=WorkspaceStatus.WARM
        )

    async def pause(self, workspace_id: WorkspaceId) -> None:
        """Stop a workspace, keeping its files. Called when a turn ends."""
        record = await self._store.get_by_id(workspace_id)
        if record is None or record.status != WorkspaceStatus.HOT:
            return
        await self._pause(record, self._now())
        await self._publish_gauges()

    async def list_for_user(self, user_id: UserId) -> Sequence[AgentWorkspace]:
        """This user's workspaces, most recently used first. Read-only; opens nothing."""
        return await self._store.list_for_user(user_id)

    async def get_for_user(self, user_id: UserId, workspace_id: str) -> AgentWorkspace | None:
        """One of this user's workspaces by id, or ``None`` -- the same answer for a workspace that
        is someone else's as for one that never existed (ownership is the lookup itself)."""
        for workspace in await self._store.list_for_user(user_id):
            if workspace.id.value == workspace_id:
                return workspace
        return None

    # -- lifecycle on request (ADR-043, roadmap row 89) -------------------------------------
    #
    # The same operations ``open``/``pause`` perform inside a turn, exposed so a person can start,
    # stop or restart *their own* workspace from the app. They add no new policy: ``start`` goes
    # through ``open``'s admission (the running limit, the host-memory guard), so a request cannot
    # take a slot or memory that a turn could not.

    async def start(self, workspace: AgentWorkspace) -> OpenWorkspace:
        """Run ``workspace`` (resuming its stopped container). Idempotent: already running is a
        touch. Refuses with :class:`WorkspaceCapacityError` when the host cannot afford it, and
        may pause the least-recently-used *other* workspace to make room (``evicted``)."""
        return await self.open(workspace.user_id, workspace.repo)

    async def stop(self, workspace: AgentWorkspace) -> bool:
        """Stop ``workspace``, keeping its files. ``True`` if it was running. Idempotent."""
        current = await self._store.get_by_id(workspace.id)
        if current is None or current.status != WorkspaceStatus.HOT:
            return False
        await self._pause(current, self._now())
        await self._publish_gauges()
        return True

    async def restart(self, workspace: AgentWorkspace) -> OpenWorkspace:
        """Stop then start. Admission applies to the start half, so a restart can be refused for
        capacity -- after which the workspace is stopped, not running (it is never half-started)."""
        await self.stop(workspace)
        return await self.start(workspace)

    async def stats(self, workspace: AgentWorkspace) -> dict[str, Any]:
        """A resource readout that never starts anything.

        State and disk always; CPU, memory and start time only when the runner reports them
        (``workspace_stats``), otherwise ``None`` with ``resources.reason`` saying why -- a
        missing number is never presented as zero."""
        from ..ports.coding import SandboxHandle

        current = await self._store.get_by_id(workspace.id) or workspace
        # A handle by key, not ``open_workspace``: that would start a stopped container just to
        # read a number about it.
        handle = SandboxHandle(task_id=current.id.value)
        running = current.status == WorkspaceStatus.HOT
        now = self._now()

        used_mb: float | None = None
        disk_reason: str | None = None
        measure = getattr(self._runner, "workspace_disk_usage_mb", None)
        if measure is None:
            disk_reason = "the runner does not report disk usage"
        else:
            try:
                used_mb = float(await measure(handle))
            except Exception:
                logger.debug("workspace disk usage unreadable", exc_info=True)
                disk_reason = "disk usage could not be read"
        quota = self._disk_quota_mb
        disk: dict[str, Any] = {
            "used_mb": round(used_mb, 2) if used_mb is not None else None,
            "quota_mb": quota,
            "percent": round(100.0 * used_mb / quota, 1) if used_mb is not None and quota else None,
            "over_quota": bool(used_mb is not None and used_mb >= quota),
            "reason": disk_reason,
        }

        reading: Mapping[str, Any] | None = None
        reason: str | None = None
        reporter = getattr(self._runner, "workspace_stats", None)
        if not running:
            reason = "the workspace is stopped; it holds no CPU or memory"
        elif reporter is None:
            reason = "the runner does not report CPU or memory"
        else:
            try:
                reading = await reporter(handle)
            except Exception:
                logger.debug("workspace stats unreadable", exc_info=True)
            if reading is None:
                reason = "the runner could not read this workspace's CPU and memory just now"

        def number(key: str) -> float | None:
            value = (reading or {}).get(key)
            return round(float(value), 2) if isinstance(value, (int, float)) else None

        if reading is not None and number("cpu_percent") is None and number("memory_mb") is None:
            reason = "the runner reported the workspace's start time but not its CPU or memory"

        started = (reading or {}).get("started_at")
        uptime: float | None = None
        if running and isinstance(started, str):
            try:
                begun = datetime.fromisoformat(started.replace("Z", "+00:00"))
                if begun.tzinfo is None:
                    begun = begun.replace(tzinfo=timezone.utc)
                uptime = max(0.0, (now - begun).total_seconds())
            except ValueError:
                uptime = None
        census = await self._store.list_all()
        return {
            "state": current.status,
            "running": running,
            "uptime_s": round(uptime, 1) if uptime is not None else None,
            "disk": disk,
            "cpu": {"percent": number("cpu_percent")},
            "memory": {
                "used_mb": number("memory_mb"),
                "limit_mb": number("memory_limit_mb") or (self._memory_limit_mb or None),
            },
            "resources": {"available": reading is not None, "reason": reason},
            "capacity": {
                "running": sum(1 for w in census if w.status == WorkspaceStatus.HOT),
                "max_running": self._max_running,
                "workspaces": len(census),
                "max_workspaces": self._max_workspaces,
            },
        }

    async def get(self, user_id: UserId, repo: str) -> AgentWorkspace | None:
        return await self._store.get(user_id, repo)

    async def touch(self, workspace_id: WorkspaceId) -> None:
        """Record activity. Every tool call does this; it is what the TTL measures."""
        await self._store.touch(workspace_id, at=self._now())

    async def destroy(self, workspace: AgentWorkspace) -> None:
        handle = await self._runner.open_workspace(workspace.id.value)
        await self._runner.destroy_workspace(handle)
        await self._store.delete(workspace.id)
        await self._publish_gauges()

    async def enforce_disk_quota(self, workspace: AgentWorkspace, *, tool: str = "run_command") -> None:
        """Raise when a checkout has outgrown its allowance.

        Checked before a write rather than after, so the quota bounds the disk rather than
        recording that it was already exceeded. ``tool`` names the tool the refusal is reported
        against (it used to say ``run_command`` whichever tool was refused).
        """
        handle = await self._runner.open_workspace(workspace.id.value)
        used = await self._runner.workspace_disk_usage_mb(handle)
        if used >= self._disk_quota_mb:
            raise WorkspaceCapacityError(
                f"workspace for {workspace.repo} has reached its {self._disk_quota_mb} MiB disk "
                f"quota ({used:.0f} MiB used). Remove build artefacts with run_command (for example "
                "`rm -rf node_modules`), or destroy and re-clone.",
                tool=tool,
            )

    async def expired(self, now: datetime | None = None) -> Sequence[AgentWorkspace]:
        """Workspaces idle past the TTL. The reaper's input."""
        cutoff = (now or self._now()) - timedelta(hours=self._ttl_hours)
        return await self._store.list_idle_since(cutoff)

    async def stale_hot(self, now: datetime | None = None) -> Sequence[AgentWorkspace]:
        """Hot workspaces whose turn plainly ended without pausing them.

        A crash, an abandoned turn, or a client that hung up leaves memory held by a container
        nobody is using; this is the sweep that returns it.
        """
        cutoff = (now or self._now()) - timedelta(seconds=self._hot_idle_s)
        return [
            w for w in await self._store.list_all()
            if w.status == WorkspaceStatus.HOT and w.last_active_at <= cutoff
        ]

    async def reap(self, *, run_sink: Any = None) -> dict[str, int]:
        """One full housekeeping pass. The thing standing between this feature and a full disk.

        Three jobs, in the order that frees the most for the least loss:

        1. **Pause** hot workspaces whose turn plainly ended (crash, abandoned turn, a client
           that hung up). Returns memory and loses nothing -- the checkout is untouched.
        2. **Delete** workspaces idle past the TTL. This is the only step that destroys work, so
           it is last and is driven purely by ``last_active_at``.
        3. **Reconcile** drift in both directions, so a container whose row vanished is not left
           holding disk that nothing will ever reclaim.

        Idempotent by construction: every step reads current state and acts on what it finds, so
        two overlapping runs (a beat restart, a manual trigger) converge rather than conflict.
        Errors on one workspace never abandon the rest -- the case this most needs to survive is
        a single wedged container, which is exactly when the sweep matters most.
        """
        summary = {"paused": 0, "expired": 0, "errors": 0}
        now = self._now()

        for workspace in await self.stale_hot(now):
            try:
                await self._pause(workspace, now)
                summary["paused"] += 1
            except Exception:
                summary["errors"] += 1
                logger.warning(
                    "could not pause an idle workspace", extra={"workspace": workspace.id.value},
                    exc_info=True,
                )

        for workspace in await self.expired(now):
            try:
                await self.destroy(workspace)
                summary["expired"] += 1
                if run_sink is not None:
                    # Expiry is a thing that happened to the user's work, so it belongs in the
                    # same history the rest of the agent's actions are visible in -- otherwise a
                    # workspace simply is not there one day with no record of why.
                    await self._record_expiry(run_sink, workspace, now)
            except Exception:
                summary["errors"] += 1
                logger.warning(
                    "could not reap an expired workspace",
                    extra={"workspace": workspace.id.value}, exc_info=True,
                )

        summary.update(await self.reconcile())
        if summary["paused"] or summary["expired"] or summary["errors"]:
            logger.info("workspace reap complete", extra=summary)
        return summary

    async def _record_expiry(self, run_sink: Any, workspace: AgentWorkspace, now: datetime) -> None:
        from ..domain.engineering import EngineerRun, EngineerRunStatus
        from ..domain.identifiers import EngineerRunId

        try:
            await run_sink.record(
                EngineerRun(
                    id=EngineerRunId(f"expiry-{workspace.id.value}-{int(now.timestamp())}"),
                    user_id=workspace.user_id,
                    turn_id="",
                    correlation_id="",
                    persona="engineer",
                    tool="destroy_workspace",
                    call_id=f"reaper-{workspace.id.value}",
                    status=EngineerRunStatus.SUCCEEDED,
                    created_at=now,
                    updated_at=now,
                    sandbox_transcript=(
                        f"Workspace for {workspace.repo} expired after "
                        f"{self._ttl_hours:.0f}h without activity and was removed."
                    ),
                )
            )
        except Exception:  # pragma: no cover - history must never block reclaiming disk
            logger.debug("could not record a workspace expiry", exc_info=True)

    async def reconcile(self) -> dict[str, int]:
        """Make the daemon's containers and the store's rows agree, in both directions.

        Runs at startup. Drift is normal rather than exceptional -- this process can be killed
        between creating a container and writing its row, and an operator can remove a container
        by hand -- so both directions resolve towards the store: a container with no row is
        unreachable and gets removed, and a row whose container is gone is downgraded to warm so
        the next open recreates it rather than failing.
        """
        summary = {"orphan_containers": 0, "missing_containers": 0}
        try:
            live = set(await self._runner.workspace_keys())
        except Exception:
            logger.warning("workspace reconciliation skipped: runner unavailable", exc_info=True)
            return summary

        rows = list(await self._store.list_all())
        known = {w.id.value for w in rows}

        for key in live - known:
            handle = await self._runner.open_workspace(key)
            await self._runner.destroy_workspace(handle)
            summary["orphan_containers"] += 1

        for row in rows:
            if row.id.value not in live and row.status == WorkspaceStatus.HOT:
                await self._store.touch(
                    row.id, at=row.last_active_at, status=WorkspaceStatus.WARM
                )
                summary["missing_containers"] += 1

        if summary["orphan_containers"] or summary["missing_containers"]:
            logger.info("workspace reconciliation complete", extra=summary)
        await self._publish_gauges()
        return summary

    async def _publish_gauges(self) -> None:
        """Publish the census. Never allowed to fail a turn -- this is observation, not policy."""
        setter = getattr(self._metrics, "set_agent_workspaces", None)
        if setter is None:
            return
        try:
            counts = {WorkspaceStatus.HOT: 0, WorkspaceStatus.WARM: 0}
            for workspace in await self._store.list_all():
                counts[workspace.status] = counts.get(workspace.status, 0) + 1
            setter(counts)
        except Exception:  # pragma: no cover - metrics must never break a turn
            logger.debug("workspace gauge publish failed", exc_info=True)
