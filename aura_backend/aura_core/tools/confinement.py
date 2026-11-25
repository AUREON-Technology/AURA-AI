"""One definition of "a path inside the workspace root" for the file-reading tools.

``read_file`` and ``list_files`` (``tools/builtin.py``) and the ADR-042 readers (``tools/media.py``:
``ocr_image``, ``inspect_spreadsheet``, ``parse_document``, ``transcribe_audio``,
``analyze_image_requirements``) all take a path relative to ``AURA_TOOLS_FILES_ROOT``. They share
this function so the rule cannot drift between them: the path is joined to the root and then
**resolved, following symlinks**, and only the resolved result is compared with the root. Checking
before resolution is the classic bypass, since a symlink inside the root can point anywhere.
"""

from __future__ import annotations

from pathlib import Path

from ..domain.errors import ToolExecutionError
from ..domain.files import strip_absolute_prefix

__all__ = ["resolve_within"]


def resolve_within(root: Path, relative: str, tool_name: str) -> Path:
    """``relative`` resolved under ``root`` (itself already resolved), or a ``ToolExecutionError``."""
    if "\x00" in relative:
        raise ToolExecutionError("path contains a null byte", tool=tool_name)
    try:
        candidate = (root / strip_absolute_prefix(relative)).resolve()
    except (OSError, RuntimeError, ValueError) as exc:  # symlink loop, over-long name
        raise ToolExecutionError(f"{relative} cannot be resolved", tool=tool_name) from exc
    if candidate != root and root not in candidate.parents:
        raise ToolExecutionError(
            "path escapes the workspace directory", tool=tool_name, details={"requested": relative},
        )
    return candidate
