"""Dependency files read from the user's workspace, offline (ADR-045).

``tools/open/dependency_manager_validator.py`` and its siblings judge text a caller pastes in. This
module reads what is actually on disk -- manifests, lockfiles, a project's own ``site-packages`` and
``node_modules`` -- and answers the questions a pasted requirements file cannot:

* are the lockfiles there, and does each lockfile agree with its manifest (declared but not locked,
  locked at a version the declared specifier excludes)?
* does the project's *own* virtualenv satisfy what its packages require (a ``pip check`` over the
  ``*.dist-info`` metadata on disk, run by reading files -- never by starting a process)?
* which declared packages does no source file import, and which imports name no declared package?
* what licence does each locked package carry, from the project's own installed metadata first?
* which locked versions an advisory file the caller supplies (OSV JSON) lists as affected?

Nothing here touches the network, spawns a process, imports a project module or writes a file.
There is no bundled advisory database in this repository or in the locked environment, so a
vulnerability result is only ever as good as the advisory file given; ``advisories`` says so.
Every path is resolved through ``confinement.resolve_within`` by the caller; the walkers here never
follow a symlink out of the root.
"""

from __future__ import annotations

import ast
import email.parser
import fnmatch
import json
import os
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

__all__ = [
    "Declared", "Pkg", "LockInfo", "norm_name", "parse_requirements", "parse_lock", "parse_manifest",
    "find_dependency_files", "consistency_report", "installed_distributions", "pip_check_report",
    "import_report", "licence_inventory", "match_advisories", "parse_advisories", "read_text_limited",
    "walk_files", "MAX_TEXT_BYTES",
]

#: Largest dependency/source file read whole. Lockfiles of big projects run to a few MB.
MAX_TEXT_BYTES = 6 * 1024 * 1024
_SKIP_DIRS = frozenset({
    ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", "dist", "build",
    ".dart_tool", ".gradle", "Pods", ".idea", ".next",
})
#: Directories that hold a project's installed third-party code; skipped by source scans.
_INSTALLED_DIRS = frozenset({"node_modules", "site-packages", ".venv", "venv", "env", ".env", "virtualenv"})

_LOCK_NAMES = frozenset({
    "poetry.lock", "uv.lock", "pipfile.lock", "pdm.lock", "package-lock.json", "yarn.lock",
    "pnpm-lock.yaml", "requirements.lock",
})
_MANIFEST_NAMES = frozenset({"pyproject.toml", "pipfile", "package.json", "requirements.txt"})


def norm_name(name: str) -> str:
    """PEP 503 name normalisation (also fine for npm names, which are already lower-case)."""
    return re.sub(r"[-_.]+", "-", str(name)).strip().lower()


# -- data ---------------------------------------------------------------------------------------


@dataclass
class Declared:
    name: str
    spec: str = ""
    ecosystem: str = "pypi"
    dev: bool = False
    pinned: str | None = None
    hashed: bool = False
    kind: str = "registry"  # registry | vcs | url | path
    source: str = ""


@dataclass
class Pkg:
    name: str
    version: str
    ecosystem: str
    hashed: bool | None = None
    kind: str = "registry"
    license: str | None = None
    source: str = ""
    dev: bool = False


@dataclass
class LockInfo:
    path: str
    kind: str
    ecosystem: str
    packages: list[Pkg] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


# -- reading and walking --------------------------------------------------------------------------


def read_text_limited(path: Path, limit: int = MAX_TEXT_BYTES) -> tuple[str | None, str | None]:
    """``(text, None)`` or ``(None, reason)``. Never raises; refuses binary and oversize files."""
    try:
        size = path.stat().st_size
        if size > limit:
            return None, f"{size:,} bytes, over the {limit:,}-byte limit"
        with path.open("rb") as handle:
            data = handle.read(limit + 1)
    except OSError as exc:
        return None, f"cannot be read: {exc.strerror or type(exc).__name__}"
    if b"\x00" in data[:8192]:
        return None, "binary file"
    try:
        return data.decode("utf-8-sig"), None
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace"), None


def walk_files(
    root: Path, start: Path, *, suffixes: Iterable[str] | None = None, names: Iterable[str] | None = None,
    max_files: int = 2000, skip_installed: bool = True, max_depth: int = 12,
) -> tuple[list[Path], bool]:
    """Regular files under ``start`` that resolve inside ``root``; ``(files, truncated)``.

    Directory links are never followed. A file link is kept only when its target is inside ``root``.
    """
    wanted_suffixes = tuple(s.lower() for s in suffixes) if suffixes else None
    wanted_names = {n.lower() for n in names} if names else None
    out: list[Path] = []
    base_depth = len(start.parts)
    for current, dirs, files in os.walk(start, followlinks=False):
        depth = len(Path(current).parts) - base_depth
        dirs[:] = sorted(
            d for d in dirs
            if d not in _SKIP_DIRS and not (skip_installed and d in _INSTALLED_DIRS) and depth < max_depth
        )
        for name in sorted(files):
            lowered = name.lower()
            if wanted_names is not None and lowered not in wanted_names:
                if wanted_suffixes is None or not lowered.endswith(wanted_suffixes):
                    continue
            elif wanted_names is None and wanted_suffixes is not None and not lowered.endswith(wanted_suffixes):
                continue
            path = Path(current) / name
            try:
                if path.is_symlink():
                    resolved = path.resolve()
                    if resolved != root and root not in resolved.parents:
                        continue
                if not path.is_file():
                    continue
            except (OSError, RuntimeError):
                continue
            if len(out) >= max_files:
                return out, True
            out.append(path)
    return out, False


