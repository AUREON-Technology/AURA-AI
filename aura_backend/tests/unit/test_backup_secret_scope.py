"""The backup sidecar sees only its own secrets (audit FSA-008 / GAP-046 / CB-08).

`deploy/secrets/` holds every key the stack has: jwt_secret, the data-encryption KEK that wraps
each user's ADR-032 data key, the Fernet key for stored OAuth tokens, the Firebase service
account. The backup container runs a third-party sync tool on the egress network; it needs the
age recipients file, the S3 keys and rclone.conf, and nothing else. This pins that no service in
either compose file bind-mounts the whole directory, and that the backup service mounts exactly
the `secrets/backup/` subdirectory `bootstrap-secrets.sh` creates.
"""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

_DEPLOY = Path(__file__).resolve().parents[3] / "deploy"
_COMPOSE = {
    "prod": _DEPLOY / "docker-compose.prod.yml",
    "dev": _DEPLOY / "docker-compose.dev.yml",
}


def _services(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["services"]


def _bind_sources(service: dict) -> list[str]:
    out = []
    for volume in service.get("volumes") or []:
        if isinstance(volume, str):
            out.append(volume.split(":", 1)[0])
        elif isinstance(volume, dict) and volume.get("type", "bind") == "bind":
            out.append(str(volume.get("source", "")))
    return out


@pytest.mark.parametrize("name", sorted(_COMPOSE))
def test_no_service_mounts_the_whole_secrets_directory(name: str) -> None:
    offenders = {
        svc: src
        for svc, spec in _services(_COMPOSE[name]).items()
        for src in _bind_sources(spec)
        if src.rstrip("/") in {"./secrets", "secrets", "./deploy/secrets"}
    }
    assert not offenders, f"{name}: wholesale ./secrets bind mount in {offenders}"


def test_the_backup_sidecar_mounts_only_its_own_subdirectory() -> None:
    backup = _services(_COMPOSE["prod"])["backup"]
    sources = _bind_sources(backup)
    assert "./secrets/backup" in sources, sources
    # Its env still points every file it reads at that one mount.
    env = backup["environment"]
    for key in ("BACKUP_AGE_RECIPIENTS_FILE", "BACKUP_S3_ACCESS_KEY_FILE",
                "BACKUP_S3_SECRET_KEY_FILE", "RCLONE_CONFIG_FILE"):
        assert str(env[key]).startswith("/run/backup-secrets/"), key
    # Stack-wide keys reach it only as named Docker secrets, never via the directory.
    assert set(backup["secrets"]) == {"postgres_password", "qdrant_api_key", "redis_password"}


def test_bootstrap_creates_the_subdirectory_and_moves_old_files() -> None:
    script = (_DEPLOY / "scripts" / "bootstrap-secrets.sh").read_text(encoding="utf-8")
    assert 'BACKUP_DIR="$DIR/backup"' in script
    for name in ("backup_age_recipients.txt", "backup_s3_access_key",
                 "backup_s3_secret_key", "rclone.conf"):
        assert name in script, f"bootstrap does not migrate {name}"
    migration = script.split('BACKUP_DIR="$DIR/backup"', 1)[1].split("done", 1)[0]
    assert "mv " in migration and "cp " not in migration
