import base64
import json
import subprocess
import urllib.request

REPO = "anasibrahimgnedy-collab/AURA-AI"
token = subprocess.check_output(["gh", "auth", "token"], text=True).strip()

def put_file(path, message):
    full = f"E:/AURA AI/{path}"
    with open(full, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    url = f"https://api.github.com/repos/{REPO}/contents/{path}"
    req_get = urllib.request.Request(url)
    req_get.add_header("Authorization", f"Bearer {token}")
    req_get.add_header("Accept", "application/vnd.github+json")
    sha = None
    try:
        with urllib.request.urlopen(req_get) as resp:
            sha = json.load(resp).get("sha")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    payload = {"message": message, "content": b64}
    if sha:
        payload["sha"] = sha
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="PUT")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req) as resp:
        out = json.load(resp)
    print(path, out["commit"]["sha"])

put_file(".github/workflows/deploy-backend.yml", "deploy: add dev_ops input to Deploy VPS")
put_file(".github/workflows/vps-dev-ops.yml", "deploy: add VPS Dev Ops workflow")
