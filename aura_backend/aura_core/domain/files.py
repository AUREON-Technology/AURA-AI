"""Workspace path confinement helpers.

Three resolvers -- the two sandbox runners (``adapters/coding_fakes.py``,
``adapters/docker_sandbox.py``) and the read-only file tools
(``tools/builtin.py``) -- shared one ``relative.lstrip("/")`` idiom for mapping
an absolute model-supplied path inside the workspace. That is correct on POSIX
but a no-op on Windows, where an absolute path carries a drive letter
(``C:\\...``): joining it discards the workspace root entirely, so the
containment check then refuses a write the caller expected to be confined and
the mapping silently differs by platform. Strip the platform's absolute prefix
first so the mapping is identical everywhere.

Stdlib only, like the rest of ``domain/`` -- see ADR-001.
"""

from __future__ import annotations

import os
import re

_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")


def strip_absolute_prefix(relative: str) -> str:
    """Return ``relative`` with any absolute prefix removed.

    POSIX behaviour is exactly the old ``lstrip("/")`` (a leading backslash is
    a legal filename character there and is preserved). On Windows the drive
    prefix (``C:``) and any leading separators (``/`` or ``\\``, covering both
    ``C:\\x`` and UNC ``\\\\server\\x``) are removed so the remainder joins
    under the workspace root instead of discarding it.
    """
    if os.name == "nt":
        return _DRIVE_PREFIX.sub("", relative, count=1).lstrip("/\\")
    return relative.lstrip("/")
