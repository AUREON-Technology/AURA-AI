#!/usr/bin/env python3
"""Write ``desktop-update.json``, the manifest AURA Desktop checks for updates (M8.10).

The client side is ``aura_client/lib/desktop/update/desktop_update.dart``; the two share one
format, pinned by ``aura_client/test/fixtures/desktop-update.sample.json`` (the Python test checks
this script reproduces it, the Dart test parses it).

Release mode, run by ``desktop-package.yml`` for a ``desktop-v<version>`` tag::

    desktop_update_manifest.py release --version 3.0.2 --tag desktop-v3.0.2 \\
        --repo owner/name --asset windows-x64=dist/setup.exe --asset linux-x64=dist/app.tar.gz \\
        --output desktop-update.json

Rollback mode, run when the owner rolls the channel back to an earlier release: the same
manifest with ``"rollback": true``, which is what lets installed newer versions accept it::

    desktop_update_manifest.py rollback --from desktop-update.json --output desktop-update.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

SCHEMA = 1
_VERSION = re.compile(r"^\d+\.\d+\.\d+$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def release_manifest(version: str, tag: str, repo: str, assets: dict[str, Path]) -> dict:
    if not _VERSION.match(version):
        raise ValueError(f"version must be major.minor.patch, got {version!r}")
    if tag != f"desktop-v{version}":
        raise ValueError(f"tag {tag!r} does not match version {version!r} (expected desktop-v{version})")
    if not re.match(r"^[\w.-]+/[\w.-]+$", repo):
        raise ValueError(f"repo must be owner/name, got {repo!r}")
    if not assets:
        raise ValueError("at least one --asset is required")
    platforms = {}
    for key, path in sorted(assets.items()):
        if not path.is_file():
            raise ValueError(f"{key}: no such file {path}")
        platforms[key] = {
            "url": f"https://github.com/{repo}/releases/download/{tag}/{path.name}",
            "sha256": _sha256(path),
            "size": path.stat().st_size,
        }
    return {"schema": SCHEMA, "version": version, "tag": tag, "rollback": False, "platforms": platforms}


def rollback_manifest(existing: dict) -> dict:
    if existing.get("schema") != SCHEMA:
        raise ValueError(f"unsupported manifest schema {existing.get('schema')!r}")
    return {**existing, "rollback": True}


def _parse_assets(values: list[str]) -> dict[str, Path]:
    assets = {}
    for value in values:
        key, sep, path = value.partition("=")
        if not sep or not key or not path:
            raise ValueError(f"--asset must be platform=path, got {value!r}")
        assets[key] = Path(path)
    return assets


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="mode", required=True)
    rel = sub.add_parser("release")
    rel.add_argument("--version", required=True)
    rel.add_argument("--tag", required=True)
    rel.add_argument("--repo", required=True)
    rel.add_argument("--asset", action="append", default=[])
    rel.add_argument("--output", required=True)
    rb = sub.add_parser("rollback")
    rb.add_argument("--from", dest="source", required=True)
    rb.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    try:
        if args.mode == "release":
            manifest = release_manifest(args.version, args.tag, args.repo, _parse_assets(args.asset))
        else:
            manifest = rollback_manifest(json.loads(Path(args.source).read_text(encoding="utf-8")))
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"desktop_update_manifest: {exc}", file=sys.stderr)
        return 1
    Path(args.output).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
