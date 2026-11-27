"""Shared by runners whose workspace files are reached through the runner, not through this host.

Two things such a runner needs and a bind-mounted sandbox does not: what a workspace path may
look like when nothing here can resolve it on disk, and how a branch is taken out of a checkout as
a git bundle (``ports/coding.py::RepositoryTransferRunner``). Everything runs *inside* the
workspace through the runner; this module never runs ``git`` itself.
"""

from __future__ import annotations

import asyncio
import posixpath
import re
import uuid
from pathlib import Path
from typing import Any

from ..observability.logging import get_logger
from ..ports.coding import BranchExport, SandboxHandle
from .docker_sandbox import SandboxError

__all__ = ["workspace_relative", "export_branch"]

logger = get_logger("adapters.sandbox_transfer")

#: How a model may spell "the workspace": the cloud sandbox's folder, and the Docker sandbox's.
_ROOTS = ("/home/aura/workspace", "/workspace")

_OBJECT_ID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
#: No option, no refspec, no revision syntax, no whitespace.
_BRANCH = re.compile(r"(?![-/.])(?!.*(?:\.\.|//|@\{))[^\x00-\x20\x7f~^:?*\[\\]+(?<![/.])")

#: ``$1`` checkout folder, ``$2`` branch, ``$3`` bundle file name at the workspace root.
#: Prints the tip and how many commits no remote-tracking branch has; bundles those, if any.
_EXPORT = """
set -e
out="$(pwd)/$3"
cd -- "$1"
tip=$(git rev-parse --verify --quiet "refs/heads/$2^{commit}") || exit 4
new=$(git rev-list --count "refs/heads/$2" --not --remotes)
printf '%s %s\\n' "$tip" "$new"
[ "$new" = 0 ] || git bundle create -q "$out" "refs/heads/$2" --not --remotes >&2
"""


def workspace_relative(path: str, *, allow_root: bool = False) -> str:
    """``path`` as a plain path below the workspace root, or a refusal.

    A leading ``/`` or the sandbox's own workspace folder is read as "inside the workspace". With
    ``allow_root`` the root itself comes back as ``"."``.
    """
    if "\x00" in path:
        raise SandboxError("path contains a null byte")
    relative = path
    for root in _ROOTS:
        if relative == root or relative.startswith(root + "/"):
            relative = relative[len(root):]
            break
    relative = relative.lstrip("/")
    relative = posixpath.normpath(relative) if relative else "."
    if relative == ".." or relative.startswith("../"):
        raise SandboxError(f"path {path!r} escapes the sandbox workspace")
    if relative == "." and not allow_root:
        raise SandboxError(f"path {path!r} names the workspace itself, not something inside it")
    return relative


async def export_branch(
    runner: Any, handle: SandboxHandle, directory: str, branch: str, destination: Path
) -> BranchExport:
    """:meth:`RepositoryTransferRunner.export_branch`, in terms of ``run`` and
    ``read_file_bytes``. What comes back is untrusted: ``HostGitService.import_bundle`` checks it."""
    folder = workspace_relative(directory, allow_root=True)
    if not _BRANCH.fullmatch(branch):
        raise SandboxError(f"{branch!r} is not a plain branch name")
    name = f".aura-bundle-{uuid.uuid4().hex}"
    try:
        result = await runner.run(
            handle, ["sh", "-c", _EXPORT, "aura-export", folder, branch, name], timeout_s=240.0
        )
        if result.exit_code == 4:
            raise SandboxError(f"the repository in the workspace has no branch {branch!r}")
        words = result.stdout.split()
        if result.exit_code != 0 or len(words) < 2 or not _OBJECT_ID.fullmatch(words[-2]):
            raise SandboxError(
                "could not read the branch from the workspace: "
                f"{result.stderr.strip()[-300:] or f'exit code {result.exit_code}'}"
            )
        tip, new = words[-2], words[-1]
        if new == "0":
            return BranchExport(tip=tip)
        try:
            data = await runner.read_file_bytes(handle, name)
        except Exception as exc:
            raise SandboxError(
                f"could not take the new commits out of the workspace: {exc}"
            ) from exc
        await asyncio.to_thread(destination.write_bytes, data)
        return BranchExport(tip=tip, bundle=destination)
    finally:
        try:
            await runner.run(handle, ["rm", "-f", "--", name], timeout_s=30.0)
        except Exception:
            logger.debug("could not remove a bundle from the workspace", exc_info=True)