def _rel(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix() or "."
    except ValueError:
        return path.name


# -- requirement-style files ----------------------------------------------------------------------

_REQ_NAME = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*(.*)$")
_HASH = re.compile(r"--hash[= ]\S+")


def _logical_lines(text: str) -> list[str]:
    out: list[str] = []
    buffer = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.endswith("\\"):
            buffer += line[:-1] + " "
            continue
        out.append((buffer + line).strip())
        buffer = ""
    if buffer.strip():
        out.append(buffer.strip())
    return out


def parse_requirements(text: str, source: str = "") -> tuple[list[Declared], list[str]]:
    """Declared packages of a requirements file, and the problems met (unreadable lines, includes)."""
    declared: list[Declared] = []
    problems: list[str] = []
    for line in _logical_lines(text):
        body = re.sub(r"\s+#.*$", "", line).strip()
        if not body or body.startswith("#"):
            continue
        if body.startswith(("-r ", "-c ", "--requirement", "--constraint")):
            problems.append(f"{body.split()[0]} include not followed: {body[:80]}")
            continue
        hashed = bool(_HASH.search(body))
        body = _HASH.sub("", body).strip()
        if body.startswith("-e "):
            body = body[3:].strip()
        if body.startswith("-"):
            continue
        if "://" in body or body.lower().startswith("git+"):
            egg = re.search(r"#egg=([A-Za-z0-9_.\-]+)", body)
            named = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*@", body)
            name = egg.group(1) if egg else named.group(1) if named else body[:80]
            declared.append(Declared(name=name, kind="vcs" if "git+" in body else "url", source=source,
                                     hashed=hashed))
            continue
        marker_split = body.split(";", 1)
        spec_part = marker_split[0].strip()
        match = _REQ_NAME.match(spec_part)
        if not match:
            problems.append(f"line not understood: {body[:80]}")
            continue
        name, _extras, rest = match.group(1), match.group(2), match.group(3).strip()
        spec = rest.replace(" ", "")
        pinned = None
        pin = re.fullmatch(r"===?([A-Za-z0-9_.+!*-]+)", spec)
        if pin and "*" not in pin.group(1):
            pinned = pin.group(1)
        declared.append(Declared(name=name, spec=spec, pinned=pinned, hashed=hashed, source=source))
    return declared, problems


# -- lockfiles ------------------------------------------------------------------------------------


def _toml(text: str) -> dict[str, Any] | None:
    try:
        return tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError, RecursionError):
        return None


def _json(text: str) -> Any:
    try:
        return json.loads(text)
    except (ValueError, RecursionError):
        return None


def parse_lock(rel: str, text: str) -> LockInfo | None:
    """The packages a lockfile pins, or ``None`` when ``rel`` is not a lockfile this module knows."""
    base = rel.rsplit("/", 1)[-1].lower()
    if base in ("poetry.lock", "uv.lock", "pdm.lock"):
        info = LockInfo(rel, base, "pypi")
        data = _toml(text)
        if data is None:
            info.problems.append("not valid TOML")
            return info
        for entry in data.get("package", []) or []:
            if not isinstance(entry, dict) or not entry.get("name") or "version" not in entry:
                continue
            source = entry.get("source") or {}
            kind = "registry"
            if isinstance(source, dict):
                stype = str(source.get("type", "")).lower()
                if "git" in source or stype == "git":
                    kind = "vcs"
                elif any(k in source for k in ("virtual", "editable", "directory")) or stype in ("directory", "file"):
                    kind = "path"
                elif stype == "url" or "url" in source:
                    kind = "url"
            hashed: bool | None
            if base == "poetry.lock":
                files = entry.get("files")
                hashed = bool(files) and all(isinstance(f, dict) and f.get("hash") for f in files)
            elif base == "uv.lock":
                sdist = entry.get("sdist") or {}
                wheels = entry.get("wheels") or []
                items = ([sdist] if sdist else []) + [w for w in wheels if isinstance(w, dict)]
                hashed = bool(items) and all(isinstance(i, dict) and i.get("hash") for i in items)
            else:
                hashed = None
            info.packages.append(Pkg(str(entry["name"]), str(entry["version"]), "pypi", hashed, kind, source=rel))
        return info
    if base == "pipfile.lock":
        info = LockInfo(rel, base, "pypi")
        data = _json(text)
        if not isinstance(data, dict):
            info.problems.append("not valid JSON")
            return info
        for section, dev in (("default", False), ("develop", True)):
            for name, meta in (data.get(section) or {}).items():
                if not isinstance(meta, dict):
                    continue
                kind = "vcs" if "git" in meta else "path" if ("path" in meta or "file" in meta) else "registry"
                info.packages.append(Pkg(name, str(meta.get("version", "")).lstrip("="), "pypi",
                                         bool(meta.get("hashes")), kind, source=rel, dev=dev))
        return info
    if base == "package-lock.json":
        info = LockInfo(rel, base, "npm")
        data = _json(text)
        if not isinstance(data, dict):
            info.problems.append("not valid JSON")
            return info
        packages = data.get("packages")
        if isinstance(packages, dict):
            for key, meta in packages.items():
                if not key or not isinstance(meta, dict) or meta.get("link"):
                    continue
                name = meta.get("name") or key.rsplit("node_modules/", 1)[-1]
                if "version" not in meta:
                    continue
                resolved = str(meta.get("resolved", ""))
                kind = "vcs" if resolved.startswith("git") else "url" if resolved and "registry" not in resolved \
                    and resolved.startswith("http") and "/-/" not in resolved else "registry"
                lic = meta.get("license")
                if isinstance(lic, dict):
                    lic = lic.get("type")
                info.packages.append(Pkg(name, str(meta["version"]), "npm", bool(meta.get("integrity")), kind,
                                         license=str(lic) if lic else None, source=rel, dev=bool(meta.get("dev"))))
        else:
            def walk(deps: Any) -> None:
                for name, meta in (deps or {}).items():
                    if not isinstance(meta, dict):
                        continue
                    info.packages.append(Pkg(name, str(meta.get("version", "")), "npm", bool(meta.get("integrity")),
                                             source=rel, dev=bool(meta.get("dev"))))
                    walk(meta.get("dependencies"))

            walk(data.get("dependencies"))
        return info
    if base == "yarn.lock":
        info = LockInfo(rel, base, "npm")
        for block in re.split(r"\n\s*\n", text):
            header = block.strip().splitlines()[0] if block.strip() else ""
            version = re.search(r'^\s+version:?\s+"?([^"\s]+)"?', block, re.M)
            if not header or header.startswith("#") or not version:
                continue
            first = header.split(",")[0].strip().strip('":')
            name = first.rsplit("@", 1)[0] if "@" in first[1:] else first
            info.packages.append(Pkg(name, version.group(1), "npm", bool(re.search(r"integrity|checksum", block)),
                                     source=rel))
        return info
    if base == "pnpm-lock.yaml":
        info = LockInfo(rel, base, "npm")
        try:
            import yaml  # a locked runtime dependency; imported lazily like every vendor module here

            data = yaml.safe_load(text)
        except Exception:
            info.problems.append("not valid YAML")
            return info
        packages = (data or {}).get("packages") if isinstance(data, dict) else None
        for key, meta in (packages or {}).items():
            text_key = str(key).lstrip("/")
            text_key = re.sub(r"\(.*\)$", "", text_key)
            if "@" not in text_key[1:]:
                continue
            name, version = text_key.rsplit("@", 1)
            integrity = bool(isinstance(meta, dict) and isinstance(meta.get("resolution"), dict)
                             and meta["resolution"].get("integrity"))
            info.packages.append(Pkg(name, version, "npm", integrity, source=rel))
        return info
    if base == "requirements.lock" or (base.startswith("requirements") and base.endswith((".lock", ".txt"))):
        declared, problems = parse_requirements(text, rel)
        info = LockInfo(rel, "requirements", "pypi", problems=problems)
        for d in declared:
            if d.pinned:
                info.packages.append(Pkg(d.name, d.pinned, "pypi", d.hashed, d.kind, source=rel))
        return info
    return None


