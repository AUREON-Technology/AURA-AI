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

Signing (audit F18). Whatever wrote the manifest, the client trusts it only with a valid
signature. The format, which the client's verifier relies on byte for byte:

* **Algorithm:** Ed25519 (RFC 8032), over the manifest file's exact bytes as published -- no
  canonicalisation, no re-serialisation; whatever is uploaded is what is signed.
* **Encoding:** standard base64 (RFC 4648 section 4, with padding) of the 64-byte signature, with
  no trailing newline.
* **Location:** a sibling file named ``<manifest name>.sig`` -- ``desktop-update.json.sig`` --
  uploaded to the same release, so it lives at ``<manifest URL>.sig``.
* **Key:** env ``DESKTOP_UPDATE_SIGNING_KEY``, standard base64 of the raw 32-byte Ed25519 seed
  (private key). ``public-key`` prints the matching standard-base64 raw 32-byte public key, which
  is what the client pins. Generate a seed once, offline:
  ``python3 -c "import base64,os; print(base64.b64encode(os.urandom(32)).decode())"``.

::

    DESKTOP_UPDATE_SIGNING_KEY=... desktop_update_manifest.py sign --manifest desktop-update.json
    DESKTOP_UPDATE_SIGNING_KEY=... desktop_update_manifest.py public-key
    desktop_update_manifest.py verify --manifest desktop-update.json --public-key <base64>

``sign`` fails, and writes nothing, when the key is missing or malformed: a release must never go
out with no signature or a placeholder one. ``cryptography`` is imported only by these three
modes, so ``release`` and ``rollback`` still run without it.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import sys
from pathlib import Path

SCHEMA = 1
SIGNING_KEY_ENV = "DESKTOP_UPDATE_SIGNING_KEY"
SIGNATURE_SUFFIX = ".sig"
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


def _b64decode(value: str, length: int, what: str) -> bytes:
    try:
        raw = base64.b64decode(value.strip(), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"{what} is not standard base64") from exc
    if len(raw) != length:
        raise ValueError(f"{what} must decode to {length} bytes, got {len(raw)}")
    return raw


def _private_key(seed_b64: str):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    if not seed_b64 or not seed_b64.strip():
        raise ValueError(f"{SIGNING_KEY_ENV} is not set")
    return Ed25519PrivateKey.from_private_bytes(_b64decode(seed_b64, 32, SIGNING_KEY_ENV))


def sign_bytes(data: bytes, seed_b64: str) -> str:
    """Ed25519 signature over ``data`` exactly, as standard base64 with no newline."""
    return base64.b64encode(_private_key(seed_b64).sign(data)).decode("ascii")


def public_key_b64(seed_b64: str) -> str:
    """The raw 32-byte public key for ``seed_b64``, as standard base64 -- what the client pins."""
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    raw = _private_key(seed_b64).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.b64encode(raw).decode("ascii")


def verify_bytes(data: bytes, signature_b64: str, public_b64: str) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    key = Ed25519PublicKey.from_public_bytes(_b64decode(public_b64, 32, "public key"))
    try:
        key.verify(_b64decode(signature_b64, 64, "signature"), data)
    except InvalidSignature:
        return False
    return True


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
    sg = sub.add_parser("sign", help=f"sign a manifest with ${SIGNING_KEY_ENV}")
    sg.add_argument("--manifest", required=True)
    sg.add_argument("--output", help="default: <manifest>.sig")
    sub.add_parser("public-key", help=f"print the public key for ${SIGNING_KEY_ENV}")
    vf = sub.add_parser("verify", help="check a manifest against its .sig")
    vf.add_argument("--manifest", required=True)
    vf.add_argument("--signature", help="default: <manifest>.sig")
    vf.add_argument("--public-key", required=True)
    args = parser.parse_args(argv)

    if args.mode in ("sign", "public-key", "verify"):
        return _signing_main(args)

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


def _signing_main(args: argparse.Namespace) -> int:
    seed = os.environ.get(SIGNING_KEY_ENV, "")
    try:
        if args.mode == "public-key":
            print(public_key_b64(seed))
            return 0
        manifest = Path(args.manifest)
        if args.mode == "sign":
            signature = sign_bytes(manifest.read_bytes(), seed)
            output = Path(args.output or f"{manifest}{SIGNATURE_SUFFIX}")
            output.write_text(signature, encoding="ascii")
            print(f"signed {manifest} -> {output}")
            return 0
        sig_path = Path(args.signature or f"{manifest}{SIGNATURE_SUFFIX}")
        if not verify_bytes(manifest.read_bytes(), sig_path.read_text(encoding="ascii"), args.public_key):
            print(f"desktop_update_manifest: {sig_path} does not verify {manifest}", file=sys.stderr)
            return 1
        print(f"verified {manifest} against {sig_path}")
        return 0
    except (ValueError, OSError) as exc:
        print(f"desktop_update_manifest: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
