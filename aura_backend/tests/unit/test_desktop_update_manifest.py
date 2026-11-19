"""``scripts/desktop_update_manifest.py``: the desktop update channel's manifest (M8.10).

The Flutter client parses this manifest (``aura_client/lib/desktop/update/desktop_update.dart``).
Both sides are held to one committed sample, ``aura_client/test/fixtures/desktop-update.sample.json``:
this test checks the script reproduces it exactly from the same inputs, and the Dart test parses
it. A format change has to update the sample, which breaks whichever side was not changed with it.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "desktop_update_manifest.py"
SAMPLE = REPO / "aura_client" / "test" / "fixtures" / "desktop-update.sample.json"


def _script():
    spec = importlib.util.spec_from_file_location("desktop_update_manifest", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _sample_assets(tmp_path: Path) -> dict[str, Path]:
    # The exact bytes the committed sample was generated from.
    win = tmp_path / "aura-desktop-windows-x64-3.0.2-setup.exe"
    win.write_bytes(b"windows installer bytes")
    linux = tmp_path / "aura-desktop-linux-x64-3.0.2.tar.gz"
    linux.write_bytes(b"linux bundle bytes")
    return {"windows-x64": win, "linux-x64": linux}


def test_release_reproduces_the_shared_sample(tmp_path: Path) -> None:
    manifest = _script().release_manifest(
        "3.0.2", "desktop-v3.0.2", "anasibrahimgnedy-collab/AURA-AI", _sample_assets(tmp_path)
    )
    assert manifest == json.loads(SAMPLE.read_text(encoding="utf-8"))


def test_release_urls_point_at_the_tagged_release(tmp_path: Path) -> None:
    manifest = _script().release_manifest("3.0.2", "desktop-v3.0.2", "o/r", _sample_assets(tmp_path))
    for asset in manifest["platforms"].values():
        assert asset["url"].startswith("https://github.com/o/r/releases/download/desktop-v3.0.2/")


def test_rollback_marks_the_manifest_and_changes_nothing_else() -> None:
    sample = json.loads(SAMPLE.read_text(encoding="utf-8"))
    rolled = _script().rollback_manifest(sample)
    assert rolled["rollback"] is True
    assert {k: v for k, v in rolled.items() if k != "rollback"} == {
        k: v for k, v in sample.items() if k != "rollback"
    }


@pytest.mark.parametrize(
    ("version", "tag", "message"),
    [
        ("3.0.2", "desktop-v3.0.3", "does not match"),
        ("3.0", "desktop-v3.0", "major.minor.patch"),
        ("3.0.2+5", "desktop-v3.0.2+5", "major.minor.patch"),
    ],
)
def test_release_refuses_a_tag_or_version_that_would_mislabel_the_channel(
    tmp_path: Path, version: str, tag: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _script().release_manifest(version, tag, "o/r", _sample_assets(tmp_path))


def test_release_refuses_a_missing_asset(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no such file"):
        _script().release_manifest("3.0.2", "desktop-v3.0.2", "o/r", {"windows-x64": tmp_path / "nope.exe"})


def test_cli_writes_the_manifest_and_fails_cleanly_on_bad_input(tmp_path: Path) -> None:
    assets = _sample_assets(tmp_path)
    out = tmp_path / "desktop-update.json"
    script = _script()
    code = script.main([
        "release", "--version", "3.0.2", "--tag", "desktop-v3.0.2",
        "--repo", "anasibrahimgnedy-collab/AURA-AI",
        "--asset", f"windows-x64={assets['windows-x64']}",
        "--asset", f"linux-x64={assets['linux-x64']}",
        "--output", str(out),
    ])
    assert code == 0
    assert json.loads(out.read_text(encoding="utf-8")) == json.loads(SAMPLE.read_text(encoding="utf-8"))
    assert script.main(["release", "--version", "3.0.2", "--tag", "x", "--repo", "o/r", "--output", str(out)]) == 1


# -- F18: the manifest is signed ---------------------------------------------------------------
#
# Format (the client, aura_client/lib/desktop/update/, verifies exactly this): an Ed25519
# signature over the manifest file's exact bytes, standard base64 with no trailing newline, in a
# sibling file named `<manifest>.sig` (desktop-update.json.sig) in the same release. The private
# key is DESKTOP_UPDATE_SIGNING_KEY: standard base64 of the raw 32-byte Ed25519 seed.


def _seed_b64() -> str:
    import base64
    import os

    return base64.b64encode(os.urandom(32)).decode("ascii")


def _ed25519_verify(public_b64: str, data: bytes, signature_b64: str) -> bool:
    """Verify independently of the script, with the library directly."""
    import base64

    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_b64, validate=True))
    try:
        key.verify(base64.b64decode(signature_b64, validate=True), data)
        return True
    except InvalidSignature:
        return False


def test_a_signature_round_trips_over_the_exact_bytes() -> None:
    script = _script()
    seed = _seed_b64()
    data = SAMPLE.read_bytes()

    signature = script.sign_bytes(data, seed)
    public = script.public_key_b64(seed)

    assert _ed25519_verify(public, data, signature)
    assert script.verify_bytes(data, signature, public)
    # One changed byte -- a URL or a hash swapped for an attacker's -- and it no longer verifies.
    tampered = data.replace(b"3.0.2", b"3.0.3", 1)
    assert not script.verify_bytes(tampered, signature, public)
    assert not _ed25519_verify(public, tampered, signature)


def test_the_published_formats_are_the_documented_ones() -> None:
    import base64

    script = _script()
    seed = _seed_b64()
    signature = script.sign_bytes(b"{}\n", seed)
    public = script.public_key_b64(seed)
    assert len(base64.b64decode(signature, validate=True)) == 64
    assert len(base64.b64decode(public, validate=True)) == 32
    assert not signature.endswith("\n") and "\n" not in public
    assert script.SIGNATURE_SUFFIX == ".sig"


@pytest.mark.parametrize("bad", ["", "not base64!", "c2hvcnQ="])
def test_a_missing_or_malformed_key_is_refused(bad: str) -> None:
    with pytest.raises(ValueError):
        _script().sign_bytes(b"{}", bad)


def test_cli_signs_the_written_file_and_fails_closed_without_the_key(tmp_path: Path, monkeypatch) -> None:
    script = _script()
    manifest = tmp_path / "desktop-update.json"
    manifest.write_bytes(SAMPLE.read_bytes())
    sig = tmp_path / "desktop-update.json.sig"

    monkeypatch.delenv("DESKTOP_UPDATE_SIGNING_KEY", raising=False)
    assert script.main(["sign", "--manifest", str(manifest)]) == 1
    assert not sig.exists(), "no key must mean no signature, never an empty or placeholder one"

    seed = _seed_b64()
    monkeypatch.setenv("DESKTOP_UPDATE_SIGNING_KEY", seed)
    assert script.main(["sign", "--manifest", str(manifest)]) == 0
    signature = sig.read_text(encoding="ascii")
    public = script.public_key_b64(seed)
    assert _ed25519_verify(public, manifest.read_bytes(), signature)
    assert script.main(["verify", "--manifest", str(manifest), "--public-key", public]) == 0

    manifest.write_bytes(manifest.read_bytes() + b" ")
    assert script.main(["verify", "--manifest", str(manifest), "--public-key", public]) == 1


def test_cli_prints_the_public_key_to_pin(monkeypatch, capsys) -> None:
    script = _script()
    seed = _seed_b64()
    monkeypatch.setenv("DESKTOP_UPDATE_SIGNING_KEY", seed)
    assert script.main(["public-key"]) == 0
    assert capsys.readouterr().out.strip() == script.public_key_b64(seed)


def test_the_script_still_runs_without_cryptography_for_release_and_rollback() -> None:
    """The release and rollback modes run before the signing library is needed; importing it at
    module scope would make them fail on a runner that has not installed it yet."""
    source = SCRIPT.read_text(encoding="utf-8")
    module_scope = [line for line in source.splitlines() if line.startswith(("import ", "from "))]
    assert not any("cryptography" in line for line in module_scope)


_WORKFLOWS = REPO / ".github" / "workflows"


def _job(workflow: str, job: str) -> dict:
    yaml = pytest.importorskip("yaml")
    spec = yaml.safe_load((_WORKFLOWS / workflow).read_text(encoding="utf-8"))
    return spec["jobs"][job]


class TestDesktopReleasePublishingIsGated:
    """F18 (CI side). Both jobs that change what installed desktops will download hold
    `contents: write`; they run only behind the `release` environment (required reviewers are
    configured there), and the manifest they publish is signed, failing closed without the key."""

    @pytest.mark.parametrize("job", ["publish-release", "rollback"])
    def test_the_job_needs_the_release_environment(self, job: str) -> None:
        assert _job("desktop-package.yml", job).get("environment") == "release"

    @pytest.mark.parametrize("job", ["publish-release", "rollback"])
    def test_the_manifest_is_signed_and_the_signature_published(self, job: str) -> None:
        steps = _job("desktop-package.yml", job)["steps"]
        text = "\n".join(str(s.get("run", "")) for s in steps)
        envs = "\n".join(str(s.get("env", {})) for s in steps)
        assert "desktop_update_manifest.py sign" in text
        assert "desktop_update_manifest.py verify" in text
        assert "secrets.DESKTOP_UPDATE_SIGNING_KEY" in envs
        assert "desktop-update.json.sig" in text
        assert "--require-hashes" in text, "the signing library must be installed hash-checked"

    def test_signing_comes_before_publishing(self) -> None:
        steps = _job("desktop-package.yml", "publish-release")["steps"]
        runs = [str(s.get("run", "")) for s in steps]
        sign = next(i for i, r in enumerate(runs) if "desktop_update_manifest.py sign" in r)
        publish = next(i for i, r in enumerate(runs) if "gh release create" in r)
        assert sign < publish