# -- manifests ------------------------------------------------------------------------------------


def parse_manifest(rel: str, text: str) -> tuple[str, list[Declared]] | None:
    """``(ecosystem, declared)`` for a manifest, or ``None`` if ``rel`` is not one."""
    base = rel.rsplit("/", 1)[-1].lower()
    if base == "pyproject.toml":
        data = _toml(text) or {}
        out: list[Declared] = []

        def add_list(items: Any, dev: bool) -> None:
            for line in items or []:
                if isinstance(line, str):
                    got, _ = parse_requirements(line, rel)
                    for d in got:
                        d.dev = dev
                        out.append(d)

        project = data.get("project") or {}
        add_list(project.get("dependencies"), False)
        for group in (project.get("optional-dependencies") or {}).values():
            add_list(group, True)
        for group in (data.get("dependency-groups") or {}).values():
            add_list([g for g in group if isinstance(g, str)], True)
        poetry = (data.get("tool") or {}).get("poetry") or {}

        def add_poetry(table: Any, dev: bool) -> None:
            for name, meta in (table or {}).items():
                if name.lower() == "python":
                    continue
                spec = meta if isinstance(meta, str) else (meta.get("version", "") if isinstance(meta, dict) else "")
                kind = "vcs" if isinstance(meta, dict) and "git" in meta else \
                    "path" if isinstance(meta, dict) and "path" in meta else "registry"
                out.append(Declared(name, _poetry_spec(str(spec)), dev=dev, kind=kind, source=rel))

        add_poetry(poetry.get("dependencies"), False)
        add_poetry(poetry.get("dev-dependencies"), True)
        for group in (poetry.get("group") or {}).values():
            if isinstance(group, dict):
                add_poetry(group.get("dependencies"), True)
        return "pypi", out
    if base == "pipfile":
        data = _toml(text) or {}
        out = []
        for section, dev in (("packages", False), ("dev-packages", True)):
            for name, meta in (data.get(section) or {}).items():
                spec = meta if isinstance(meta, str) else (meta.get("version", "") if isinstance(meta, dict) else "")
                out.append(Declared(name, "" if spec == "*" else str(spec).replace(" ", ""), dev=dev, source=rel))
        return "pypi", out
    if base == "package.json":
        data = _json(text)
        if not isinstance(data, dict):
            return "npm", []
        out = []
        for section, dev in (("dependencies", False), ("optionalDependencies", False), ("peerDependencies", False),
                             ("devDependencies", True)):
            for name, spec in (data.get(section) or {}).items():
                spec = str(spec)
                kind = "vcs" if spec.startswith(("git", "github:")) else \
                    "url" if spec.startswith(("http:", "https:")) else \
                    "path" if spec.startswith(("file:", "link:", "workspace:")) else "registry"
                out.append(Declared(name, spec, "npm", dev, kind=kind, source=rel))
        return "npm", out
    if base == "requirements.txt" or (base.startswith("requirements") and base.endswith(".txt")):
        declared, _ = parse_requirements(text, rel)
        for d in declared:
            d.dev = "dev" in base or "test" in base
        return "pypi", declared
    return None


def _poetry_spec(spec: str) -> str:
    """A Poetry constraint (``^1.2``, ``~1.2.3``, ``1.*``) as a PEP 440 specifier set; ``''`` for any."""
    spec = spec.strip()
    if spec in ("", "*"):
        return ""
    parts = []
    for piece in spec.split(","):
        piece = piece.strip()
        m = re.fullmatch(r"([\^~])\s*(\d+)(?:\.(\d+))?(?:\.(\d+))?", piece)
        if m:
            op, major, minor, patch = m.group(1), int(m.group(2)), m.group(3), m.group(4)
            lower = f"{major}.{minor or 0}.{patch or 0}"
            if op == "^":
                if major > 0 or minor is None:
                    upper = f"{major + 1}.0.0"
                elif int(minor) > 0 or patch is None:
                    upper = f"0.{int(minor) + 1}.0"
                else:
                    upper = f"0.0.{int(patch) + 1}"
            else:
                upper = f"{major}.{int(minor) + 1}.0" if minor is not None else f"{major + 1}.0.0"
            parts.append(f">={lower},<{upper}")
        elif re.fullmatch(r"\d[\w.]*", piece):
            parts.append(f"=={piece}")
        else:
            parts.append(piece.replace(" ", ""))
    return ",".join(parts)


# -- version logic ------------------------------------------------------------------------------


_PEP440 = re.compile(
    r"^\s*v?(?:(?P<epoch>\d+)!)?(?P<release>\d+(?:\.\d+)*)"
    r"(?:[-_.]?(?P<pre_l>alpha|beta|preview|pre|rc|a|b|c)[-_.]?(?P<pre_n>\d+)?)?"
    r"(?:-(?P<post_implicit>\d+)|[-_.]?(?P<post_l>post|rev|r)[-_.]?(?P<post_n>\d+)?)?"
    r"(?:[-_.]?(?P<dev_l>dev)[-_.]?(?P<dev_n>\d+)?)?(?:\+(?P<local>[a-z0-9]+(?:[-_.][a-z0-9]+)*))?\s*$", re.I)
_PRE_RANK = {"a": 0, "alpha": 0, "b": 1, "beta": 1, "c": 2, "rc": 2, "pre": 2, "preview": 2}
_INF = float("inf")


