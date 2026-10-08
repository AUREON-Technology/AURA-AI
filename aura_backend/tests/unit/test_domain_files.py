"""`domain/files.py::strip_absolute_prefix` -- the absolute-path mapping every
workspace resolver shares.

The POSIX branch is the old `lstrip("/")` behaviour, pinned exactly (including
that a leading backslash is a legal filename character there and is preserved).
The Windows branch is exercised via `monkeypatch` on `os.name` -- the function
is pure string handling with no pathlib involved, so both branches run on any
platform. Found by `tests/security/test_workspace_boundaries.py` failing on
Windows, where `C:\\...` sailed straight through `lstrip("/")` and the
containment check then refused a write the contract says is confined.
"""

import os

import pytest

from aura_core.domain.files import strip_absolute_prefix


def test_posix_strips_leading_slashes(monkeypatch) -> None:
    monkeypatch.setattr(os, "name", "posix")
    assert strip_absolute_prefix("/a/b") == "a/b"
    assert strip_absolute_prefix("///a") == "a"
    assert strip_absolute_prefix("a/b") == "a/b"
    assert strip_absolute_prefix("") == ""


def test_posix_preserves_a_leading_backslash(monkeypatch) -> None:
    monkeypatch.setattr(os, "name", "posix")
    # A backslash starts a legal POSIX filename -- stripping it would rename
    # the file, so only `/` goes.
    assert strip_absolute_prefix("\\weird") == "\\weird"


def test_posix_leaves_a_drive_like_prefix_alone(monkeypatch) -> None:
    monkeypatch.setattr(os, "name", "posix")
    # `C:foo` is an ordinary relative name on POSIX, not a drive.
    assert strip_absolute_prefix("C:foo") == "C:foo"


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("C:\\Users\\x\\f.txt", "Users\\x\\f.txt"),
        ("C:/Users/x/f.txt", "Users/x/f.txt"),
        ("c:\\x", "x"),
        ("/a/b", "a/b"),
        ("\\\\server\\share\\x", "server\\share\\x"),
        ("rel\\path", "rel\\path"),
        ("rel/path", "rel/path"),
    ],
)
def test_windows_strips_drive_and_separators(monkeypatch, given: str, expected: str) -> None:
    monkeypatch.setattr(os, "name", "nt")
    assert strip_absolute_prefix(given) == expected
