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
    host_proxy = (REPO / "deploy/nginx/host-proxy.conf.example").read_text(encoding="utf-8")
    assert "context: ../aura_admin" in compose
    assert "SUPERADMIN_EMAILS: ${SUPERADMIN_EMAILS:-}" in compose
    assert "ADMIN_EMAILS: ${ADMIN_EMAILS:-}" in compose
    assert "DEVELOPER_EMAILS: ${DEVELOPER_EMAILS:-}" in compose
    assert "GF_SECURITY_ALLOW_EMBEDDING" in compose
    assert "location /console" in nginx
    assert "location = /console" not in nginx
    assert "auth_request /_grafana_auth" in nginx
    assert "proxy_pass http://aura_grafana;" in nginx
    assert "proxy_pass http://aura_grafana/;" not in nginx
    assert 'GF_AUTH_ANONYMOUS_ENABLED: "true"' in compose
    assert "location /metrics" in nginx
    assert "deny all" in nginx
    # Phase H live voice: dedicated Upgrade location before /api/ (and host-proxy mirror).
    assert "location /api/v3/voice/live" in nginx
    assert 'proxy_set_header Connection "upgrade"' in nginx
    assert "location /api/v3/voice/live" in host_proxy
    assert "AURA_VOICE_LIVE_ENABLED:" in compose


def test_vps_update_force_recreates_nginx_for_template_rerender() -> None:
    text = (REPO / "deploy/scripts/vps-update.sh").read_text(encoding="utf-8")
    assert "--force-recreate --no-deps nginx" in text
    assert "api/v3/voice/live" in text


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


def test_backups_are_encrypted_by_default_and_fail_closed() -> None:
    """P1.3: encryption is the compose default, the image ships age, and the lib fails closed.

    The behaviour itself is tested by deploy/scripts/tests/test_backup_crypto.sh (Backend CI's
    backup-scripts job); this pins the wiring that test cannot see.
    """
    compose = (REPO / "deploy/docker-compose.prod.yml").read_text(encoding="utf-8")
    assert "BACKUP_ENCRYPTION: ${BACKUP_ENCRYPTION:-age}" in compose
    assert "age" in (REPO / "deploy/backup/Dockerfile").read_text(encoding="utf-8").split("apk add", 1)[1]
    lib = (REPO / "deploy/scripts/backup-lib.sh").read_text(encoding="utf-8")
    assert "bk_require_encryption_config" in lib
    assert "refusing to ship an unencrypted backup set offsite" in lib
    backup = (REPO / "deploy/scripts/backup.sh").read_text(encoding="utf-8")
    assert "bk_require_encryption_config" in backup


def test_backup_never_packs_the_data_encryption_key() -> None:
    """A backup that carries its own decryption key protects nothing (P1.2 key custody)."""
    for name in ("backup.sh", "backup-lib.sh"):
        text = (REPO / "deploy/scripts" / name).read_text(encoding="utf-8")
        assert "data_encryption_keys" not in text, f"{name} references the data-encryption key"