def pep440_key(text: str) -> tuple | None:
    """A sortable key for a PEP 440 version (``None`` if it is not one). Local labels are ignored."""
    m = _PEP440.match(text or "")
    if not m:
        return None
    release = tuple(int(x) for x in m.group("release").split("."))
    while len(release) > 1 and release[-1] == 0:
        release = release[:-1]
    pre = (_PRE_RANK[m.group("pre_l").lower()], int(m.group("pre_n") or 0)) if m.group("pre_l") else None
    post_n = m.group("post_n") if m.group("post_n") is not None else m.group("post_implicit")
    post = int(post_n or 0) if (m.group("post_l") or post_n is not None) else None
    dev = int(m.group("dev_n") or 0) if m.group("dev_l") else None
    if pre is None and post is None and dev is not None:
        pre_key: Any = (-_INF, 0)
    elif pre is None:
        pre_key = (_INF, 0)
    else:
        pre_key = pre
    return (int(m.group("epoch") or 0), release, pre_key, -_INF if post is None else post, _INF if dev is None else dev)


def _wildcard_release(text: str) -> tuple[int, ...] | None:
    m = re.fullmatch(r"\s*v?(?:\d+!)?(\d+(?:\.\d+)*)\.\*\s*", text)
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def _py_satisfies(version: str, spec: str) -> bool | None:
    """Whether ``version`` satisfies a PEP 440 specifier set (comma separated); ``None`` if either is unparseable."""
    if not spec:
        return True
    key = pep440_key(version)
    if key is None:
        return None
    release = tuple(int(x) for x in re.match(r"\s*v?(?:\d+!)?(\d+(?:\.\d+)*)", version).group(1).split("."))
    for clause in [c.strip() for c in spec.split(",") if c.strip()]:
        m = re.fullmatch(r"(===|==|!=|~=|<=|>=|<|>)\s*(.+)", clause)
        if not m:
            return None
        op, rhs = m.group(1), m.group(2).strip()
        wild = _wildcard_release(rhs)
        if wild is not None and op in ("==", "!="):
            padded = release + (0,) * max(0, len(wild) - len(release))
            hit = padded[:len(wild)] == wild
            if (op == "==") != hit:
                return False
            continue
        other = pep440_key(rhs)
        if other is None:
            return None
        if op == "===":
            ok = version.strip() == rhs
        elif op == "==":
            ok = key == other
        elif op == "!=":
            ok = key != other
        elif op == "<=":
            ok = key <= other
        elif op == ">=":
            ok = key >= other
        elif op == "<":
            # PEP 440: "<V" does not match a pre-release of V itself.
            ok = key < other and not (key[:2] == other[:2] and other[2] == (_INF, 0) and other[4] == _INF
                                      and (key[2] != (_INF, 0) or key[4] != _INF))
        elif op == ">":
            # ... and ">V" does not match a post-release of V.
            ok = key > other and not (key[:2] == other[:2] and other[3] == -_INF and key[3] != -_INF)
        else:  # ~= : at least rhs, and the same release up to its second-to-last segment
            rel = tuple(int(x) for x in re.match(r"\s*v?(?:\d+!)?(\d+(?:\.\d+)*)", rhs).group(1).split("."))
            if len(rel) < 2:
                return None
            prefix = rel[:-1]
            padded = release + (0,) * max(0, len(prefix) - len(release))
            ok = key >= other and key[0] == other[0] and padded[:len(prefix)] == prefix
        if not ok:
            return False
    return True


def _semver(text: str) -> tuple[int, int, int] | None:
    m = re.match(r"^v?(\d+)(?:\.(\d+|[xX*]))?(?:\.(\d+|[xX*]))?", text.strip())
    if not m:
        return None
    parts = [int(g) if g and g.isdigit() else 0 for g in m.groups()]
    return parts[0], parts[1], parts[2]


def _node_satisfies(version: str, spec: str) -> bool | None:
    """Whether ``version`` satisfies a simple npm range; ``None`` when the range is not one we parse."""
    v = _semver(version)
    spec = spec.strip()
    if v is None:
        return None
    if spec in ("", "*", "x", "latest") or spec.startswith(("npm:",)):
        return True if spec in ("", "*", "x", "latest") else None
    if re.match(r"^(git|github|http|file|link|workspace|[\w.-]+/[\w.-]+$)", spec):
        return None
    for alternative in spec.split("||"):
        ok = True
        known = True
        for comp in re.findall(r"(\^|~|>=|<=|>|<|=)?\s*(v?\d+(?:\.(?:\d+|[xX*]))?(?:\.(?:\d+|[xX*]))?[\w.+-]*)",
                               alternative):
            op, ver = comp
            t = _semver(ver)
            if t is None:
                known = False
                break
            wild_minor = bool(re.fullmatch(r"v?\d+(?:\.[xX*])?", ver.split("-")[0]))
            wild_patch = bool(re.fullmatch(r"v?\d+\.(?:\d+|[xX*])(?:\.[xX*])?", ver.split("-")[0])) and ver.count(".") < 2 \
                or ver.endswith((".x", ".X", ".*"))
            if op == "^":
                upper = (t[0] + 1, 0, 0) if t[0] > 0 else (0, t[1] + 1, 0) if t[1] > 0 else (0, 0, t[2] + 1)
                ok = ok and t <= v < upper
            elif op == "~":
                ok = ok and t <= v < (t[0], t[1] + 1, 0)
            elif op == ">=":
                ok = ok and v >= t
            elif op == "<=":
                ok = ok and v <= t
            elif op == ">":
                ok = ok and v > t
            elif op == "<":
                ok = ok and v < t
            else:
                if wild_minor:
                    ok = ok and v[0] == t[0]
                elif wild_patch:
                    ok = ok and v[:2] == t[:2]
                else:
                    ok = ok and v == t
        if known and ok:
            return True
        if not known:
            return None
    return False


# -- discovering dependency files --------------------------------------------------------------


def find_dependency_files(root: Path, start: Path, *, max_files: int = 400) -> tuple[list[Path], list[Path], bool]:
    """``(manifests, lockfiles, truncated)`` under ``start`` (installed-package trees skipped)."""
    names = _LOCK_NAMES | _MANIFEST_NAMES
    found, truncated = walk_files(root, start, names=names, suffixes=(".txt", ".lock"), max_files=max_files * 4)
    manifests: list[Path] = []
    locks: list[Path] = []
    for path in found:
        lowered = path.name.lower()
        if lowered in _LOCK_NAMES or (lowered.startswith("requirements") and lowered.endswith(".lock")):
            locks.append(path)
        elif lowered in _MANIFEST_NAMES or (lowered.startswith("requirements") and lowered.endswith(".txt")):
            manifests.append(path)
    return manifests[:max_files], locks[:max_files], truncated


