"""Prove the broker's guest scripts (aura_sandbox_broker/guest) on a real Railway sandbox.

The broker's tests use an in-memory vendor, so fence.sh, run.sh and file.sh are proven only
here. The Railway CLI stands in for the SDK: both run a command inside the sandbox as root.
Same rules as run.py: a project holding nothing but sandboxes, everything destroyed afterwards.

    python broker_guest.py --project <sandbox project id> --out results.json
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import shlex
import subprocess
import sys
import time
import uuid
from pathlib import Path

from run import PRODUCTION_PROJECT, Spike

ROOT = Path(__file__).resolve().parents[3]
GUEST = ROOT / "aura_sandbox_broker" / "guest"
LIB = "/usr/local/lib/aura"
STAGING = "/var/lib/aura/staging"
WS = "/home/aura/workspace"
CHECKPOINT = "aura-broker-proof"
# The parts of the broker's template the scripts need.
PACKAGES = "curl ca-certificates iproute2 procps util-linux python3 python3-pip git"
HOSTS = [
    line.strip("^$").replace("\\.", ".")
    for line in (ROOT / "deploy" / "sandbox" / "filter").read_text(encoding="utf-8").splitlines()
    if line.strip() and not line.startswith("#")
]


class Proof(Spike):
    def check(self, group: str, ok: bool, detail: str) -> None:
        self.record(group, "PASS" if ok else "FAIL", detail)

    def sh(self, sandbox: str, command: str, timeout: float = 200) -> tuple[int, str, str]:
        done, _ = self.exec(sandbox, command, timeout=timeout)
        errors = "\n".join(
            line for line in done.stderr.decode("utf-8", "replace").splitlines()
            if "sandboxes are experimental" not in line
        )
        return done.returncode, done.stdout.decode("utf-8", "replace").strip(), errors.strip()

    def push(self, sandbox: str, path: str, data: bytes) -> None:
        encoded = base64.b64encode(data).decode("ascii")
        self.text(sandbox, f"mkdir -p {LIB} && rm -f /root/up.b64", check=True)
        for start in range(0, len(encoded), 6000):
            self.text(sandbox, f"printf %s '{encoded[start:start + 6000]}' >> /root/up.b64", check=True)
        self.text(sandbox, f"base64 -d /root/up.b64 > {path} && chmod 755 {path}", check=True)

    def install(self, sandbox: str, packages: bool) -> None:
        if packages:
            self.text(sandbox, "export DEBIAN_FRONTEND=noninteractive; apt-get update -qq && "
                               f"apt-get install -y -qq {PACKAGES} >/dev/null", check=True)
        for name in ("fence.sh", "run.sh", "file.sh", "confine.py", "proxy.py"):
            self.push(sandbox, f"{LIB}/{name}", (GUEST / name).read_bytes().replace(b"\r\n", b"\n"))

    def fence(self, sandbox: str) -> tuple[int, str, str]:
        started = time.monotonic()
        result = self.sh(sandbox, f"bash {LIB}/fence.sh " + " ".join(HOSTS))
        self.timings.setdefault("fence_s", round(time.monotonic() - started, 1))
        return result

    @staticmethod
    def command_line(command_id: str, limit: int, argv: list[str]) -> str:
        return f"bash {LIB}/run.sh {command_id} {limit} -- " + " ".join(shlex.quote(a) for a in argv)

    def agent(self, sandbox: str, script: str, limit: int = 60) -> tuple[int, str, str, str]:
        """Run as the broker would. Returns the secret that prefixes run.sh's own reports."""
        self.last_id, mark = uuid.uuid4().hex, uuid.uuid4().hex
        line = f"AURA_MARK={mark} " + self.command_line(self.last_id, limit, ["sh", "-c", script])
        code, out, err = self.sh(sandbox, line)
        return code, out, err, mark

    def refused(self, sandbox: str) -> bool:
        code, out, _, _ = self.agent(sandbox, "echo ran")
        return code == 97 and "ran" not in out

    def agent_processes(self, sandbox: str) -> str:
        # An empty answer is neither pids nor "none": the call itself failed, so ask again.
        for _ in range(3):
            out = self.sh(sandbox, "sleep 1; pgrep -u 10001 -x -d, sleep || echo none")[1]
            if out:
                return out
        return "no answer"

    def file(self, sandbox: str, *args: str) -> tuple[int, str, str]:
        return self.sh(sandbox, f"bash {LIB}/file.sh " + " ".join(shlex.quote(a) for a in args))

    @staticmethod
    def stage() -> str:
        return f"{STAGING}/{uuid.uuid4().hex}"

    # -- the proof ---------------------------------------------------------------------------

    def prove(self) -> None:
        first, _ = self.create()
        self.install(first, packages=True)
        code, out, err = self.fence(first)
        self.check("G1", code == 0 and "fence ready" in out, f"fence on a fresh sandbox: exit {code} {err[-300:]}")
        if code != 0:
            return
        code, out, err = self.fence(first)
        self.check("G1", code == 0, f"fence again (idempotent): exit {code} {err[-200:]}")

        self.commands(first)
        self.real_clients(first)
        self.fence_loss(first)
        self.files(first)
        self.review_findings(first)
        self.fronting(first)
        self.hostile_restore(first)

    def commands(self, box: str) -> None:
        code, out, err, _ = self.agent(box, "id -u; pwd; echo oops >&2; exit 3")
        self.check("G2", code == 3 and out.split() == ["10001", WS], f"agent uid, workspace, exit code: {code} {out!r} {err[-120:]}")
        self.check("G2", "oops" in err, "stderr arrives separately")
        command_id = uuid.uuid4().hex
        values = ["a b", "c'd", "$(id)", "line\nbreak", "--", "*"]
        code, out, _ = self.sh(box, self.command_line(command_id, 30, ["printf", "%s|", *values]))
        self.check("G2", out == "|".join(values) + "|", f"arguments arrive unchanged: {out!r}")
        code, out, _, _ = self.agent(box, "env | cut -d= -f1 | sort | tr '\\n' ' '; cat /proc/self/cgroup")
        clean = "RAILWAY_" not in out and "AURA_MARK" not in out and "HTTPS_PROXY" in out
        self.check("G2", clean, f"clean environment: {out[:200]!r}")
        self.check("G2", f"aura-{self.last_id}" in out, "the command runs in its own cgroup")
        # Read the kernel's own view: `sh` here is dash, whose ulimit has no -u.
        code, out, _, _ = self.agent(
            box, "awk '/^Max processes|^Max open files/ {print $(NF-1)}' /proc/self/limits; "
                 "grep NoNewPrivs /proc/self/status")
        self.check("G2", out.split()[:2] == ["512", "4096"] and out.endswith("1"), f"limits: {out!r}")

        code, _, err, _ = self.agent(box, "curl -sSf --max-time 20 -o /dev/null https://pypi.org/simple/pip/")
        self.check("G3", code == 0, f"allowed host: exit {code} {err[-150:]}")
        for label, script in (
            ("refused host", "curl -sSf --max-time 15 -o /dev/null https://example.com"),
            ("direct connection", "curl -sS --noproxy '*' --max-time 8 -o /dev/null https://1.1.1.1"),
            ("DNS", "getent ahosts example.com"),
            ("root via sudo or su", "sudo -n true || echo | su -c true root"),
            ("rewrite the runner", f"echo x >> {LIB}/run.sh"),
            ("read staging", f"ls {STAGING}"),
            ("flush the firewall", "/usr/local/bin/nft flush ruleset"),
            ("write a cgroup", "mkdir /sys/fs/cgroup/escape || echo 1 > /sys/fs/cgroup/cgroup.procs"),
        ):
            code, out, _, _ = self.agent(box, script)
            self.check("G3", code != 0, f"agent cannot: {label} (exit {code})")
        code, out, _, _ = self.agent(
            box, "find / -xdev \\( -type f -o -type d \\) -writable -user root -not -path '/proc/*' "
                 "-not -path '/tmp*' -not -path '/var/tmp*' -not -path '/dev/*' -not -path '/run/lock*' "
                 "-not -path '/sys/*' 2>/dev/null | head; true")
        self.check("G3", out == "", f"no root-owned path is writable: {out[:200]!r}")

        started = time.monotonic()
        code, _, err, mark = self.agent(box, "setsid sleep 91 >/dev/null 2>&1 & sleep 92", limit=3)
        elapsed = time.monotonic() - started
        self.check("G4", code in (124, 137) and f"aura:timeout:{mark}" in err and elapsed < 40,
                   f"deadline: exit {code} after {elapsed:.1f}s")
        left = self.agent_processes(box)
        self.check("G4", left == "none", f"nothing survives a deadline, detached included: {left}")
        code, _, err, _ = self.agent(box, "exit 124")
        self.check("G4", code == 124 and "aura:timeout" not in err, "a command's own exit 124 is not a timeout")

        code, out, _, _ = self.agent(box, "setsid sleep 93 >/dev/null 2>&1 & sleep 1; echo done")
        left = self.agent_processes(box)
        self.check("G5", code == 0 and out == "done" and left == "none", f"detached process ends with its command: {left}")
        started = time.monotonic()
        code, out, _, _ = self.agent(box, "sleep 94 & echo done", limit=60)
        elapsed = time.monotonic() - started
        self.check("G5", out == "done" and elapsed < 30, f"a background child holding stdout does not hang: {elapsed:.1f}s")

        command_id = uuid.uuid4().hex
        line = self.command_line(command_id, 120, ["sh", "-c", "setsid sleep 95 & sleep 96"])
        process = subprocess.Popen(
            [self.cli, "sandbox", "exec", "--id", box, *self.scope, "--", line],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
        )
        time.sleep(8)
        before = self.agent_processes(box)
        code, _, err = self.sh(box, f"bash {LIB}/run.sh --cancel {command_id}")
        try:
            process.wait(timeout=30)
            ended = True
        except subprocess.TimeoutExpired:
            process.kill()
            ended = False
        left = self.agent_processes(box)
        self.check("G6", before != "none" and code == 0 and ended and left == "none",
                   f"cancel by id: running {before}, cancel exit {code}, client ended {ended}, left {left} {err[-100:]}")
        code, out, _ = self.sh(box, "ls -d /sys/fs/cgroup/aura-* 2>/dev/null || echo none")
        self.check("G6", out == "none", f"no command cgroups left: {out}")

    def real_clients(self, box: str) -> None:
        """The tools the agent actually uses, through the proxy's name check."""
        for label, script in (
            ("git reads a repository", "git ls-remote https://github.com/octocat/Hello-World HEAD"),
            ("git clones a repository", "rm -rf hw && git clone -q --depth 1 https://github.com/octocat/Hello-World hw && test -f hw/README"),
            ("pip downloads a wheel", "python3 -m pip download -q --no-deps --only-binary=:all: -d dl numpy && ls dl/*.whl"),
            ("a large registry document", "curl -q -sSf --max-time 60 -o react.json https://registry.npmjs.org/react && test $(stat -c %s react.json) -gt 1000000"),
            ("thirty requests at once", "seq 30 | xargs -P 30 -I{} curl -q -sSf --max-time 60 -o /dev/null https://pypi.org/simple/pip/"),
            ("TLS 1.2 still works", "curl -q -sSf --tls-max 1.2 --max-time 30 -o /dev/null https://github.com/robots.txt"),
        ):
            code, _, err, _ = self.agent(box, script, limit=120)
            self.check("G3b", code == 0, f"{label}: exit {code} {err[-160:]}")
        self.agent(box, "rm -rf hw dl react.json")

    def fence_loss(self, box: str) -> None:
        for label, damage in (("firewall", "nft delete table inet aura"), ("proxy", "pkill -u aura-proxy; sleep 1")):
            self.sh(box, damage)
            code, out, err, mark = self.agent(box, "echo ran")
            self.check("G7", code == 97 and "ran" not in out and f"aura:fence-missing:{mark}" in err,
                       f"no {label}, no command: exit {code}")
            code, _, err = self.fence(box)
            self.check("G7", code == 0, f"fence restored after losing the {label}: exit {code} {err[-150:]}")

    def files(self, box: str) -> None:
        stage = self.stage()
        self.sh(box, f"head -c 5242880 /dev/urandom > {stage}")
        want = self.sh(box, f"sha256sum < {stage}")[1]
        code, _, err = self.file(box, "write", "bin/my blob", stage)
        got = self.sh(box, f"sha256sum < '{WS}/bin/my blob'; stat -c '%U %a' '{WS}/bin/my blob'; test -e {stage} || echo unstaged")[1]
        self.check("G8", code == 0 and want in got and "aura 644" in got and "unstaged" in got, f"write: exit {code} {got!r} {err[-150:]}")
        stage = self.stage()
        code, _, err = self.file(box, "read", "bin/my blob", stage, "10000000")
        got = self.sh(box, f"sha256sum < {stage}; stat -c '%U %a' {stage}")[1]
        self.check("G8", code == 0 and want in got and "root 600" in got, f"read: exit {code} {got!r} {err[-150:]}")
        self.file(box, "discard", stage)
        self.sh(box, f"chmod 755 '{WS}/bin/my blob'")
        stage = self.stage()
        self.sh(box, f"echo again > {stage}")
        self.file(box, "write", "bin/my blob", stage)
        mode = self.sh(box, f"stat -c %a '{WS}/bin/my blob'")[1]
        self.check("G8", mode == "755", f"overwriting keeps the file's mode: {mode}")

        self.agent(box, "ln -s /etc/passwd pw; ln -s /etc etcdir; mkdir -p sub; ln -s /var/lib sub/link; "
                        "mkfifo pipe; mkdir adir; printf 12345678901 > eleven")
        for label, args, expected in (
            ("read through a file link", ("read", "pw"), 91),
            ("read through a folder link", ("read", "etcdir/hostname"), 91),
            ("read a missing file", ("read", "nothing"), 92),
            ("read a pipe", ("read", "pipe"), 92),
            ("read over the limit", ("read", "eleven"), 93),
        ):
            stage = self.stage()
            code, _, _ = self.file(box, *args, stage, "10")
            left = self.sh(box, f"test -e {stage} && echo staged || echo clean")[1]
            self.check("G8", code == expected and left == "clean", f"{label}: exit {code} (want {expected}), {left}")
        for label, path in (("write through a folder link", "etcdir/aura-owned"),
                            ("write through a nested link", "sub/link/aura-owned"),
                            ("write onto a folder", "adir")):
            stage = self.stage()
            self.sh(box, f"echo x > {stage}")
            code, _, _ = self.file(box, "write", path, stage)
            self.check("G8", code == 91, f"{label}: exit {code}")
        out = self.sh(box, "ls /etc/aura-owned /var/lib/aura-owned 2>/dev/null || echo untouched")[1]
        self.check("G8", out == "untouched", "nothing was written outside the workspace")
        code, _, _ = self.file(box, "read", "eleven", "/etc/shadow", "100")
        mode = self.sh(box, "stat -c '%U %s' /etc/shadow")[1]
        self.check("G8", code == 2 and not mode.endswith(" 0"), f"a staging path outside staging is refused: exit {code} {mode}")

        code, out, _ = self.file(box, "usage")
        self.check("G9", code == 0 and re.fullmatch(r"\d+", out) is not None and int(out) > 0, f"usage: {out!r}")
        self.sh(box, "setpriv --reuid=10001 --regid=10001 --init-groups sh -c 'setsid sleep 97 >/dev/null 2>&1 &'")
        before = self.agent_processes(box)
        self.sh(box, f"echo x > {self.stage()}")
        code, _, err = self.file(box, "quiesce")
        left = self.agent_processes(box)
        staged = self.sh(box, f"ls {STAGING} | wc -l")[1]
        self.check("G9", before != "none" and code == 0 and left == "none" and staged == "0",
                   f"quiesce: exit {code}, processes {before} -> {left}, staged files {staged}")

    def review_findings(self, box: str) -> None:
        """The security review's findings, on a real kernel."""
        self.sh(box, "rm -f /var/lib/aura/ready")
        stage = self.stage()
        self.sh(box, f"echo x > {stage}")
        code, _, _ = self.file(box, "write", "x", stage)
        self.check("G11", self.refused(box) and code == 97, f"no ready mark, no command or file write ({code})")

        code, _, err = self.sh(box, f"bash {LIB}/fence.sh " + " ".join([*HOSTS, "example.com"]))
        self.check("G11", code == 11 and self.refused(box),
                   f"a fence that fails its proof leaves the sandbox unusable: exit {code} {err[-120:]}")
        self.sh(box, "(setsid python3 -m http.server 9999 >/dev/null 2>&1 &); sleep 2")
        code, _, err = self.fence(box)
        self.check("G11", code == 14 and self.refused(box), f"a privileged TCP listener fails the fence: exit {code}")
        self.sh(box, "pkill -f 'http.server 9999'; sleep 1")
        self.fence(box)

        hostile = (
            "printf 'proxy = \"http://127.0.0.1:1\"\\nconnect-to = \"::127.0.0.1:1\"\\n' > /home/aura/.curlrc; "
            "echo 'touch /tmp/rc-ran' | tee /home/aura/.bashrc /home/aura/.profile >/dev/null"
        )
        self.agent(box, hostile)
        self.sh(box, "rm -f /tmp/rc-ran")
        code, _, err = self.fence(box)
        self.file(box, "usage")
        stage = self.stage()
        self.sh(box, f"echo x > {stage}")
        write, _, _ = self.file(box, "write", "after-rc", stage)
        ran = self.sh(box, "test -e /tmp/rc-ran && echo yes || echo no")[1]
        self.check("G11", code == 0 and write == 0 and ran == "no",
                   f"agent startup files take no part in proof or file work: fence {code}, ran {ran} {err[-120:]}")

        command_id = uuid.uuid4().hex
        self.sh(box, f"bash {LIB}/run.sh --cancel {command_id}")
        code, out, _ = self.sh(box, self.command_line(command_id, 30, ["echo", "ran"]))
        self.check("G11", code == 143 and "ran" not in out, f"a cancel before the start holds: exit {code}")

        code, out, _, _ = self.agent(box, "ls /proc/self/fd | tr '\\n' ' '")
        self.check("G11", out.split() == ["0", "1", "2", "3"], f"only the standard streams are handed down: {out}")
        code, _, _, _ = self.agent(box, "unshare -Urn true")
        self.check("G11", code != 0, "user namespaces are off")
        probe = "import socket; socket.socket(40, socket.SOCK_STREAM)"
        code, _, err, _ = self.agent(box, f"/usr/bin/python3 -c '{probe}'")
        self.check("G11", code != 0 and "PermissionError" in err, f"no hypervisor socket: exit {code}")
        probe = ("import ctypes; l = ctypes.CDLL(None, use_errno=True); "
                 "r = l.syscall(425, 4, ctypes.create_string_buffer(256)); print(r, ctypes.get_errno())")
        code, out, _, _ = self.agent(box, f"/usr/bin/python3 -c '{probe}'")
        self.check("G11", out == "-1 38", f"no io_uring: {out!r}")
        code, _, err, _ = self.agent(
            box, "/usr/bin/python3 -c 'import socket,subprocess; socket.socket(); subprocess.run([\"true\"], check=True)'")
        self.check("G11", code == 0, f"control: ordinary sockets and processes work: exit {code} {err[-150:]}")
        self.agent(box, "mkdir -p hidden && head -c 100000 /dev/zero > hidden/f && chmod 000 hidden")
        code, out, _ = self.file(box, "usage")
        self.check("G11", code == 0 and out.isdigit() and int(out) > 100000, f"usage counts what the agent hid: {out}")
        out = self.sh(box, "ls -d /sys/fs/cgroup/aura-* 2>/dev/null || echo none")[1]
        self.check("G11", out == "none", f"no groups left behind: {out}")
        settings = self.sh(box, "for f in /proc/sys/kernel/yama/ptrace_scope /proc/sys/user/max_user_namespaces; do "
                                "echo \"$f=$(cat $f 2>/dev/null || echo absent)\"; done")[1]
        self.record("G11", "INFO", " ".join(settings.split()))

    def fronting(self, box: str) -> None:
        """Can a tunnel opened to an allowed host be used to reach another site on its network?"""
        for allowed, other in (
            ("pypi.org", "www.fastly.com"),
            ("files.pythonhosted.org", "www.fastly.com"),
            ("registry.npmjs.org", "www.cloudflare.com"),
            ("objects.githubusercontent.com", "pages.github.com"),
        ):
            code, out, err, _ = self.agent(
                box, f"curl -q -sS --max-time 20 -o /dev/null -w '%{{http_code}}' "
                     f"--connect-to {other}:443:{allowed}:443 https://{other}/")
            served = code == 0 and out[:1] in ("2", "3")
            if "ssh.railway.com" in err:
                # The CLI never reached the sandbox: that is no evidence of a refusal.
                self.record("G12", "FAIL", f"inconclusive for {allowed} to {other}: the CLI could not connect")
                continue
            self.check("G12", not served,
                       f"tunnel to {allowed}, TLS name {other}: curl {code}, HTTP {out or '-'} {err[-100:]}")
        # The proxy cannot see inside TLS: does the network honour a different Host header?
        for allowed, other, marker in (
            ("pypi.org", "www.fastly.com", "Fastly"),
            ("files.pythonhosted.org", "www.fastly.com", "Fastly"),
            ("registry.npmjs.org", "www.cloudflare.com", "Cloudflare"),
            ("objects.githubusercontent.com", "pages.github.com", "GitHub Pages"),
            ("github.com", "pages.github.com", "GitHub Pages"),
            ("codeload.github.com", "pages.github.com", "GitHub Pages"),
            ("api.github.com", "pages.github.com", "GitHub Pages"),
        ):
            code, out, err, _ = self.agent(
                box, f"curl -q -sS --max-time 20 -D /tmp/h -o /tmp/b -w '%{{http_code}}' -H 'Host: {other}' "
                     f"https://{allowed}/; echo; grep -io '<title>[^<]*' /tmp/b | head -1; "
                     f"grep -i '^location:' /tmp/h | head -1; rm -f /tmp/h /tmp/b")
            lines = out.splitlines()
            status = lines[0] if lines else "-"
            rest = " ".join(lines[1:])[:140]
            served = (status[:1] == "2" and marker in rest) or (status[:1] == "3" and other in rest)
            self.check("G13", not served, f"tunnel to {allowed}, Host {other}: HTTP {status} {rest}")

    def hostile_restore(self, box: str) -> None:
        """What an agent can leave on disk must not turn into root's actions after a restore."""
        # chmod first: an earlier check leaves a folder that `rm` alone cannot empty.
        self.agent(box, "cd /home/aura && chmod -R u+rwx workspace && rm -rf workspace && ln -s /etc workspace; "
                        "echo 'id -u > /tmp/profile-ran' | tee .bashrc .profile >/dev/null")
        link = self.sh(box, "readlink /home/aura/workspace")[1]
        self.check("G10", link == "/etc", f"control: the agent replaced its workspace with a link: {link!r}")
        before = self.sh(box, "stat -c '%U:%G %a' /etc")[1]
        self.checkpoints.append(CHECKPOINT)
        done, seconds = self.call(["checkpoint", *self.scope, "create", CHECKPOINT, "--id", box])
        if done.returncode != 0:
            raise RuntimeError("checkpoint failed: " + done.stderr.decode("utf-8", "replace")[-500:])
        self.timings["checkpoint_s"] = round(seconds, 1)
        self.destroy(box)
        second, _ = self.create("--checkpoint", CHECKPOINT)
        code, _, err, mark = self.agent(second, "echo ran")
        self.check("G10", code == 97 and f"aura:fence-missing:{mark}" in err,
                   f"a restored sandbox runs nothing before fencing: exit {code}")
        self.sh(second, "rm -f /tmp/profile-ran")
        self.install(second, packages=False)
        code, out, err = self.fence(second)
        self.check("G10", code == 0, f"fence after restore: exit {code} {err[-300:]}")
        after = self.sh(second, "stat -c '%U:%G %a' /etc")[1]
        self.check("G10", after == before, f"/etc untouched by root: {before} -> {after}")
        ran = self.sh(second, "cat /tmp/profile-ran 2>/dev/null || echo no")[1]
        self.check("G10", ran in ("no", "10001"), f"root did not run the agent's shell files: {ran}")
        stage = self.stage()
        self.sh(second, f"echo x > {stage}")
        code, _, _ = self.file(second, "write", "aura-owned", stage)
        self.check("G10", code == 91, f"a workspace replaced by a link is refused: exit {code}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--project", required=True)
    parser.add_argument("--environment", default="production")
    parser.add_argument("--out", required=True)
    options = parser.parse_args()
    if options.project == PRODUCTION_PROJECT:
        raise SystemExit("refusing to run the proof in AURA's production project")
    proof = Proof(options.project, options.environment)
    try:
        proof.prove()
    except Exception as exc:  # the report must say what broke, and cleanup must still run
        proof.record("--", "FAIL", f"{type(exc).__name__}: {exc}")
    finally:
        proof.cleanup()
        Path(options.out).write_text(
            json.dumps({"timings": proof.timings, "results": proof.results}, indent=2), encoding="utf-8"
        )
    failed = sum(r["verdict"] == "FAIL" for r in proof.results)
    print(f"{len(proof.results) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
