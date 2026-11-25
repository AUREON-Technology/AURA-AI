"""ADR-055: reading GPUs honestly. No GPU exists on this machine, so the probe is exercised against a real
executable named ``nvidia-smi`` that prints what the real one prints (the documented CSV), plus the cases a real
host produces: missing binary, driver error, garbage, slow answer. The tool runs through the real sandbox runner."""

from __future__ import annotations

import json
import os
import stat

import pytest

from aura_core.adapters.coding_fakes import InMemorySandboxRunner
from aura_core.adapters.gpu_probe import GpuProbe
from aura_core.observability.gpu import GpuInfo, as_dicts, parse_nvidia_smi
from aura_core.observability.metrics import Metrics
from aura_core.ports.runtime import ToolContext
from aura_core.tools.coding import build_sandbox_tools

TWO_GPUS = "0, NVIDIA A100-SXM4-40GB, 40960, 1024, 7, 34\n1, NVIDIA A100-SXM4-40GB, 40960, 40000, 100, 81\n"


def fake_smi(tmp_path, body: str, *, code: int = 0, sleep: float = 0.0):
    path = tmp_path / "nvidia-smi"
    path.write_text(f"#!/bin/sh\nsleep {sleep}\ncat <<'EOF'\n{body}EOF\nexit {code}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return f"{tmp_path}:/usr/bin:/bin"  # a PATH the script's own sleep/cat can still be found on


class TestParser:
    def test_the_documented_csv(self) -> None:
        gpus = parse_nvidia_smi(TWO_GPUS)
        assert [g.index for g in gpus] == [0, 1] and gpus[0].name == "NVIDIA A100-SXM4-40GB"
        assert gpus[0].memory_total_bytes == 40960 * 1024 * 1024 and gpus[0].utilisation == 0.07
        assert gpus[1].memory_free_bytes == 960 * 1024 * 1024 and gpus[1].temperature_c == 81
        assert as_dicts(gpus)[1]["memory_free_mib"] == 960

    def test_unsupported_fields_are_unknown_not_zero(self) -> None:
        (g,) = parse_nvidia_smi("0, GeForce, 8192, [N/A], [Not Supported], [N/A]\n")
        assert g.memory_used_bytes is None and g.utilisation is None and g.memory_free_bytes is None

    def test_no_output_is_no_gpus_but_other_text_is_an_error(self) -> None:
        assert parse_nvidia_smi("") == []
        for text in ("NVIDIA-SMI has failed because it couldn't communicate with the NVIDIA driver.",
                     "0, a, b", "Failed to initialize NVML: Driver/library version mismatch"):
            with pytest.raises(ValueError):
                parse_nvidia_smi(text)


class TestProbe:
    async def test_a_working_binary_is_read_and_cached(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("PATH", fake_smi(tmp_path, TWO_GPUS))
        probe = GpuProbe(ttl_s=60)
        first = await probe.probe()
        assert first.available and len(first.gpus) == 2
        (tmp_path / "nvidia-smi").unlink()
        assert (await probe.probe()) is first, "a scrape every few seconds must not fork a process each time"

    async def test_a_missing_binary_is_unavailable_not_zero(self, monkeypatch) -> None:
        monkeypatch.setenv("PATH", "/nonexistent-aura-path")
        result = await GpuProbe().probe()
        assert not result.available and "not installed" in result.reason and result.gpus == []

    async def test_a_driver_error_garbage_and_a_hang_are_each_unavailable_with_a_reason(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("PATH", fake_smi(tmp_path, "NVIDIA-SMI has failed because it couldn't communicate\n", code=9))
        failed = await GpuProbe().probe()
        assert not failed.available and "failed" in failed.reason
        monkeypatch.setenv("PATH", fake_smi(tmp_path, "totally unrelated\n"))
        assert "unreadable" in (await GpuProbe().probe()).reason
        monkeypatch.setenv("PATH", fake_smi(tmp_path, TWO_GPUS, sleep=5))
        slow = await GpuProbe(timeout_s=0.5).probe()
        assert not slow.available and "did not answer" in slow.reason

    async def test_the_gauges_are_set_per_device_and_left_unset_without_a_gpu(self, tmp_path, monkeypatch) -> None:
        metrics = Metrics()
        monkeypatch.setenv("PATH", "/nonexistent-aura-path")
        await GpuProbe().refresh_gauges(metrics)
        assert b'aura_gpu_memory_used_bytes{' not in metrics.render()
        monkeypatch.setenv("PATH", fake_smi(tmp_path, TWO_GPUS))
        await GpuProbe().refresh_gauges(metrics)
        text = metrics.render().decode()
        assert 'aura_gpu_memory_used_bytes{device="1"} 4.194304e+10' in text
        assert 'aura_gpu_utilisation_ratio{device="1"} 1.0' in text


class TestTheTool:
    @pytest.fixture
    def tools(self):
        return {t.spec.name: t for t in build_sandbox_tools(InMemorySandboxRunner())}

    @pytest.fixture
    def ctx(self, user) -> ToolContext:
        return ToolContext(user_id=user, correlation_id="c", deadline_s=60.0)

    async def test_it_reports_devices_and_says_it_cannot_allocate(self, tools, ctx, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("PATH", fake_smi(tmp_path, TWO_GPUS))
        out = json.loads(await tools["gpu_status"].execute({}, ctx))
        assert out["available"] and out["count"] == 2 and out["gpus"][1]["utilisation"] == 1.0
        assert "does not reserve or allocate" in out["note"]

    async def test_no_driver_is_not_installed_never_zero(self, tools, ctx, monkeypatch) -> None:
        monkeypatch.setenv("PATH", "/nonexistent-aura-path")
        out = json.loads(await tools["gpu_status"].execute({}, ctx))
        assert out == {**out, "available": False, "status": "not_installed", "gpus": []}

    async def test_a_failing_driver_is_failed_with_the_message(self, tools, ctx, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("PATH", fake_smi(tmp_path, "Failed to initialize NVML\n", code=18))
        out = json.loads(await tools["gpu_status"].execute({}, ctx))
        assert out["available"] is False and out["status"] == "failed" and "NVML" in out["detail"]

    def test_it_is_engineer_gated_and_never_writes(self, tools) -> None:
        spec = tools["gpu_status"].spec
        assert spec.required_capability == "engineer_agent" and "untrusted_content" in spec.tags