def consistency_report(root: Path, files: list[Path]) -> dict[str, Any]:
    """Manifest-versus-lockfile agreement for the dependency files in ``files`` (grouped per folder)."""
    by_dir: dict[Path, dict[str, list[Path]]] = {}
    for path in files:
        group = by_dir.setdefault(path.parent, {"manifests": [], "locks": []})
        lowered = path.name.lower()
        if lowered in _LOCK_NAMES or (lowered.startswith("requirements") and lowered.endswith(".lock")):
            group["locks"].append(path)
        else:
            group["manifests"].append(path)
    projects: list[dict[str, Any]] = []
    for folder, group in sorted(by_dir.items()):
        entry: dict[str, Any] = {
            "folder": _rel(root, folder),
            "manifests": [_rel(root, p) for p in group["manifests"]],
            "lockfiles": [_rel(root, p) for p in group["locks"]],
            "problems": [], "declared_not_locked": [], "version_mismatches": [], "locked_duplicates": [],
            "unlocked_ecosystems": [], "unpinned_or_unhashed": [],
        }
        locks: list[LockInfo] = []
        for path in group["locks"]:
            text, err = read_text_limited(path)
            if text is None:
                entry["problems"].append(f"{_rel(root, path)}: {err}")
                continue
            info = parse_lock(_rel(root, path), text)
            if info is not None:
                locks.append(info)
                entry["problems"].extend(f"{info.path}: {p}" for p in info.problems)
                if not info.packages:
                    entry["problems"].append(f"{info.path}: pins no packages")
        declared_by_eco: dict[str, list[Declared]] = {}
        for path in group["manifests"]:
            text, err = read_text_limited(path)
            if text is None:
                entry["problems"].append(f"{_rel(root, path)}: {err}")
                continue
            parsed = parse_manifest(_rel(root, path), text)
            if parsed is not None:
                declared_by_eco.setdefault(parsed[0], []).extend(parsed[1])
        locked_by_eco: dict[str, dict[str, list[Pkg]]] = {}
        for info in locks:
            table = locked_by_eco.setdefault(info.ecosystem, {})
            for pkg in info.packages:
                table.setdefault(norm_name(pkg.name), []).append(pkg)
        for eco, declared in declared_by_eco.items():
            table = locked_by_eco.get(eco)
            if table is None:
                if declared:
                    entry["unlocked_ecosystems"].append(eco)
                continue
            for d in declared:
                if d.kind != "registry" and d.kind != "vcs":
                    continue
                hits = table.get(norm_name(d.name))
                if not hits:
                    entry["declared_not_locked"].append({"package": d.name, "spec": d.spec, "in": d.source})
                    continue
                if d.kind == "vcs":
                    continue
                versions = sorted({p.version for p in hits})
                check = _py_satisfies if eco == "pypi" else _node_satisfies
                verdicts = [check(v, d.spec) for v in versions]
                if verdicts and all(v is False for v in verdicts):
                    entry["version_mismatches"].append(
                        {"package": d.name, "declared": d.spec, "locked": versions, "in": d.source})
        for eco, table in locked_by_eco.items():
            if eco != "pypi":
                continue  # npm legitimately nests several versions of one package
            for key, pkgs in table.items():
                versions = sorted({p.version for p in pkgs if p.kind != "path"})
                if len(versions) > 1:
                    entry["locked_duplicates"].append({"package": key, "versions": versions})
            unhashed = [p.name for pkgs in table.values() for p in pkgs if p.hashed is False and p.kind == "registry"]
            if unhashed:
                entry["unpinned_or_unhashed"].append(
                    {"ecosystem": eco, "without_hashes": len(unhashed), "examples": sorted(unhashed)[:10]})
        projects.append(entry)
    return {"projects": projects}


# -- the project's own installed packages ---------------------------------------------------------


@dataclass
class Dist:
    name: str
    version: str
    requires: list[str]
    license: str | None
    top_level: list[str]
    path: str


_CLASSIFIER_LICENSES = {
    "MIT License": "MIT", "Apache Software License": "Apache-2.0", "BSD License": "BSD-3-Clause",
    "ISC License (ISCL)": "ISC", "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "GNU General Public License v3 (GPLv3)": "GPL-3.0", "GNU General Public License v2 (GPLv2)": "GPL-2.0",
    "GNU General Public License (GPL)": "GPL", "GNU Lesser General Public License v3 (LGPLv3)": "LGPL-3.0",
    "GNU Lesser General Public License v2 or later (LGPLv2+)": "LGPL-2.1",
    "GNU Affero General Public License v3": "AGPL-3.0", "Python Software Foundation License": "PSF-2.0",
    "The Unlicense (Unlicense)": "Unlicense", "Public Domain": "Public Domain",
}


def _dist_license(msg: Any) -> str | None:
    expr = msg.get("License-Expression")
    if expr:
        return str(expr).strip()
    classifiers = [c for c in (msg.get_all("Classifier") or []) if c.startswith("License ::")]
    mapped = []
    for c in classifiers:
        tail = c.split("::")[-1].strip()
        mapped.append(_CLASSIFIER_LICENSES.get(tail, tail))
    mapped = [m for m in mapped if m and m != "OSI Approved"]
    lic = msg.get("License")
    if lic and "\n" not in lic.strip() and len(lic.strip()) <= 100 and lic.strip().upper() not in ("UNKNOWN", ""):
        return lic.strip()
    if mapped:
        return " OR ".join(dict.fromkeys(mapped)) if len(mapped) > 1 else mapped[0]
    return None


