"""``list_files`` / ``read_file`` / ``fetch_url`` can now be switched on (audit T-06, inventory rows 56, 141-143).

They were implemented in ``tools/builtin.py`` but ``Container`` never passed the ``workspace`` or
``allowed_http_hosts`` that ``default_tools`` needs, so no deployment could register them. These tests
build a real ``Container`` and read its real tool registry.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aura_core.config.settings import AuraSettings, ToolSettings, load_settings
from aura_core.container import Container
from aura_core.domain.errors import ConfigurationError
from aura_core.domain.identifiers import UserId
from aura_core.tools.base import ToolContext

TOOLS = {"list_files", "read_file", "fetch_url"}


def _names(monkeypatch: pytest.MonkeyPatch, **env: str) -> set[str]:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return set(Container(load_settings(refresh=True, validate=False)).tools.names())


def test_nothing_is_registered_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AURA_TOOLS_FILES_ROOT", raising=False)
    monkeypatch.delenv("AURA_TOOLS_HTTP_ALLOWED_HOSTS", raising=False)
    assert not (_names(monkeypatch) & TOOLS)


def test_a_files_root_registers_the_read_only_file_tools(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    names = _names(monkeypatch, AURA_TOOLS_FILES_ROOT=str(tmp_path))
    assert {"list_files", "read_file"} <= names and "fetch_url" not in names


def test_allowed_hosts_register_fetch_url(monkeypatch: pytest.MonkeyPatch) -> None:
    names = _names(monkeypatch, AURA_TOOLS_HTTP_ALLOWED_HOSTS="docs.python.org, example.com")
    assert "fetch_url" in names and not ({"list_files", "read_file"} & names)


async def test_registered_file_tools_really_work_and_stay_confined(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "notes.txt").write_text("hello from the root")
    (tmp_path / "outside-secret.txt").write_text("must stay unreadable")
    monkeypatch.setenv("AURA_TOOLS_FILES_ROOT", str(root))
    container = Container(load_settings(refresh=True, validate=False))
    ctx = ToolContext(user_id=UserId("u1"), correlation_id="corr-1", deadline_s=30.0)

    assert "hello from the root" in await container.tools.get("read_file").execute({"path": "notes.txt"}, ctx)
    assert "notes.txt" in await container.tools.get("list_files").execute({}, ctx)
    with pytest.raises(Exception, match="escapes"):
        await container.tools.get("read_file").execute({"path": "../outside-secret.txt"}, ctx)


@pytest.mark.parametrize("root", ["relative/dir", "/", "/data/../etc"])
def test_a_bad_files_root_is_a_startup_problem(root: str) -> None:
    with pytest.raises(ConfigurationError) as raised:
        AuraSettings(tools=ToolSettings(files_root=root)).validate()
    assert any("AURA_TOOLS_FILES_ROOT" in p for p in raised.value.details["problems"])


@pytest.mark.parametrize("host", ["https://example.com", "example.com/path", "*.example.com", "example.com:8443"])
def test_a_bad_http_host_is_a_startup_problem(host: str) -> None:
    with pytest.raises(ConfigurationError) as raised:
        AuraSettings(tools=ToolSettings(http_allowed_hosts=(host,))).validate()
    assert any("AURA_TOOLS_HTTP_ALLOWED_HOSTS" in p for p in raised.value.details["problems"])


def test_good_values_raise_no_problem_of_their_own() -> None:
    settings = AuraSettings(tools=ToolSettings(files_root="/srv/aura-files", http_allowed_hosts=("docs.python.org",)))
    try:
        settings.validate()
    except ConfigurationError as exc:  # the bare default settings may have unrelated problems
        assert not any("AURA_TOOLS_" in p for p in exc.details["problems"])
