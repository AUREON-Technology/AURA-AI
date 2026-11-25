"""Scrape-time GPU probe: runs ``nvidia-smi`` on the machine the gateway runs on and sets the GPU gauges.

Read-only and bounded: a short timeout, a cached result (a scrape every few seconds must not fork a process each
time), and "no ``nvidia-smi`` / no driver / no GPU" is reported as unavailable, never as zero usage. On the gateway
hosts here (no GPU: the Railway control plane, the CPU VPS) it stays unavailable and the gauges stay unset, which is
the truth; it reports something on a host that really has a GPU (the inference host, or a gateway running with one).
"""

from __future__ import annotations

import asyncio
import shutil
import time
from dataclasses import dataclass, field
from typing import Any

from ..observability.gpu import QUERY_ARGS, GpuInfo, parse_nvidia_smi

__all__ = ["GpuProbeResult", "GpuProbe"]


@dataclass(frozen=True)
class GpuProbeResult:
    available: bool
    reason: str = ""
    gpus: list[GpuInfo] = field(default_factory=list)


class GpuProbe:
    def __init__(self, *, binary: str = "nvidia-smi", timeout_s: float = 3.0, ttl_s: float = 15.0) -> None:
        self._binary = binary
        self._timeout = timeout_s
        self._ttl = ttl_s
        self._cached: tuple[float, GpuProbeResult] | None = None
        self._lock = asyncio.Lock()

    async def probe(self) -> GpuProbeResult:
        async with self._lock:
            now = time.monotonic()
            if self._cached and now - self._cached[0] < self._ttl:
                return self._cached[1]
            result = await self._run()
            self._cached = (now, result)
            return result

    async def _run(self) -> GpuProbeResult:
        path = shutil.which(self._binary)
        if path is None:
            return GpuProbeResult(False, f"{self._binary} is not installed on this host")
        try:
            proc = await asyncio.create_subprocess_exec(
                path, *QUERY_ARGS, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
        except asyncio.TimeoutError:
            return GpuProbeResult(False, f"{self._binary} did not answer within {self._timeout:.0f}s")
        except OSError as exc:
            return GpuProbeResult(False, f"{self._binary} could not be started ({type(exc).__name__})")
        if proc.returncode != 0:
            return GpuProbeResult(False, (stderr or stdout).decode("utf-8", "replace").strip()[:200]
                                  or f"{self._binary} exited {proc.returncode}")
        try:
            return GpuProbeResult(True, "", parse_nvidia_smi(stdout.decode("utf-8", "replace")))
        except ValueError as exc:
            return GpuProbeResult(False, f"unreadable {self._binary} output: {exc}")

    async def refresh_gauges(self, metrics: Any) -> GpuProbeResult:
        """Set the per-device gauges from a fresh (or recently cached) probe; unset ones stay unset."""
        result = await self.probe()
        for gpu in result.gpus:
            device = str(gpu.index)
            if gpu.memory_used_bytes is not None:
                metrics.gpu_memory_used_bytes.labels(device=device).set(gpu.memory_used_bytes)
            if gpu.utilisation is not None:
                metrics.gpu_utilisation.labels(device=device).set(gpu.utilisation)
        return result