def installed_distributions(root: Path, start: Path, *, max_dists: int = 4000) -> tuple[list[Dist], dict[str, Any]]:
    """Distributions installed in virtualenvs found under ``start`` (``*.dist-info/METADATA`` on disk)."""
    parser = email.parser.HeaderParser()
    dists: list[Dist] = []
    envs: list[dict[str, Any]] = []
    truncated = False
    for current, dirs, _files in os.walk(start, followlinks=False):
        here = Path(current)
        depth = len(here.parts) - len(start.parts)
        if here.name == "site-packages":
            dirs[:] = []
            try:
                resolved = here.resolve()
                if resolved != root and root not in resolved.parents:
                    continue
                entries = sorted(here.iterdir())
            except OSError:
                continue
            env = {"site_packages": _rel(root, here), "distributions": 0, "python": None}
            cfg = None
            for parent in list(here.parents)[:5]:
                if (parent / "pyvenv.cfg").is_file():
                    cfg = parent / "pyvenv.cfg"
                    break
            if cfg is not None:
                text, _ = read_text_limited(cfg, 20_000)
                m = re.search(r"^version(?:_info)?\s*=\s*(\d+\.\d+(?:\.\d+)?)", text or "", re.M)
                env["python"] = m.group(1) if m else None
            for entry in entries:
                if not entry.name.endswith(".dist-info"):
                    continue
                meta = entry / "METADATA"
                try:
                    if entry.is_symlink() or not meta.is_file() or meta.stat().st_size > 2_000_000:
                        continue
                    with meta.open("r", encoding="utf-8", errors="replace") as handle:
                        head = handle.read(131_072)
                except OSError:
                    continue
                # Headers end at the first blank line; the long description after it is never parsed.
                msg = parser.parsestr(head.split("\n\n", 1)[0])
                name, version = msg.get("Name"), msg.get("Version")
                if not name or not version:
                    continue
                top: list[str] = []
                try:
                    tl = entry / "top_level.txt"
                    if tl.is_file() and tl.stat().st_size < 100_000:
                        top = [ln.strip() for ln in tl.read_text(encoding="utf-8", errors="replace").splitlines()
                               if ln.strip()]
                except OSError:
                    pass
                if len(dists) >= max_dists:
                    truncated = True
                    break
                dists.append(Dist(name, version, list(msg.get_all("Requires-Dist") or []), _dist_license(msg), top,
                                  _rel(root, entry)))
                env["distributions"] += 1
            envs.append(env)
            continue
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS and d != "node_modules" and depth < 7)
    return dists, {"environments": envs, "truncated": truncated}


_REQ_LINE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*\(?\s*([^;()]*?)\s*\)?\s*(?:;(.*))?$")


def pip_check_report(dists: list[Dist], envs: dict[str, Any]) -> dict[str, Any]:
    """What ``pip check`` would report for the distributions found on disk, computed from metadata.

    A requirement carrying an environment marker (``; python_version < "3.9"``) is not evaluated -- it is
    counted under ``conditional_requirements_not_evaluated`` -- so a marker can never produce a false alarm.
    """
    python = next((e["python"] for e in envs.get("environments", []) if e.get("python")), None)
    installed: dict[str, list[Dist]] = {}
    for d in dists:
        installed.setdefault(norm_name(d.name), []).append(d)
    broken: list[dict[str, Any]] = []
    conditional = 0
    unparsed = 0
    multiple = [{"package": k, "versions": sorted({d.version for d in v})}
                for k, v in installed.items() if len({d.version for d in v}) > 1]
    for d in dists:
        for line in d.requires:
            m = _REQ_LINE.match(line)
            if not m:
                unparsed += 1
                continue
            name, spec, marker = m.group(1), m.group(2).replace(" ", ""), m.group(3)
            if marker is not None and marker.strip():
                conditional += 1
                continue
            have = installed.get(norm_name(name))
            if not have:
                broken.append({"package": d.name, "version": d.version, "requires": line.strip(),
                               "problem": "not installed"})
                continue
            verdicts = [_py_satisfies(h.version, spec) for h in have]
            if all(v is False for v in verdicts):
                broken.append({"package": d.name, "version": d.version, "requires": line.strip(),
                               "problem": f"installed {sorted({h.version for h in have})[0]}"})
    return {
        "performed": True, "source": "dist-info metadata in the workspace (no process was started)",
        "python_assumed": python or "unknown (no pyvenv.cfg found)",
        "distributions_checked": len(dists), "healthy": not broken, "broken_requirements": broken[:100],
        "broken_count": len(broken), "multiple_versions_installed": multiple[:50],
        "conditional_requirements_not_evaluated": conditional, "requirements_not_understood": unparsed,
    }


# -- imports -------------------------------------------------------------------------------------

_IMPORT_TO_DIST = {
    "yaml": "pyyaml", "pil": "pillow", "cv2": "opencv-python", "sklearn": "scikit-learn", "bs4": "beautifulsoup4",
    "dateutil": "python-dateutil", "jwt": "pyjwt", "dotenv": "python-dotenv", "attr": "attrs", "magic": "python-magic",
    "serial": "pyserial", "usb": "pyusb", "git": "gitpython", "mysqldb": "mysqlclient", "crypto": "pycryptodome",
    "google": "protobuf", "multipart": "python-multipart", "jose": "python-jose", "fitz": "pymupdf",
    "docx": "python-docx", "pptx": "python-pptx", "skimage": "scikit-image", "ruamel": "ruamel-yaml",
    "psycopg2": "psycopg2-binary", "tomllib": "tomli", "markdown": "markdown", "firebase_admin": "firebase-admin",
}
_TOOLING = (
    "pytest*", "ruff", "mypy", "black", "flake8", "isort", "coverage", "pre-commit", "tox", "wheel", "setuptools",
    "pip", "build", "twine", "hatchling", "poetry-core", "uv", "pip-tools", "sphinx*", "types-*", "*-stubs",
    "mkdocs*", "pylint", "bandit", "pip-audit", "nox", "gunicorn", "uvicorn*", "typing-extensions", "eslint*",
    "prettier", "typescript", "@types/*", "jest*", "vitest*", "webpack*", "vite*", "babel*", "@babel/*",
    "ts-node", "tsx", "nodemon", "husky", "lint-staged", "rimraf", "cross-env", "concurrently", "playwright*",
    "@playwright/*", "tailwindcss", "postcss", "autoprefixer", "sass", "next", "react-scripts",
)
_JS_IMPORT = re.compile(
    r"""(?:\bfrom\s*|\bimport\s*\(?\s*|\brequire\s*\(\s*|\bimport\s+)['"]([^'"\s]+)['"]""")


def _stdlib_names() -> frozenset[str]:
    return frozenset(getattr(sys, "stdlib_module_names", ())) | {"__future__"}


