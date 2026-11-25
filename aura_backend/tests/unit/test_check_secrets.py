"""scripts/check_secrets.py refuses a committed credential and stays quiet on a clean tree."""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

SCRIPT =Path(__file__).resolve().parents[3] / "scripts" / "check_secrets.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_secrets", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for name, body in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    subprocess.run(["git", "add", "-f", "."], cwd=tmp_path, check=True)
    return tmp_path


def test_clean_tree_passes(tmp_path):
    repo = _repo(tmp_path, {"a.py": "print('hi')\n"})
    assert _load().scan(repo) == []


def test_service_account_json_is_found_without_echoing_the_secret(tmp_path):
    body = '{"type": "service_account", "private_key_id": "' + "ab12" * 10 + '"}\n'
    repo = _repo(tmp_path, {"cfg/anything.json": body})
    found = _load().scan(repo)
    assert found == ["cfg/anything.json:1: service-account JSON"]
    assert "ab12" not in found[0]


def test_private_key_block_is_found(tmp_path):
    repo = _repo(tmp_path, {"k.txt": "-----BEGIN " + "PRIVATE KEY-----\nx\n"})
    assert any("private key block" in f for f in _load().scan(repo))


def test_credentials_json_is_refused_by_name(tmp_path):
    repo = _repo(tmp_path, {"aura_backend/credentials.json": "{}"})
    assert any("file name is a credential" in f for f in _load().scan(repo))


def test_allowlisted_firebase_client_key_passes(tmp_path):
    key = "AIza" + "A" * 35
    repo = _repo(tmp_path, {"aura_client/android/app/google-services.json": f'{{"current_key": "{key}"}}'})
    assert _load().scan(repo) == []


def test_same_key_elsewhere_is_still_flagged(tmp_path):
    repo = _repo(tmp_path, {"other.json": '"' + "AIza" + "A" * 35 + '"'})
    assert any("Google API key" in f for f in _load().scan(repo))


@pytest.mark.parametrize(("kind", "secret"), [
    ("Google OAuth client secret", "GOCSPX-" + "a1B2" * 7),
    ("Google AI Studio key", "AQ." + "Ab8R" * 12 + "xy"),
    ("JSON Web Token", ".".join(["eyJ" + "a" * 20, "eyJ" + "b" * 30, "c" * 43])),
])
def test_other_leaked_key_formats_are_found_without_echoing_them(tmp_path, kind, secret):
    repo = _repo(tmp_path, {"cfg.txt": f"KEY = {secret}\n"})
    assert _load().scan(repo) == [f"cfg.txt:1: {kind}"]


@pytest.mark.parametrize("name", [".env", "aura_backend/.env", "deploy/.env.production", "prod.env"])
def test_env_files_are_refused_by_name(tmp_path, name):
    repo = _repo(tmp_path, {name: "PORT=8000\n"})
    assert _load().scan(repo) == [f"{name}: file name is a credential (never commit it)"]


@pytest.mark.parametrize("name", ["deploy/.env.example", "deploy/.env.development.example", ".env.sample",
                                  ".env.template"])
def test_env_templates_may_be_tracked(tmp_path, name):
    repo = _repo(tmp_path, {name: "PORT=8000\n"})
    assert _load().scan(repo) == []
