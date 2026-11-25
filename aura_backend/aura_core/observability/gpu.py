"""GPU facts from ``nvidia-smi`` (ADR-055): a pure parser and the query it parses.

The Prometheus gauges ``gpu_memory_used_bytes`` / ``gpu_utilisation_ratio`` were declared "when a probe is
available" and no probe existed, and ``gpu_resource_scheduler`` was an in-memory ledger that never read a device.
This is the reading half, honestly scoped: it reports what the driver says about each device. It does **not**
allocate or reserve anything (see the ADR for what "allocation" could honestly mean here).

Stdlib only. The same parser serves the gateway's scrape-time probe (``adapters/gpu_probe.py``) and the
``gpu_status`` tool that runs on the user's desktop or in the sandbox.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["QUERY_ARGS", "GpuInfo", "parse_nvidia_smi", "as_dicts"]

#: argv (after ``nvidia-smi``) whose CSV output :func:`parse_nvidia_smi` reads.
QUERY_ARGS: tuple[str, ...] = (
    "--query-gpu=index,name,memory.total,memory.used,utilization.gpu,temperature.gpu",
    "--format=csv,noheader,nounits",
)
_MIB = 1024 * 1024


@dataclass(frozen=True)
class GpuInfo:
    index: int
    name: str
    memory_total_bytes: int | None
    memory_used_bytes: int | None
    utilisation: float | None  # 0..1
    temperature_c: int | None

    @property
    def memory_free_bytes(self) -> int | None:
        if self.memory_total_bytes is None or self.memory_used_bytes is None:
            return None
        return max(0, self.memory_total_bytes - self.memory_used_bytes)


def _number(text: str) -> float | None:
    """``[N/A]`` / ``[Not Supported]`` / blanks are "the driver does not know", not zero."""
    value = text.strip()
    if not value or value.startswith("[") or value.upper() in ("N/A", "NAN"):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_nvidia_smi(output: str) -> list[GpuInfo]:
    """GPUs from ``nvidia-smi --query-gpu=... --format=csv,noheader,nounits``.

    Raises ``ValueError`` for output that is not that CSV (an error message, a different format), so a failed
    probe is never read as "no GPUs" and "no GPUs" is only ever an empty list from a real, empty answer.
    """
    gpus: list[GpuInfo] = []
    for raw in output.splitlines():
        line = raw.strip()
        if not line:
            continue
        cells = [c.strip() for c in line.split(",")]
        if len(cells) != 6 or _number(cells[0]) is None:
            raise ValueError(f"not nvidia-smi query output: {line[:80]!r}")
        total, used, util, temp = _number(cells[2]), _number(cells[3]), _number(cells[4]), _number(cells[5])
        gpus.append(GpuInfo(
            index=int(float(cells[0])), name=cells[1][:100],
            memory_total_bytes=int(total * _MIB) if total is not None else None,
            memory_used_bytes=int(used * _MIB) if used is not None else None,
            utilisation=round(util / 100.0, 4) if util is not None else None,
            temperature_c=int(temp) if temp is not None else None))
    return gpus


def as_dicts(gpus: list[GpuInfo]) -> list[dict[str, Any]]:
    return [{
        "index": g.index, "name": g.name, "memory_total_mib": round(g.memory_total_bytes / _MIB) if g.memory_total_bytes is not None else None,
        "memory_used_mib": round(g.memory_used_bytes / _MIB) if g.memory_used_bytes is not None else None,
        "memory_free_mib": round(g.memory_free_bytes / _MIB) if g.memory_free_bytes is not None else None,
        "utilisation": g.utilisation, "temperature_c": g.temperature_c} for g in gpus]
