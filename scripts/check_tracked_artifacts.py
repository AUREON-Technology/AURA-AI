#!/usr/bin/env python3
"""Fail if git tracks a build artefact, log, local database or other binary blob (audit F-37/F-29, CB-13).

What this caught when it was written: a 72 MB release APK, a 7.3 MB Windows executable with no
extension (``aura_hostd/aura_hostd``), daemon logs and three 4.8 MB scratch files -- all already
named in ``.gitignore``, all committed anyway with ``git add -f`` or before the rule existed. An
ignore rule does nothing for a file that is already tracked, so this checks the index itself.

Three independent checks over ``git ls-files``:

* **extension** -- packaged apps, native binaries, logs, local databases, scratch files, keystores;
* **content** -- an executable header (PE ``MZ``, ELF, Mach-O), which is how an extension-less
  binary is found;
* **size** -- anything over ``MAX_BYTES``, whatever it is. A binary that belongs in a release
  belongs in a GitHub Release asset or a CI artifact, not in every clone forever.

A file that must stay tracked goes in ``ALLOWLIST`` with the reason. Stdlib only, no network.

    python3 scripts/check_tracked_artifacts.py            # from anywhere inside the checkout
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

FORBIDDEN_SUFFIXES = frozenset({
    # packaged apps and installers
    ".apk", ".aab", ".ipa", ".msi", ".msix", ".dmg", ".appimage",
    # native binaries and libraries
    ".exe", ".dll", ".so", ".dylib", ".o", ".obj", ".a", ".lib",
    # logs and scratch output
    ".log", ".log.err", ".tmp",
    # local databases
    ".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm",
    # signing material (also covered by .gitignore's secrets section)
    ".jks", ".keystore",
    # model weights
    ".gguf", ".safetensors", ".onnx", ".pt", ".pth", ".ckpt",
})

# Executable headers. Java class files share Mach-O's fat-binary magic, which is fine: neither
# belongs in the tree.
EXECUTABLE_MAGIC = (b"MZ", b"\x7fELF", b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe",
                    b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe")

MAX_BYTES = 5 * 1024 * 1024

# path -> why it is allowed. Keep this short; every entry is a decision someone has to defend.
ALLOWLIST: dict[str, str] = {
    # Text, not a binary: a single host-key line left over from the deploy host-key work. Owned by
    # the deploy area; listed here rather than untracked so this check does not silently decide a
    # deploy question. Remove the entry once deploy/ either deletes or renames it.
    "deploy/vps_known_hosts.tmp": "deploy-owned host-key text file (see comment)",
}


def tracked_files(repo: Path) -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=repo, check=True, capture_output=True).stdout
    return [p for p in out.decode("utf-8", "surrogateescape").split("\0") if p]


def find_violations(repo: Path, paths: list[str]) -> list[str]:
    problems: list[str] = []
    for rel in paths:
        if rel in ALLOWLIST:
            continue
        path = repo / rel
        lower = rel.lower()
        suffix = next((s for s in FORBIDDEN_SUFFIXES if lower.endswith(s)), None)
        if suffix:
            problems.append(f"{rel}: tracked '{suffix}' file")
            continue
        # A path in the index can be missing from the worktree (sparse checkout, deleted but not
        # staged); there is nothing to read then, and the extension check above still applied.
        if not path.is_file() or path.is_symlink():
            continue
        size = path.stat().st_size
        if size > MAX_BYTES:
            problems.append(f"{rel}: {size / 1024 / 1024:.1f} MB exceeds the {MAX_BYTES // 1024 // 1024} MB limit")
            continue
        with path.open("rb") as fh:
            head = fh.read(4)
        if any(head.startswith(m) for m in EXECUTABLE_MAGIC):
            problems.append(f"{rel}: executable binary header {head!r}")
    return problems


def repo_root(start: Path) -> Path:
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=start, check=True,
                         capture_output=True, text=True).stdout.strip()
    return Path(out)


def main() -> int:
    repo = repo_root(Path(__file__).resolve().parent)
    stale = sorted(set(ALLOWLIST) - set(tracked_files(repo)))
    problems = find_violations(repo, tracked_files(repo))
    for p in stale:
        problems.append(f"{p}: in ALLOWLIST but no longer tracked -- remove the entry")
    if problems:
        print("Tracked build artefacts / binaries found (untrack with `git rm --cached`, add a "
              ".gitignore rule, or allowlist with a reason in scripts/check_tracked_artifacts.py):")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("No tracked build artefacts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