def python_imports(text: str) -> tuple[set[str], set[str]]:
    """``(absolute top-level imports, relative-import module names)`` of a source text; ``set()`` if unparseable."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return set(), set()
    absolute: set[str] = set()
    relative: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            absolute.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                absolute.add(node.module.split(".")[0])
            elif node.module:
                relative.add(node.module.split(".")[0])
    return absolute, relative


def js_imports(text: str) -> set[str]:
    out: set[str] = set()
    for spec in _JS_IMPORT.findall(text):
        if spec.startswith((".", "/", "node:", "#", "~", "@/")):
            continue
        parts = spec.split("/")
        out.add("/".join(parts[:2]) if spec.startswith("@") and len(parts) > 1 else parts[0])
    return out


def import_report(
    root: Path, start: Path, declared_py: list[Declared], declared_js: list[Declared], dists: list[Dist],
    *, max_files: int = 1500,
) -> dict[str, Any]:
    """Declared packages no source imports, and imports no declared package (or workspace module) explains."""
    py_files, py_trunc = walk_files(root, start, suffixes=(".py",), max_files=max_files)
    js_files, js_trunc = walk_files(root, start, suffixes=(".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue"),
                                    max_files=max_files)
    imported: dict[str, set[str]] = {}
    local_modules: set[str] = set()
    for path in py_files:
        local_modules.add(path.stem)
        local_modules.add(path.parent.name)
    for path in py_files:
        text, _ = read_text_limited(path, 1_000_000)
        if text is None:
            continue
        absolute, _relative = python_imports(text)
        for name in absolute:
            imported.setdefault(name, set()).add(_rel(root, path))
    stdlib = _stdlib_names()
    top_levels: dict[str, str] = {}
    for d in dists:
        for tl in d.top_level or [re.sub(r"[-.]", "_", d.name)]:
            top_levels[tl.lower()] = norm_name(d.name)
    declared_names = {norm_name(d.name) for d in declared_py}

    def dist_for(import_name: str) -> str:
        low = import_name.lower()
        return top_levels.get(low) or _IMPORT_TO_DIST.get(low) or norm_name(import_name)

    used_dists = {dist_for(n) for n in imported if n not in stdlib}
    # Which workspace directory holds the package an import names (``app`` for ``app/main.py``)?
    local_names = {n.lower() for n in local_modules}
    undeclared = sorted(
        n for n in imported
        if n not in stdlib and n.lower() not in local_names and dist_for(n) not in declared_names
        and not n.startswith("_")
    ) if declared_py else []
    unused_py = sorted({
        d.name for d in declared_py
        if norm_name(d.name) not in used_dists and not any(fnmatch.fnmatch(norm_name(d.name), t) for t in _TOOLING)
        and d.kind == "registry"
    }) if py_files else []

    js_imported: dict[str, set[str]] = {}
    for path in js_files:
        text, _ = read_text_limited(path, 1_000_000)
        if text is None:
            continue
        for name in js_imports(text):
            js_imported.setdefault(name, set()).add(_rel(root, path))
    unused_js = sorted({
        d.name for d in declared_js
        if d.name not in js_imported and not any(fnmatch.fnmatch(d.name, t) for t in _TOOLING) and d.kind == "registry"
    }) if js_files else []
    undeclared_js = sorted(
        n for n in js_imported if declared_js and n not in {d.name for d in declared_js}
        and not n.startswith(("@types/",)) and n not in {"react-dom/client"}
    )[:100]
    return {
        "python": {
            "files_scanned": len(py_files), "truncated": py_trunc,
            "possibly_unused_declared": unused_py[:100],
            "imports_not_declared": undeclared[:100],
        },
        "javascript": {
            "files_scanned": len(js_files), "truncated": js_trunc,
            "possibly_unused_declared": unused_js[:100], "imports_not_declared": undeclared_js,
        },
        "caveat": ("name matching is heuristic: an import name can differ from its package name, and tools run "
                   "from the command line (linters, servers, plugins) are never imported. Treat 'possibly_unused' "
                   "as a lead to check, not as proof."),
    }


# -- licences ------------------------------------------------------------------------------------


def node_modules_licences(root: Path, start: Path, *, max_packages: int = 6000) -> tuple[dict[tuple[str, str], str], int]:
    """``{(name, version): licence}`` read from ``node_modules/**/package.json`` under ``start``."""
    out: dict[tuple[str, str], str] = {}
    seen = 0
    for current, dirs, _files in os.walk(start, followlinks=False):
        here = Path(current)
        if here.name == "node_modules":
            dirs[:] = []
            try:
                resolved = here.resolve()
                if resolved != root and root not in resolved.parents:
                    continue
                children = sorted(here.iterdir())
            except OSError:
                continue
            candidates: list[Path] = []
            for child in children:
                if child.name.startswith("@") and child.is_dir() and not child.is_symlink():
                    try:
                        candidates.extend(sorted(child.iterdir()))
                    except OSError:
                        pass
                elif not child.name.startswith("."):
                    candidates.append(child)
            for pkg_dir in candidates:
                manifest = pkg_dir / "package.json"
                try:
                    if pkg_dir.is_symlink() or not manifest.is_file():
                        continue
                except OSError:
                    continue
                text, _ = read_text_limited(manifest, 1_000_000)
                data = _json(text) if text else None
                if not isinstance(data, dict) or not data.get("name"):
                    continue
                lic = data.get("license")
                if isinstance(lic, dict):
                    lic = lic.get("type")
                if not lic and isinstance(data.get("licenses"), list):
                    lic = " OR ".join(str(x.get("type", x)) if isinstance(x, dict) else str(x) for x in data["licenses"])
                seen += 1
                if lic:
                    out[(str(data["name"]), str(data.get("version", "")))] = str(lic)
                if seen >= max_packages:
                    return out, seen
            continue
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS and d != "site-packages"
                         and not d.startswith(".") and len(here.parts) - len(start.parts) < 7)
    return out, seen


def gateway_license(name: str, version: str) -> str | None:
    """Licence of the *same name and version* installed in the gateway's own environment, if any."""
    try:
        from importlib import metadata

        dist = metadata.distribution(name)
        if str(dist.version) != version:
            return None
        return _dist_license(dist.metadata)
    except Exception:
        return None


