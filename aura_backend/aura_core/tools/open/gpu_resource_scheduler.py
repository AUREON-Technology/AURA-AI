<<<<<<< HEAD
"""Superseded -- deliberately NOT registered as a tool (ADR-036). The platform already has the real
signals: `observability/metrics.py` (`gpu_memory_used_bytes`, `gpu_utilisation`) and
`adapters/ml/device.py`. This class is a bookkeeping ledger -- it never reads a device, has no
queue or priority, and has no locking -- so a scheduler that says 'allocated' says nothing about
the GPU.

Kept for reference only: nothing under ``aura_core`` may import it
(``tests/architecture/test_superseded_open_tools_unused.py``).
"""

from typing import Any, Dict
=======
from typing import Any, Dict, List, Optional
>>>>>>> f8b3e927b90821d5152b6e48f081b879bfbb4c3b


class GpuResourceScheduler:
    """Scheduler for tracking VRAM capacity, estimating model requirements, offload planning, and job allocation."""

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

    @staticmethod
    def estimate_model_vram_requirement(
        param_count_billions: float,
        precision_bits: int = 16,
        context_length: int = 4096,
        batch_size: int = 1,
    ) -> Dict[str, Any]:
        """Estimate model weights and KV cache memory footprint in Gigabytes."""
        bytes_per_param = precision_bits / 8.0
        model_weights_gb = (param_count_billions * 1e9 * bytes_per_param) / (1024**3)

        # Approximate KV cache footprint (2 * num_layers * hidden_size * context * batch * bytes_per_elem)
        # Empirical approximation: ~0.5MB per 1k tokens per billion parameters for 16-bit
        kv_cache_gb = (context_length / 1000.0) * (param_count_billions * 0.25) * batch_size * (precision_bits / 16.0)
        
        # 20% activation overhead
        activation_buffer_gb = (model_weights_gb + kv_cache_gb) * 0.20
        total_estimated_gb = model_weights_gb + kv_cache_gb + activation_buffer_gb

        return {
            "param_count_billions": param_count_billions,
            "precision_bits": precision_bits,
            "weights_vram_gb": round(model_weights_gb, 2),
            "kv_cache_vram_gb": round(kv_cache_gb, 2),
            "total_vram_recommended_gb": round(total_estimated_gb, 2),
        }

    @classmethod
    def suggest_offload_strategy(
        cls,
        model_vram_gb: float,
        available_gpu_vram_gb: float,
    ) -> Dict[str, Any]:
        """Suggest optimization strategies (quantization, CPU offloading) if model exceeds GPU memory."""
        fits_natively = model_vram_gb <= available_gpu_vram_gb
        deficit_gb = max(0.0, model_vram_gb - available_gpu_vram_gb)

        recommendations: List[str] = []
        if not fits_natively:
            # 8-bit quantization halves weight size
            if (model_vram_gb * 0.55) <= available_gpu_vram_gb:
                recommendations.append("Apply 8-bit quantization (bitsandbytes / INT8) to fit fully on GPU.")
            elif (model_vram_gb * 0.30) <= available_gpu_vram_gb:
                recommendations.append("Apply 4-bit quantization (AWQ / GPTQ / Q4_K_M) to fit on GPU.")
            else:
                pct_cpu = round((deficit_gb / model_vram_gb) * 100.0, 1)
                recommendations.append(f"Offload ~{pct_cpu}% of transformer layers to System RAM (CPU offload).")

        return {
            "fits_natively": fits_natively,
            "deficit_gb": round(deficit_gb, 2),
            "recommendations": recommendations,
        }

    def schedule_workload_queue(
        self,
        pending_jobs: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Allocate as many pending workloads as possible given available VRAM."""
        allocated_jobs: List[str] = []
        queued_jobs: List[str] = []

        # Sort by priority then requested VRAM
        sorted_jobs = sorted(pending_jobs, key=lambda x: (x.get("priority", 1), -x.get("requested_vram_gb", 0)), reverse=True)

        for job in sorted_jobs:
            jid = job.get("job_id") or job.get("id")
            req = float(job.get("requested_vram_gb", 1.0))
            res = self.request_allocation(jid, req)
            if res.get("allocated"):
                allocated_jobs.append(jid)
            else:
                queued_jobs.append(jid)

        return {
            "allocated_jobs": allocated_jobs,
            "queued_jobs": queued_jobs,
            "current_status": self.status_snapshot(),
        }
