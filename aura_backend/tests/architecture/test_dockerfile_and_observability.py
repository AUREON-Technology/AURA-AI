"""AURA-012 / AURA-015 / AURA-019 / AURA-020: Dockerfile, lockfile, alerts, backups.

Multiprocess metrics behaviour lives in tests/unit/test_metrics_multiprocess.py so this
module stays a string-level fitness function (no prometheus_client import required).
"""

from __future__ import annotations

from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
REPO = BACKEND.parent


def test_dockerfile_cmd_boots_aura_service() -> None:
    """Production image default must hydrate secrets and mount /api/v3 (AURA-012)."""
    text = (BACKEND / "Dockerfile").read_text(encoding="utf-8")
    cmd_lines = [ln for ln in text.splitlines() if ln.strip().startswith("CMD")]
    assert cmd_lines, "Dockerfile has no CMD"
    assert any("aura_service:app" in ln for ln in cmd_lines)
    assert not any("main:app" in ln for ln in cmd_lines)


def test_dockerfile_installs_from_lockfile() -> None:
    text = (BACKEND / "Dockerfile").read_text(encoding="utf-8")
    assert "requirements.lock" in text
    assert (BACKEND / "requirements.lock").is_file()


def test_testcontainers_is_not_a_runtime_dependency() -> None:
    runtime = (BACKEND / "requirements.txt").read_text(encoding="utf-8")
    dev = (BACKEND / "requirements-dev.txt").read_text(encoding="utf-8")
    assert "testcontainers" not in runtime
    assert "testcontainers" in dev


def test_alertmanager_routes_critical_without_committed_webhook() -> None:
    text = (REPO / "deploy/monitoring/alertmanager.yml").read_text(encoding="utf-8")
    assert "receiver: critical" in text
    assert "receiver: default" in text
    assert "inhibit_rules:" in text
    lowered = text.lower()
    assert "https://" not in lowered
    assert "hooks.example" not in lowered
    assert "webhook_configs:" not in text


def test_compose_and_nginx_serve_the_operator_console() -> None:
    compose = (REPO / "deploy/docker-compose.prod.yml").read_text(encoding="utf-8")
    nginx = (REPO / "deploy/nginx/templates/aura.conf.template").read_text(encoding="utf-8")
    assert "context: ../aura_admin" in compose
    assert "SUPERADMIN_EMAILS: ${SUPERADMIN_EMAILS:-}" in compose
    assert "ADMIN_EMAILS: ${ADMIN_EMAILS:-}" in compose
    assert "DEVELOPER_EMAILS: ${DEVELOPER_EMAILS:-}" in compose
    assert "GF_SECURITY_ALLOW_EMBEDDING" in compose
    assert "location /console" in nginx
    assert "location = /console" not in nginx
    assert "auth_request /_grafana_auth" in nginx
    assert "location /metrics" in nginx
    assert "deny all" in nginx


def test_compose_sets_prometheus_multiproc_dir_on_gateway() -> None:
    text = (REPO / "deploy/docker-compose.prod.yml").read_text(encoding="utf-8")
    assert "PROMETHEUS_MULTIPROC_DIR:" in text
    assert "prometheus_multiproc:" in text


def test_restore_supports_noninteractive_confirm_matching_database() -> None:
    text = (REPO / "deploy/scripts/restore.sh").read_text(encoding="utf-8")
    assert "RESTORE_CONFIRM" in text
    assert "PGDATABASE" in text


def test_backup_copies_qdrant_snapshots_and_redis_rdb() -> None:
    text = (REPO / "deploy/scripts/backup.sh").read_text(encoding="utf-8")
    assert "qdrant-" in text
    assert "BACKUP_DIR" in text
    assert "redis-cli" in text
    assert "--rdb" in text


def test_restore_drill_script_refuses_prod_postgres_env() -> None:
    text = (REPO / "deploy/scripts/restore-drill.sh").read_text(encoding="utf-8")
    assert "throwaway" in text.lower()
    assert "pg_restore" in text
    assert "PGHOST" in text
