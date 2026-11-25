"""Deleting and renaming one entry inside a sandbox workspace, shared by both server-side runners.

``DockerSandboxRunner`` and ``InMemorySandboxRunner`` mirror each other on purpose (the in-memory
one is what every test exercises), and the rules for these two operations are subtle enough that
two copies would drift. The desktop daemon (``aura_hostd``, ``task_handler.dart``) enforces the
same rules on the user's own machine; this is their server-side twin.

The rules, and why:

* **An entry is named, not followed.** The parent directory is resolved (so a symlinked directory
  cannot lead outside the workspace) but the final component is kept as written. Resolving the whole
  path would make deleting or renaming a symlink act on whatever it points at.
* **Delete removes one file or link, never a directory.** Deleting a tree is the call that destroys
  the most for the least typing; it stays an explicit ``run_command``.
* **Rename never replaces.** Overwriting would be a delete the caller did not ask for.
* **Nothing at or under ``.git``.** A repository's own storage is changed with ``git``, not by
  moving its files around; the daemon refuses the same paths.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..domain.files import strip_absolute_prefix

__all__ = ["delete_entry", "rename_entry"]


def _entry_within(root: Path, relative: str, error: type[Exception]) -> Path:
    """The path of the entry ``relative`` names inside ``root``, or ``error`` if it cannot name one
    there (null byte, the root itself, ``.``/``..``, or a parent that resolves outside ``root``)."""
    if "\x00" in relative:
        raise error("path contains a null byte")
    root = root.resolve()
    candidate = root / strip_absolute_prefix(relative)
    name = candidate.name
    if name in ("", ".", ".."):
        raise error(f"{relative!r} does not name a file or directory entry")
    parent = candidate.parent.resolve()
    if parent != root and root not in parent.parents:
        raise error(f"path {relative!r} escapes the sandbox workspace")
    return parent / name


def _refuse_git_internals(entry: Path, relative: str, verb: str, error: type[Exception]) -> None:
    if any(part.lower().rstrip(". ") == ".git" for part in entry.parts):
        raise error(
            f"{verb} of {relative!r} is refused: it is inside a .git directory -- use git commands "
            "to change a repository's own storage"
        )


def delete_entry(root: Path, relative: str, error: type[Exception]) -> None:
    """Delete the one file or symlink ``relative`` names inside ``root``."""
    entry = _entry_within(root, relative, error)
    _refuse_git_internals(entry, relative, "delete", error)
    if not os.path.lexists(entry):
        raise error(f"{relative} does not exist in the sandbox workspace")
    if entry.is_dir() and not entry.is_symlink():
        raise error(
            f"{relative} is a directory -- delete_file removes one file at a time; use "
            "run_command to remove a directory"
        )
    try:
        entry.unlink()
    except OSError as exc:
        raise error(f"could not delete {relative}: {exc.strerror or exc}") from exc


def rename_entry(root: Path, relative: str, new_relative: str, error: type[Exception]) -> None:
    """Move ``relative`` to ``new_relative`` inside ``root``; the destination must not exist."""
    source = _entry_within(root, relative, error)
    destination = _entry_within(root, new_relative, error)
    _refuse_git_internals(source, relative, "rename", error)
    _refuse_git_internals(destination, new_relative, "rename", error)
    if not os.path.lexists(source):
        raise error(f"{relative} does not exist in the sandbox workspace")
    if os.path.lexists(destination):
        raise error(f"{new_relative} already exists -- rename never replaces an existing entry")
    if source.is_dir() and not source.is_symlink() and source in destination.parents:
        raise error(f"cannot move {relative} into itself ({new_relative})")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.rename(source, destination)
    except OSError as exc:
        raise error(f"could not rename {relative} to {new_relative}: {exc.strerror or exc}") from exc
