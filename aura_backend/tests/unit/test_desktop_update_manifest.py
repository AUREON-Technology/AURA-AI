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