def licence_inventory(
    root: Path, start: Path, locks: list[LockInfo], dists: list[Dist],
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """One ``{package, license, source}`` per locked package, plus how each licence was found."""
    from .open.dependency_license_compliance_scanner import DependencyLicenseComplianceScanner

    node_lic, node_seen = node_modules_licences(root, start)
    dist_lic = {(norm_name(d.name), d.version): d.license for d in dists if d.license}
    dist_lic_by_name = {norm_name(d.name): d.license for d in dists if d.license}
    entries: list[dict[str, str]] = []
    sources: dict[str, int] = {}
    seen_keys: set[tuple[str, str, str]] = set()
    for lock in locks:
        for pkg in lock.packages:
            key = (pkg.ecosystem, norm_name(pkg.name), pkg.version)
            if key in seen_keys or pkg.kind == "path":
                continue
            seen_keys.add(key)
            lic, how = None, ""
            if pkg.license:
                lic, how = pkg.license, "lockfile"
            elif pkg.ecosystem == "npm" and (pkg.name, pkg.version) in node_lic:
                lic, how = node_lic[(pkg.name, pkg.version)], "workspace node_modules"
            elif pkg.ecosystem == "pypi" and (norm_name(pkg.name), pkg.version) in dist_lic:
                lic, how = dist_lic[(norm_name(pkg.name), pkg.version)], "workspace site-packages metadata"
            elif pkg.ecosystem == "pypi" and norm_name(pkg.name) in dist_lic_by_name and not dists == []:
                lic, how = dist_lic_by_name[norm_name(pkg.name)], "workspace site-packages metadata (other version)"
            if lic is None and pkg.ecosystem == "pypi":
                got = gateway_license(pkg.name, pkg.version)
                if got:
                    lic, how = got, "gateway-installed metadata (same name and version)"
            if lic is None:
                guess = DependencyLicenseComplianceScanner.infer_package_license(pkg.name)
                if guess and guess.upper() not in ("UNKNOWN", ""):
                    lic, how = guess, "built-in table (inferred, not read from the package)"
            if lic is None:
                lic, how = "UNKNOWN", "not found offline"
            sources[how] = sources.get(how, 0) + 1
            entries.append({"package": f"{pkg.name}=={pkg.version}", "license": lic, "source": how,
                            "ecosystem": pkg.ecosystem})
    return entries, {"by_source": sources, "node_modules_packages_read": node_seen,
                     "python_distributions_read": len(dists)}


def supply_chain_report(locks: list[LockInfo]) -> dict[str, Any]:
    """Facts about the lockfiles that bear on supply-chain risk, none of which needs a registry."""
    total = unhashed = 0
    non_registry: list[dict[str, str]] = []
    for lock in locks:
        for pkg in lock.packages:
            if pkg.kind == "path":
                continue
            total += 1
            if pkg.kind in ("vcs", "url"):
                non_registry.append({"package": pkg.name, "version": pkg.version, "kind": pkg.kind, "in": lock.path})
            if pkg.hashed is False:
                unhashed += 1
    return {
        "locked_packages": total, "without_integrity_hash": unhashed,
        "from_vcs_or_url": non_registry[:50],
        "outdated_check": {"performed": False,
                           "reason": "needs a package registry; this tool makes no network calls"},
    }


# -- advisories (OSV JSON the caller supplies) ---------------------------------------------------


def parse_advisories(text: str) -> tuple[list[dict[str, Any]], str | None]:
    data = _json(text)
    if data is None:
        return [], "the advisory file is not valid JSON"
    if isinstance(data, dict):
        for key in ("vulns", "advisories", "vulnerabilities", "results"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            data = [data]
    if not isinstance(data, list):
        return [], "the advisory file is not an OSV object or a list of them"
    advisories = [a for a in data if isinstance(a, dict) and isinstance(a.get("affected"), list)]
    if not advisories:
        return [], "no OSV-style entries (objects with an 'affected' list) were found in the advisory file"
    return advisories, None


def _version_key(eco: str, version: str) -> Any:
    return pep440_key(version) if eco == "pypi" else _semver(version)


def _in_range(eco: str, version: str, events: list[dict[str, Any]]) -> bool | None:
    v = _version_key(eco, version)
    if v is None:
        return None
    state = False
    for event in events:
        if "introduced" in event:
            start = str(event["introduced"])
            sk = _version_key(eco, "0") if start in ("0", "") else _version_key(eco, start)
            if sk is not None and v >= sk:
                state = True
        elif "fixed" in event:
            fk = _version_key(eco, str(event["fixed"]))
            if fk is not None and v >= fk:
                state = False
        elif "last_affected" in event:
            lk = _version_key(eco, str(event["last_affected"]))
            if lk is not None and v > lk:
                state = False
    return state


def match_advisories(locks: list[LockInfo], advisories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    index: dict[tuple[str, str], list[Pkg]] = {}
    for lock in locks:
        for pkg in lock.packages:
            index.setdefault((pkg.ecosystem, norm_name(pkg.name)), []).append(pkg)
    hits: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for adv in advisories:
        adv_id = str(adv.get("id", "?"))
        for aff in adv.get("affected", []):
            if not isinstance(aff, dict):
                continue
            package = aff.get("package") or {}
            eco = {"pypi": "pypi", "npm": "npm"}.get(str(package.get("ecosystem", "")).lower())
            if eco is None or not package.get("name"):
                continue
            for pkg in index.get((eco, norm_name(package["name"])), []):
                matched = pkg.version in [str(v) for v in aff.get("versions") or []]
                fixed: list[str] = []
                for rng in aff.get("ranges") or []:
                    if not isinstance(rng, dict) or rng.get("type") not in ("ECOSYSTEM", "SEMVER"):
                        continue
                    events = [e for e in rng.get("events") or [] if isinstance(e, dict)]
                    fixed += [str(e["fixed"]) for e in events if "fixed" in e]
                    if _in_range(eco, pkg.version, events):
                        matched = True
                key = (adv_id, eco, pkg.name, pkg.version)
                if matched and key not in seen:
                    seen.add(key)
                    sev = (adv.get("database_specific") or {}).get("severity")
                    if not sev and isinstance(adv.get("severity"), list) and adv["severity"]:
                        sev = str(adv["severity"][0].get("score", ""))[:40]
                    hits.append({
                        "advisory": adv_id, "aliases": [str(a) for a in (adv.get("aliases") or [])][:5],
                        "package": pkg.name, "installed": pkg.version, "ecosystem": eco,
                        "fixed_in": sorted(set(fixed))[:5], "severity": sev,
                        "summary": str(adv.get("summary", ""))[:200], "locked_in": pkg.source,
                    })
    return hits
