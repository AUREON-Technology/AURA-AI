"""Superseded -- deliberately NOT registered as a tool (ADR-036). The platform already has the real
signals: `observability/metrics.py` (`gpu_memory_used_bytes`, `gpu_utilisation`) and
`adapters/ml/device.py`. This class is a bookkeeping ledger -- it never reads a device, has no
queue or priority, and has no locking -- so a scheduler that says 'allocated' says nothing about
the GPU.

Kept for reference only: nothing under ``aura_core`` may import it
(``tests/architecture/test_superseded_open_tools_unused.py``).
"""

from typing import Any, Dict


class GpuResourceScheduler:
    """Scheduler for tracking VRAM capacity, allocating GPU memory to jobs, and releasing leases."""

    def __init__(self, total_vram_gb: float = 12.0, device_name: str = "gpu_0") -> None:
        self.total_vram_gb = max(0.0, float(total_vram_gb))
        self.device_name = device_name
        self.allocations: Dict[str, float] = {}

    def available_vram_gb(self) -> float:
        """Compute currently free VRAM in GB."""
        used = sum(self.allocations.values())
        return round(max(0.0, self.total_vram_gb - used), 4)

    def request_allocation(self, job_id: str, requested_vram_gb: float) -> Dict[str, Any]:
        """Allocate VRAM for a job if sufficient capacity is available."""
        req = float(requested_vram_gb)
        free = self.available_vram_gb()
        if req <= 0:
            return {"allocated": False, "job_id": job_id, "error": "Requested VRAM must be positive."}

        if job_id in self.allocations:
            return {
                "allocated": True,
                "job_id": job_id,
                "already_allocated": True,
                "allocated_vram_gb": self.allocations[job_id],
                "available_vram_gb": free,
            }

        if req <= free + 1e-9:
            self.allocations[job_id] = req
            return {
                "allocated": True,
                "job_id": job_id,
                "device": self.device_name,
                "allocated_vram_gb": req,
                "available_vram_gb": self.available_vram_gb(),
            }

        return {
            "allocated": False,
            "job_id": job_id,
            "device": self.device_name,
            "requested_vram_gb": req,
            "available_vram_gb": free,
            "error": "Insufficient VRAM capacity.",
        }

    def release_allocation(self, job_id: str) -> Dict[str, Any]:
        """Release VRAM allocated to a finished or cancelled job."""
        if job_id not in self.allocations:
            return {"released": False, "job_id": job_id, "error": "Job ID not found in allocations."}

        freed = self.allocations.pop(job_id)
        return {
            "released": True,
            "job_id": job_id,
            "freed_vram_gb": freed,
            "available_vram_gb": self.available_vram_gb(),
        }

    def status_snapshot(self) -> Dict[str, Any]:
        """Return current device VRAM usage and active job allocations."""
        used = round(sum(self.allocations.values()), 4)
        return {
            "device": self.device_name,
            "total_vram_gb": self.total_vram_gb,
            "used_vram_gb": used,
            "available_vram_gb": self.available_vram_gb(),
            "active_jobs": dict(self.allocations),
        }