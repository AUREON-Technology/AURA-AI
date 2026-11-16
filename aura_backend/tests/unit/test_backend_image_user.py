"""Which user the backend image runs as, and where the compose stacks deliberately override it.

The image runs as the unprivileged ``aura`` user (uid 10002). Railway runs the image as built, so
that is the user the public gateway runs as there. The VPS compose stacks cannot drop root yet:
``bootstrap-secrets.sh`` writes root-owned mode-600 secret files, compose bind-mounts file
secrets with those permissions, and the existing named volumes are root-owned. So every service
built from the image says ``user: "0:0"`` explicitly, prod and dev alike (they share a host and
are kept at parity). A service added later without it would start as ``aura``, fail to read its
secrets, and refuse to boot -- this test catches that before a deploy does.

``docker build`` itself is not run here (no Docker in the default suite). The image was built
and run as uid 10002 when this was introduced; Backend CI's docker-build job builds it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

_ROOT = Path(__file__).resolve().parents[3]
_DOCKERFILE = _ROOT / "aura_backend" / "Dockerfile"


def _backend_services(name: str) -> dict[str, dict]:
    services = yaml.safe_load((_ROOT / "deploy" / name).read_text(encoding="utf-8"))["services"]
    return {
        key: spec for key, spec in services.items()
        if isinstance(spec.get("build"), dict)
        and spec["build"].get("context", "").rstrip("/").endswith("aura_backend")
    }


def _runner_stage() -> str:
    text = _DOCKERFILE.read_text(encoding="utf-8")
    return text[text.index(" as runner"):]


class TestImage:
    def test_the_runner_stage_ends_as_the_unprivileged_user(self) -> None:
        users = re.findall(r"^USER\s+(\S+)", _runner_stage(), flags=re.MULTILINE)
        assert users and users[-1] == "aura"

    def test_the_user_is_created_with_the_pinned_uid(self) -> None:
        assert re.search(r"useradd[^\n]*--uid 10002", _runner_stage())

    def test_packages_are_not_left_under_root_home(self) -> None:
        # /root is mode 0700: a non-root process can never import from /root/.local.
        stage = _runner_stage()
        assert "ENV PYTHONUSERBASE=/opt/aura-python" in stage
        assert "COPY --from=builder /root/.local /opt/aura-python" in stage
        assert "/root/.local/bin" not in stage

    def test_the_default_data_dir_is_writable_by_that_user(self) -> None:
        stage = _runner_stage()
        assert "ENV AURA_DATA_DIR=/var/lib/aura/data" in stage
        assert re.search(r"chown aura:aura [^\n]*/var/lib/aura/data", stage)


@pytest.mark.parametrize("compose", ["docker-compose.prod.yml", "docker-compose.dev.yml"])
def test_every_vps_service_built_from_the_image_stays_root(compose: str) -> None:
    services = _backend_services(compose)
    assert len(services) == 5, sorted(services)  # gateway, two workers, training worker, beat
    not_root = {key: spec.get("user") for key, spec in services.items() if spec.get("user") != "0:0"}
    assert not not_root, f"{compose}: these would run as uid 10002 and could not read their secrets: {not_root}"
