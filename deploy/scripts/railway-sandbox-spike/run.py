"""Phase 0 proof for Railway Sandboxes (docs/plans/railway-sandboxes-plan.md).

Drives throwaway sandboxes through the logged-in Railway CLI. Run it only against a project that
holds nothing but sandboxes. Every sandbox and checkpoint it creates is destroyed before it
exits, and a short idle timeout is the backstop if this process dies.

    python run.py --project <sandbox project id> --phase fence|isolation|checkpoint|io --out r.json
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

#: AURA's production project. A proof never runs next to the database it must not reach.
PRODUCTION_PROJECT = "a85f9ca9-b8c6-4ccd-a99c-90cb45fadef9"
HERE = Path(__file__).resolve().parent
UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
IDLE_MINUTES = "10"
CHECKPOINT = "aura-spike-checkpoint"
MIB = 1024 * 1024


def railway_executable() -> str:
    """The real binary, not the npm ``.cmd`` shim, whose quoting mangles shell commands."""
    appdata = os.environ.get("APPDATA")
    if appdata:
        exe = Path(appdata, "npm", "node_modules", "@railway", "cli", "bin", "railway.exe")
        if exe.is_file():
            return str(exe)
    found = shutil.which("railway")
    if not found:
        raise SystemExit("the Railway CLI is not installed")
    return found


class Spike:
    def __init__(self, project: str, environment: str) -> None:
        self.cli = railway_executable()
        self.scope = ["--project", project, "--environment", environment]
        self.sandboxes: list[str] = []
        self.checkpoints: list[str] = []
        self.results: list[dict] = []
        self.timings: dict[str, float] = {}

    # -- plumbing ----------------------------------------------------------------------------

    def record(self, check: str, verdict: str, detail: str) -> None:
        self.results.append({"check": check, "verdict": verdict, "detail": detail})
        print(f"{check:3} {verdict:4} {detail}", flush=True)

    def call(self, args: list[str], *, data: bytes | None = None, timeout: float = 600):
        started = time.monotonic()
        extra = {"input": data} if data is not None else {"stdin": subprocess.DEVNULL}
        done = subprocess.run([self.cli, "sandbox", *args], capture_output=True, timeout=timeout, **extra)
        return done, time.monotonic() - started

    def create(self, *extra: str) -> tuple[str, float]:
        done, seconds = self.call(
            ["create", *self.scope, "--idle-timeout-minutes", IDLE_MINUTES, "--json", *extra]
        )
        text = done.stdout.decode("utf-8", "replace")
        sandbox = None
        try:
            payload = json.loads(text)
            sandbox = payload.get("id") or payload.get("sandbox", {}).get("id")
        except (ValueError, AttributeError):
            match = UUID.search(text)
            sandbox = match.group(0) if match else None
        if sandbox:
            self.sandboxes.append(sandbox)
        if done.returncode != 0 or not sandbox:
            raise RuntimeError(f"create failed ({done.returncode}): {done.stderr.decode('utf-8', 'replace')[-800:]}")
        seconds += self.wait_until_running(sandbox)
        print(f"{sandbox} running after {seconds:.1f}s", flush=True)
        return sandbox, seconds

    def wait_until_running(self, sandbox: str, limit_s: float = 300) -> float:
        """Older CLIs (5.23) return from ``create`` while the sandbox is still CREATING."""
        started = time.monotonic()
        while time.monotonic() - started < limit_s:
            done, _ = self.call(["list", *self.scope, "--all", "--json"], timeout=60)
            try:
                rows = json.loads(done.stdout.decode("utf-8", "replace"))
            except ValueError:
                rows = []
            row = next((r for r in rows if r.get("id") == sandbox), None)
            if row and row.get("status") == "RUNNING":
                print(f"status RUNNING, idle timeout {row.get('idleTimeoutMinutes')}, region {row.get('region')}", flush=True)
                return time.monotonic() - started
            if row and row.get("status") in ("FAILED", "DESTROYED"):
                raise RuntimeError(f"sandbox {sandbox} became {row.get('status')} while starting")
            time.sleep(2)
        raise RuntimeError(f"sandbox {sandbox} was not running after {limit_s:.0f}s")

    def exec(self, sandbox: str, command: str, *, data: bytes | None = None,
             options: tuple[str, ...] = (), timeout: float = 600):
        return self.call(
            ["exec", "--id", sandbox, *self.scope, *options, "--", command], data=data, timeout=timeout
        )

    def text(self, sandbox: str, command: str, *, check: bool = False, timeout: float = 600) -> str:
        done, _ = self.exec(sandbox, command, timeout=timeout)
        # The CLI prints its own "experimental" warning on stderr; it is not command output.
        errors = "\n".join(
            line for line in done.stderr.decode("utf-8", "replace").splitlines()
            if "sandboxes are experimental" not in line
        )
        out = done.stdout.decode("utf-8", "replace").rstrip("\n") + ("\n" + errors if errors else "")
        if check and done.returncode != 0:
            raise RuntimeError(f"command failed ({done.returncode}): {command[:80]}\n{out[-1500:]}")
        return out.strip()

    def upload_guest(self, sandbox: str) -> None:
        script = (HERE / "guest.sh").read_bytes().replace(b"\r\n", b"\n")
        encoded = base64.b64encode(script).decode("ascii")
        self.text(sandbox, "rm -f /root/guest.b64", check=True)
        for start in range(0, len(encoded), 6000):
            self.text(sandbox, f"printf %s '{encoded[start:start + 6000]}' >> /root/guest.b64", check=True)
        self.text(sandbox, "base64 -d /root/guest.b64 > /root/guest.sh && chmod 700 /root/guest.sh", check=True)
        want = hashlib.sha256(script).hexdigest()
        got = self.text(sandbox, "sha256sum /root/guest.sh", check=True)
        if want not in got:
            raise RuntimeError("guest.sh arrived altered")

    def destroy(self, sandbox: str) -> None:
        done, _ = self.call(["destroy", sandbox, *self.scope])
        if done.returncode == 0:
            self.sandboxes.remove(sandbox)
        else:
            print(f"destroy {sandbox} failed: {done.stderr.decode('utf-8', 'replace')[-300:]}", flush=True)

    def cleanup(self) -> None:
        for sandbox in list(self.sandboxes):
            self.destroy(sandbox)
        for name in list(self.checkpoints):
            done, _ = self.call(["checkpoint", *self.scope, "delete", name])
            if done.returncode == 0:
                self.checkpoints.remove(name)
            else:
                print(f"checkpoint delete failed: {done.stderr.decode('utf-8', 'replace')[-300:]}", flush=True)
        if self.sandboxes or self.checkpoints:
            self.record("--", "FAIL", f"left behind: {self.sandboxes} {self.checkpoints}")

    # -- phases ------------------------------------------------------------------------------

    def phase_fence(self) -> None:
        """S1, S2 and the single-sandbox half of S3."""
        sandbox, seconds = self.create()
        self.timings["create_s"] = round(seconds, 1)
        self.upload_guest(sandbox)
        print(self.text(sandbox, "/root/guest.sh inspect"), flush=True)
        # Control: the refusals proved later must be caused by the fence, not by a dead network.
        open_before = self.text(sandbox, "curl -s -o /dev/null -w '%{http_code}' --max-time 15 https://example.com || true")
        self.record("S2", "INFO", f"before the fence, example.com answers HTTP {open_before}")
        _, seconds = self.exec(sandbox, "true")
        self.timings["exec_roundtrip_s"] = round(seconds, 2)
        started = time.monotonic()
        print(self.text(sandbox, "/root/guest.sh setup", check=True), flush=True)
        self.timings["fence_setup_s"] = round(time.monotonic() - started, 1)
        output = self.text(sandbox, "/root/guest.sh probe")
        for line in output.splitlines():
            parts = line.split("|", 3)
            if len(parts) == 4 and parts[0] == "RESULT":
                self.record(parts[1], parts[2], parts[3])
        if not any(r["check"] == "S3" for r in self.results):
            self.record("S1", "FAIL", f"probe did not finish: {output[-600:]}")
        # The control channel must survive the fence: this is a fresh exec after setup.
        alive = self.text(sandbox, "echo control-channel-ok")
        self.record("S1", "PASS" if "control-channel-ok" in alive else "FAIL", "platform exec works after the fence")

    @staticmethod
    def global_addresses(output: str) -> list[str]:
        """Only the lines that are real, non-loopback addresses."""
        found = []
        for word in output.split():
            try:
                address = ipaddress.ip_address(word)
            except ValueError:
                continue
            if not address.is_loopback and not address.is_link_local:
                found.append(word)
        return found

    def phase_isolation(self) -> None:
        """S3: one default (isolated) sandbox cannot reach another. Needs two at once."""
        first, _ = self.create()
        second, _ = self.create()
        query = "ip -o addr show scope global | awk '$2 != \"lo\" {print $4}' | cut -d/ -f1"
        addresses = self.global_addresses(self.text(first, query, check=True))
        mine = self.global_addresses(self.text(second, query, check=True))
        self.record("S3", "INFO", f"first has {addresses}; second has {mine}")
        self.text(first, "mkdir -p /tmp/spike && echo reachable-first > /tmp/spike/marker && "
                         "cd /tmp/spike && (setsid python3 -m http.server 8099 --bind :: >/tmp/spike.log 2>&1 &) ; sleep 2", check=True)
        local = self.text(first, "curl -s --noproxy '*' --max-time 5 http://127.0.0.1:8099/marker || true")
        self.record("S3", "PASS" if "reachable-first" in local else "FAIL", "control: the test server answers locally")
        for address in addresses:
            host = f"[{address}]" if ":" in address else address
            # Control: the server answers on this very address from inside, so a refusal from
            # the other sandbox is the network's doing and not a server that never listened.
            own = self.text(first, f"curl -s --noproxy '*' --max-time 5 'http://{host}:8099/marker' || true")
            self.record("S3", "PASS" if "reachable-first" in own else "FAIL", f"control: first answers itself at {address}")
            answer = self.text(second, f"curl -s --noproxy '*' --max-time 8 'http://{host}:8099/marker' || true")
            verdict = "FAIL" if "reachable-first" in answer else "PASS"
            self.record("S3", verdict, f"second sandbox reaching first at {address}")
        if not addresses:
            self.record("S3", "FAIL", "no global address found; isolation between sandboxes not tested")

    def phase_checkpoint(self) -> None:
        """S4: checkpoint and boot a workspace holding 1 GiB."""
        sandbox, _ = self.create()
        self.record("S4", "INFO", "disk before: " + self.text(sandbox, "df -h / | tail -1"))
        self.upload_guest(sandbox)
        self.text(sandbox, "/root/guest.sh setup", check=True)
        self.text(sandbox, "mkdir -p /home/aura/ws && dd if=/dev/urandom of=/home/aura/ws/blob bs=1M count=1024 status=none "
                           "&& echo kept > /home/aura/ws/marker && sync", check=True)
        before = self.text(sandbox, "sha256sum /home/aura/ws/blob | cut -d' ' -f1", check=True)
        self.checkpoints.append(CHECKPOINT)
        done, seconds = self.call(["checkpoint", *self.scope, "create", CHECKPOINT, "--id", sandbox])
        if done.returncode != 0:
            raise RuntimeError("checkpoint failed: " + done.stderr.decode("utf-8", "replace")[-600:])
        self.timings["checkpoint_1gib_s"] = round(seconds, 1)
        self.record("S4", "PASS" if seconds <= 60 else "FAIL", f"checkpoint of 1 GiB took {seconds:.1f}s (target 60)")
        self.destroy(sandbox)
        started = time.monotonic()
        restored, _ = self.create("--checkpoint", CHECKPOINT)
        self.text(restored, "true", check=True)
        seconds = time.monotonic() - started
        self.timings["boot_from_checkpoint_s"] = round(seconds, 1)
        self.record("S4", "PASS" if seconds <= 30 else "FAIL", f"boot from checkpoint to first command {seconds:.1f}s (target 30)")
        after = self.text(restored, "sha256sum /home/aura/ws/blob | cut -d' ' -f1; cat /home/aura/ws/marker", check=True)
        self.record("S4", "PASS" if before in after and "kept" in after else "FAIL", "1 GiB file and marker identical after restore")
        rules = self.text(restored, "nft list table inet aura >/dev/null 2>&1 && echo fence-present || echo fence-gone; "
                                    "pgrep -x tinyproxy >/dev/null && echo proxy-running || echo proxy-stopped")
        self.record("S4", "INFO", "firewall and proxy after restore: " + " ".join(rules.split()))

    def phase_io(self) -> None:
        """S5: streaming, timeout, cancel and a 100 MiB transfer through the CLI."""
        sandbox, _ = self.create()
        process = subprocess.Popen(
            [self.cli, "sandbox", "exec", "--id", sandbox, *self.scope, "--",
             "for i in 1 2 3 4; do echo tick$i; sleep 3; done"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
        )
        arrivals = []
        for line in iter(process.stdout.readline, b""):
            if b"tick" in line:
                arrivals.append(time.monotonic())
        process.wait(timeout=60)
        spread = arrivals[-1] - arrivals[0] if len(arrivals) == 4 else 0
        self.record("S5", "PASS" if spread > 6 else "FAIL", f"output streams while running ({len(arrivals)} lines over {spread:.1f}s)")

        done, seconds = self.exec(sandbox, "sleep 61", options=("--timeout", "5"), timeout=90)
        still = self.text(sandbox, "pgrep -f '^sleep 61' >/dev/null && echo alive || echo gone")
        ok = done.returncode == 124 and "gone" in still
        self.record("S5", "PASS" if ok else "FAIL", f"timeout: exit {done.returncode} after {seconds:.1f}s, process {still}")

        process = subprocess.Popen(
            [self.cli, "sandbox", "exec", "--id", sandbox, *self.scope, "--", "sleep 77"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
        )
        time.sleep(8)
        process.kill()
        process.wait(timeout=30)
        time.sleep(12)
        still = self.text(sandbox, "pgrep -f '^sleep 77' >/dev/null && echo alive || echo gone")
        self.record("S5", "INFO", f"client killed without a cancel message: process {still}")

        payload = os.urandom(100 * MIB)
        want = hashlib.sha256(payload).hexdigest()
        done, seconds = self.exec(sandbox, "cat > /root/up.bin; sha256sum /root/up.bin", data=payload)
        ok = want in done.stdout.decode("utf-8", "replace")
        self.record("S5", "PASS" if ok else "FAIL", f"100 MiB upload over exec stdin in {seconds:.1f}s")
        self.text(sandbox, "dd if=/dev/urandom of=/root/down.bin bs=1M count=100 status=none", check=True)
        want = self.text(sandbox, "sha256sum /root/down.bin | cut -d' ' -f1", check=True)
        done, seconds = self.exec(sandbox, "cat /root/down.bin")
        ok = hashlib.sha256(done.stdout).hexdigest() == want.split()[0]
        self.record("S5", "PASS" if ok else "FAIL",
                    f"100 MiB download over one exec's stdout in {seconds:.1f}s ({len(done.stdout)} bytes)")
        self.workarounds(sandbox, want.split()[0])

    def workarounds(self, sandbox: str, want: str) -> None:
        """What the broker has to do where the raw transport is not enough."""
        # A deadline enforced inside the VM does not depend on the client's connection.
        done, seconds = self.exec(sandbox, "timeout --kill-after=5 5 sleep 62", timeout=90)
        still = self.text(sandbox, "pgrep -f '^sleep 62' >/dev/null && echo alive || echo gone")
        ok = done.returncode == 124 and still.startswith("gone")
        self.record("S5", "PASS" if ok else "FAIL",
                    f"workaround, timeout inside the VM: exit {done.returncode} after {seconds:.1f}s, process {still}")
        # Cancel: end every process of the agent user, whatever the client's connection did.
        self.text(sandbox, "id aura >/dev/null 2>&1 || useradd -m -u 10001 aura", check=True)
        process = subprocess.Popen(
            [self.cli, "sandbox", "exec", "--id", sandbox, *self.scope, "--",
             "setpriv --reuid=10001 --regid=10001 --init-groups --no-new-privs sh -c 'setsid sleep 78 & sleep 79'"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
        )
        time.sleep(8)
        before = self.text(sandbox, "pgrep -u 10001 | wc -l")
        self.text(sandbox, "pkill -KILL -u 10001; sleep 1; true")
        after = self.text(sandbox, "pgrep -u 10001 | wc -l")
        process.kill()
        process.wait(timeout=30)
        ok = before.split()[0] != "0" and after.split()[0] == "0"
        self.record("S5", "PASS" if ok else "FAIL",
                    f"workaround, cancel by user: {before} agent processes before, {after} after")
        # Large reads in pieces that stay under the retained-output limit.
        digest, received, started = hashlib.sha256(), 0, time.monotonic()
        for index in range(25):
            done, _ = self.exec(sandbox, f"dd if=/root/down.bin bs=4M skip={index} count=1 status=none")
            digest.update(done.stdout)
            received += len(done.stdout)
        seconds = time.monotonic() - started
        self.record("S5", "PASS" if digest.hexdigest() == want else "FAIL",
                    f"workaround, 100 MiB download in 25 pieces of 4 MiB: {received} bytes in {seconds:.1f}s")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--project", required=True)
    parser.add_argument("--environment", default="production")
    parser.add_argument("--phase", required=True, choices=["fence", "isolation", "checkpoint", "io"])
    parser.add_argument("--out", required=True)
    options = parser.parse_args()
    if options.project == PRODUCTION_PROJECT:
        raise SystemExit("refusing to run the proof in AURA's production project")

    spike = Spike(options.project, options.environment)
    try:
        getattr(spike, f"phase_{options.phase}")()
    except Exception as exc:  # the report must say what broke, and cleanup must still run
        spike.record("--", "FAIL", f"{type(exc).__name__}: {exc}")
    finally:
        spike.cleanup()
        Path(options.out).write_text(
            json.dumps({"phase": options.phase, "timings": spike.timings, "results": spike.results}, indent=2),
            encoding="utf-8",
        )
    return 1 if any(r["verdict"] == "FAIL" for r in spike.results) else 0


if __name__ == "__main__":
    sys.exit(main())
