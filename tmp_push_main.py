import base64
import json
import subprocess
from pathlib import Path
import urllib.error

REPO = "anasibrahimgnedy-collab/AURA-AI"
ROOT = Path("E:/AURA AI")
FILES = [
    "deploy/scripts/dev-console-up.sh",
    "deploy/.env.development.example",
    ".github/workflows/deploy-backend.yml",
    ".github/workflows/vps-dev-ops.yml",
]

token = subprocess.check_output(["gh", "auth", "token"], text=True).strip()

def gh_api(path, method="GET", data=None):
    import urllib.request
    url = f"https://api.github.com/repos/{REPO}/git/{path}"
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)

def gh_repo_api(path, method="GET", data=None):
    import urllib.request
    url = f"https://api.github.com/repos/{REPO}/{path}"
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)

ref = gh_repo_api("git/ref/heads/main")
base_sha = ref["object"]["sha"]
base_commit = gh_api(f"commits/{base_sha}")
base_tree = base_commit["tree"]["sha"]

tree_items = []
for rel in FILES:
    path = ROOT / rel
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    blob = gh_api("blobs", "POST", {"content": b64, "encoding": "base64"})
    tree_items.append({"path": rel.replace("\\", "/"), "mode": "100644", "type": "blob", "sha": blob["sha"]})

new_tree = gh_api("trees", "POST", {"base_tree": base_tree, "tree": tree_items})
message = (
    "deploy: VPS Dev Ops workflow and dev console recovery\n\n"
    "Add workflow_dispatch VPS Dev Ops (console-only bring-up, vps-dev-bring-up), "
    "dev_ops flag on Deploy VPS, and Google OAuth origin instructions for dev nip.io."
)
commit = gh_api(
    "commits",
    "POST",
    {"message": message, "tree": new_tree["sha"], "parents": [base_sha]},
)
gh_repo_api("git/refs/heads/main", "PATCH", {"sha": commit["sha"], "force": False})
print(commit["sha"])
print(f"https://github.com/{REPO}/commit/{commit['sha']}")
