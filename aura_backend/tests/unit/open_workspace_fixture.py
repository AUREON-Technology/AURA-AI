"""A realistic workspace for the ADR-043 tool tests: real-format lockfiles, a venv, a notebook, an ELF, a tarball."""
from __future__ import annotations

import base64
import io
import json
import os
import shutil
import sqlite3
import struct
import tarfile
import zlib
from pathlib import Path


def _png(width: int, height: int) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + b"\x10\x80\xf0" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def build(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)

    def w(rel: str, text: str) -> Path:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    # -- project instructions and style --------------------------------------------------------
    w("AGENTS.md", "# Agent rules\n\n- Always run pytest before you finish.\n- Never commit secrets or .env files.\n"
      "- You must use type hints in new code.\n\n## Commands\n\n```bash\npytest -q\nruff check .\n```\n")
    w("CONTRIBUTING.md", "# Contributing\n\nOpen a PR against main.\n")
    w("services/billing/AGENTS.md", "# Billing rules\n\n- Never change invoice numbering.\n- Always add a test.\n")
    w(".editorconfig", "root = true\n\n[*]\nindent_style = space\nindent_size = 4\ninsert_final_newline = true\n"
      "trim_trailing_whitespace = true\nmax_line_length = 100\n\n[*.{js,ts}]\nindent_size = 2\n")
    w("services/billing/styled.py", "def total(items):\n\treturn sum(items)   \n")

    # -- skills / rules / workflows ------------------------------------------------------------
    w(".aura/skills/deploy/SKILL.md", "---\nname: deploy-railway\ndescription: Deploy the backend to Railway and "
      "verify health\ntags: [deploy, railway]\n---\n\n# Deploy\n\nRun the predeploy script, push, then check "
      "/health/ready on the live host before telling the user it is done.\n")
    w(".claude/skills/review/SKILL.md", "---\nname: pr-review\ndescription: Review a pull request for correctness\n"
      "---\n\nRead the diff first, then the tests, then comment on risks only a human would catch.\n")
    w(".aura/workflows/release.md", "# Release\n\n## Step 1: Test\n\n```bash\npytest\n```\n\n## Step 2: Tag\n\n"
      "```bash\ngit tag v1\n```\n")
    w(".claude/rules/style.md", "# Style\n\nAlways keep functions short.\n")

    # -- a small python repo: import graph, bad imports, arity ---------------------------------
    w("app/__init__.py", "")
    w("app/users.py", "from dataclasses import dataclass\n\n\n@dataclass\nclass UserStore:\n    items: dict\n\n"
      "    def get(self, user_id):\n        return self.items.get(user_id)\n\n\n"
      "def load_user(user_id, store, *, strict=False):\n    user = store.get(user_id)\n"
      "    if strict and user is None:\n        raise KeyError(user_id)\n    return user\n")
    w("app/store.py", "import json\nfrom app.users import UserStore\n\n\ndef open_store(path):\n"
      "    with open(path) as handle:\n        return UserStore(json.load(handle))\n")
    w("app/api.py", "import os\nimport requests\nimport yaml\nimport nonexistent_pkg_xyz\n"
      "from app.users import load_user, missing_fn\nfrom app.store import open_store\nfrom os import nope\n\n\n"
      "def handler(user_id):\n    store = open_store(os.environ['STORE'])\n"
      "    user = load_user(user_id)\n    other = load_user(user_id, store, True, 3)\n"
      "    return undefined_helper(user, other)\n")
    w("app/cli.py", "from app import users\nfrom app.users import UserStore\n\n\ndef main():\n    return UserStore({})\n")
    w("tests/test_users.py", "from app.users import load_user\n\n\ndef test_load():\n    assert load_user(1, {}) is None\n")
    w("tests/test_cli.py", "from app.cli import main\n\n\ndef test_main():\n    assert main()\n")
    w("tests/test_other_billing.py", "def test_x():\n    pass\n")

    # a js file importing another
    w("web/src/util.ts", "export function slug(x: string) { return x.toLowerCase(); }\n")
    w("web/src/page.tsx", "import { slug } from './util';\nimport React from 'react';\n"
      "export default function Page() { return slug('A'); }\n")

    # -- dependencies --------------------------------------------------------------------------
    w("requirements.txt", "requests==2.31.0\nPyYAML>=6.0\nflask==2.0.1\nflask==2.1.0\nnumpy\n"
      "unusedlib==1.0\n-e git+https://example.invalid/x.git#egg=vcsdep\n")
    w("requirements.lock", "requests==2.31.0 \\\n    --hash=sha256:" + "a" * 64 + "\n"
      "pyyaml==5.4.1 \\\n    --hash=sha256:" + "b" * 64 + "\nflask==2.1.0\nlicensed-gpl==1.0.0\n")
    w("pyproject.toml", '[project]\nname = "demo"\nversion = "0.1.0"\nrequires-python = ">=3.11"\n'
      'dependencies = ["requests>=2.31", "pyyaml>=6.0", "missinglib>=1.0"]\n')
    w("poetry.lock", '[[package]]\nname = "requests"\nversion = "2.31.0"\nfiles = [{file = "r.whl", hash = "sha256:' + "c" * 64 + '"}]\n\n'
      '[[package]]\nname = "pyyaml"\nversion = "5.4.1"\nfiles = []\n')
    w("package.json", json.dumps({"name": "web", "version": "1.0.0", "dependencies": {
        "react": "^18.2.0", "left-pad": "*", "lodash": "^4.17.0"}, "devDependencies": {"eslint": "^8.0.0"}}, indent=2))
    w("package-lock.json", json.dumps({"name": "web", "lockfileVersion": 3, "packages": {
        "": {"name": "web"},
        "node_modules/react": {"version": "18.2.0", "integrity": "sha512-x", "license": "MIT"},
        "node_modules/lodash": {"version": "3.10.1", "integrity": "sha512-y", "license": "MIT"},
        "node_modules/left-pad": {"version": "1.3.0", "integrity": "sha512-z", "license": "WTFPL"},
        "node_modules/gplpkg": {"version": "2.0.0", "license": "GPL-3.0"},
    }}, indent=2))
    # a real-looking virtualenv inside the workspace
    sp = root / ".venv/lib/python3.11/site-packages"
    (root / ".venv/pyvenv.cfg").parent.mkdir(parents=True, exist_ok=True)
    (root / ".venv/pyvenv.cfg").write_text("home = /usr/bin\nversion = 3.11.4\n")

    def dist(name, version, license_line, requires=()):
        d = sp / f"{name}-{version}.dist-info"
        d.mkdir(parents=True, exist_ok=True)
        lines = ["Metadata-Version: 2.1", f"Name: {name}", f"Version: {version}"] + license_line \
            + [f"Requires-Dist: {r}" for r in requires]
        (d / "METADATA").write_text("\n".join(lines) + "\n\nLong description here.\n")
        (d / "top_level.txt").write_text(name.replace("-", "_") + "\n")

    dist("requests", "2.31.0", ["License: Apache-2.0"], ["urllib3>=1.21.1,<3", "charset-normalizer>=2"])
    dist("PyYAML", "5.4.1", ["License: MIT"])
    dist("licensed-gpl", "1.0.0", ["License-Expression: GPL-3.0-only"])
    dist("urllib3", "0.9", ["Classifier: License :: OSI Approved :: MIT License"])
    nm = root / "node_modules"
    for pkg, ver, lic in (("react", "18.2.0", "MIT"), ("left-pad", "1.3.0", "WTFPL")):
        (nm / pkg).mkdir(parents=True, exist_ok=True)
        (nm / pkg / "package.json").write_text(json.dumps({"name": pkg, "version": ver, "license": lic}))
    w(".venv/lib/python3.11/site-packages/yaml/__init__.py", "def safe_load(x):\n    return x\n")
    w(".venv/lib/python3.11/site-packages/requests/__init__.py", "def get(url):\n    return url\n")

    # -- advisories (OSV) ----------------------------------------------------------------------
    w("advisories.json", json.dumps([{
        "id": "GHSA-demo-0001", "aliases": ["CVE-2099-0001"], "summary": "PyYAML arbitrary code execution (demo entry)",
        "database_specific": {"severity": "CRITICAL"},
        "affected": [{"package": {"name": "PyYAML", "ecosystem": "PyPI"},
                      "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "5.4"}]}]}],
    }, {
        "id": "GHSA-demo-0002", "summary": "lodash prototype pollution (demo entry)",
        "affected": [{"package": {"name": "lodash", "ecosystem": "npm"},
                      "ranges": [{"type": "SEMVER", "events": [{"introduced": "0.0.0"}, {"fixed": "4.17.21"}]}]}],
    }]))

    # -- secrets (built at runtime; never real) ------------------------------------------------
    fake_key = "sk-" + "".join(chr(97 + (i * 7) % 26) for i in range(40))
    w("config/settings.env", f"OPENAI={fake_key}\nDEBUG=1\n")
    w("scripts/run.py", "import os\nimport subprocess\n\nsubprocess.run('ls ' + os.environ['X'], shell=True)\n"
      "password = 'hunter2hunter2'\nresult = eval(input())\n")

    # -- notebook ------------------------------------------------------------------------------
    img = base64.b64encode(_png(4, 4)).decode()
    nb = {"cells": [
        {"cell_type": "markdown", "metadata": {}, "source": ["# Churn analysis\n", "Quarterly numbers."]},
        {"cell_type": "code", "metadata": {}, "execution_count": 1, "source": ["import pandas as pd\n", "%matplotlib inline\n",
         "df = pd.DataFrame({'a': [1, 2]})\n", "print(len(df))"], "outputs": [
            {"output_type": "stream", "name": "stdout", "text": ["2\n"]}]},
        {"cell_type": "code", "metadata": {}, "execution_count": 2, "source": ["df.plot()"], "outputs": [
            {"output_type": "display_data", "data": {"image/png": img, "text/plain": ["<Figure size 640x480>"]}, "metadata": {}}]},
        {"cell_type": "code", "metadata": {}, "execution_count": 3, "source": ["1/0"], "outputs": [
            {"output_type": "error", "ename": "ZeroDivisionError", "evalue": "division by zero",
             "traceback": ["\u001b[0;31mZeroDivisionError\u001b[0m: division by zero"]}]},
        {"cell_type": "raw", "metadata": {}, "source": ["raw note"]},
    ], "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3"}, "language_info": {"name": "python"}},
        "nbformat": 4, "nbformat_minor": 5}
    w("notebooks/analysis.ipynb", json.dumps(nb, indent=1))

    # -- binaries ------------------------------------------------------------------------------
    (root / "bin").mkdir(exist_ok=True)
    src = next(Path(p) for p in ("/bin/true", "/usr/bin/true", "/bin/ls") if Path(p).exists())
    shutil.copy(src, root / "bin/tool.bin")
    (root / "assets").mkdir(exist_ok=True)
    (root / "assets/logo.png").write_bytes(_png(32, 16))
    con = sqlite3.connect(root / "assets/app.db")
    con.execute("create table t(x)")
    con.execute("insert into t values (1)")
    con.commit()
    con.close()
    (root / "assets/random.dat").write_bytes(os.urandom(4096))

    # -- backups: a real tarball of a project dir + checksum file + sql dump ---------------------
    proj = root / "billing-proj"
    (proj / "src").mkdir(parents=True, exist_ok=True)
    (proj / "src/main.py").write_text("print('billing')\n")
    (proj / "README.md").write_text("billing project\n")
    (proj / "data.csv").write_text("a,b\n1,2\n")
    (root / "backups").mkdir(exist_ok=True)
    tar_path = root / "backups/billing.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        tf.add(proj, arcname="billing-proj")
    import hashlib

    digest = hashlib.sha256(tar_path.read_bytes()).hexdigest()
    (root / "backups/billing.tar.gz.sha256").write_text(f"{digest}  billing.tar.gz\n")
    # the live folder has drifted from the backup: one file edited, one deleted, one added
    (proj / "src/main.py").write_text("print('billing v2')\n")
    (proj / "data.csv").unlink()
    (proj / "NEW.txt").write_text("added after backup\n")
    (root / "backups/dump.sql").write_text(
        "CREATE TABLE users (id INT PRIMARY KEY, name TEXT);\nINSERT INTO users VALUES (1, 'a');\n"
        "INSERT INTO users VALUES (2, 'b');\n")
    (root / "backups/cut.sql").write_text("CREATE TABLE users (id INT);\nINSERT INTO users VALUES (1, 'a'")
    bad = bytearray(tar_path.read_bytes())
    (root / "backups/truncated.tar.gz").write_bytes(bytes(bad[: len(bad) // 2]))
    return {"tar_sha256": digest, "fake_key": fake_key}
