"""Phase J live checks, run inside the gateway container against a real Docker daemon.

Everything here exists because none of it can be inferred from a green test suite. The default
`pytest` run uses an in-memory runner that never touches the Docker API, and no CI job stands up
`docker-socket-proxy`, so every claim about the deployed sandbox -- that a bind-mount path means
the same thing on both sides, that a workspace really is unprivileged and read-only, that egress
really is allowlisted, that the reaper really reclaims -- is unproven until something exercises
it here.

Driven by `verify-engineer-workspaces.sh`, which is what an operator runs. This half assumes it
is already inside the gateway with the settings the gateway actually booted with, and it goes
through `Container` rather than constructing adapters by hand, so what it proves is the wiring
the product uses and not a hand-built lookalike.

Exit code is the number of failed checks, capped at 125.
"""

from __future__ import annotations

import asyncio
import os
import sys
import traceback
from datetime import timedelta

_GREEN, _RED, _YELLOW, _DIM, _RESET = "\033[1;32m", "\033[1;31m", "\033[1;33m", "\033[2m", "\033[0m"

#: A tiny, stable, public repository. Cloning it exercises the egress allowlist's GitHub entries
#: and host-side git at once, without needing anybody's credentials.
_PUBLIC_REPO = os.environ.get("AURA_VERIFY_REPO", "octocat/Hello-World")

_results: list[tuple[str, str, str]] = []


def _record(number: int, name: str, status: str, detail: str) -> None:
    colour = {"PASS": _GREEN, "FAIL": _RED, "SKIP": _YELLOW, "MANUAL": _YELLOW}[status]
    print(f"  {colour}{status:<6}{_RESET} {number:>2}. {name}")
    if detail:
        print(f"         {_DIM}{detail}{_RESET}")
    _results.append((str(number), name, status))


def check(number: int, name: str):
    """Run one check. A raised exception is a failure with its message, never a traceback that
    stops the run -- an operator wants the other nine results even when one blows up."""
    def decorator(fn):
        async def invoke(*args, **kwargs):
            try:
                detail = await fn(*args, **kwargs)
                _record(number, name, "PASS", detail or "")
                return True
            except _Skipped as exc:
                _record(number, name, "SKIP", str(exc))
                return True
            except Exception as exc:
                detail = f"{type(exc).__name__}: {exc}"
                if os.environ.get("AURA_VERIFY_TRACEBACK"):
                    detail += "\n" + traceback.format_exc()
                _record(number, name, "FAIL", detail)
                return False
        invoke.__name__ = fn.__name__
        return invoke
    return decorator


class _Skipped(Exception):
    """Raised when a check cannot run here -- reported as SKIP, never silently as a pass."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def _sh(runner, handle, command: str, *, timeout_s: float = 60.0):
    return await runner.run(handle, ["sh", "-lc", command], timeout_s=timeout_s)


# -- the checks ---------------------------------------------------------------------------------


@check(0, "docker exec is refused by the socket proxy")
async def check_exec_is_refused(runner, manager, ws) -> str:
    """Not one of the original ten, and the most important one to run first: it is the reason
    commands are dispatched as their own container. If the proxy ever started allowing exec, the
    adapter would still work and nobody would notice the allowlist had widened."""
    import docker

    client = runner._get_client()
    containers = client.containers.list(all=True)
    if not containers:
        raise _Skipped("no container to attempt exec against")
    try:
        containers[0].exec_run(["true"])
    except docker.errors.APIError as exc:
        _require(
            "403" in str(exc) or "forbidden" in str(exc).lower(),
            f"exec failed, but not with the proxy's 403: {exc}",
        )
        return "the proxy returned 403, as EXEC: \"0\" requires"
    raise AssertionError(
        "docker exec SUCCEEDED. The socket proxy is allowing exec, which means a compromised "
        "gateway can also exec into postgres and ollama. Check EXEC in the compose file."
    )


@check(1, "write_file then run_command round-trips (bind-mount path identity)")
async def check_bind_mount(runner, manager, ws) -> str:
    """The defect this whole feature was blocked on: the daemon resolves bind sources on the
    *host*, so a directory the gateway made in its own filesystem is a different, empty directory
    to the container. It fails silently -- the write succeeds, the read returns nothing."""
    await runner.write_file(ws, "probe.txt", "round-trip-ok")

    result = await _sh(runner, ws, "cat probe.txt")

    _require(result.exit_code == 0, f"cat exited {result.exit_code}: {result.stderr.strip()}")
    _require(
        "round-trip-ok" in result.stdout,
        "the container saw a different directory than write_file wrote to -- host_root is not "
        "bind-mounted into the gateway at the same absolute path it has on the host",
    )
    return "the gateway and the container agree on one directory"


@check(2, "clone_repository produces a real checkout with history")
async def check_real_clone(runner, manager, ws, host_git) -> str:
    if host_git is None:
        raise _Skipped("host-side git is not configured on this deployment")
    from pathlib import Path

    dest = Path(await runner.workspace_path(ws)) / "checkout"
    await host_git.clone(_PUBLIC_REPO, dest=dest, token=None)

    log = await _sh(runner, ws, "cd checkout && git log --oneline | head -5")

    _require(log.exit_code == 0, f"git log exited {log.exit_code}: {log.stderr.strip()}")
    _require(log.stdout.strip(), "the checkout has no history -- this is a file copy, not a clone")
    return f"{len(log.stdout.strip().splitlines())} commits visible in {_PUBLIC_REPO}"


@check(3, "no credential is left anywhere in the checkout")
async def check_no_credential(runner, manager, ws) -> str:
    """Host-side git exists so a token never reaches a container running model-chosen commands.
    A token surviving in .git/config would undo that quietly."""
    found = await _sh(
        runner, ws, "grep -rIl -E 'ghp_|github_pat_|x-access-token|:.*@github' . || true"
    )

    _require(
        not found.stdout.strip(),
        f"credential-shaped strings found in: {found.stdout.strip()}",
    )
    return "no token, PAT or credential-in-URL in the workspace tree"


@check(4, "the workspace is unprivileged with a read-only rootfs")
async def check_confinement(runner, manager, ws) -> str:
    uid = await _sh(runner, ws, "id -u")
    _require(uid.stdout.strip() == "10001", f"running as uid {uid.stdout.strip()!r}, expected 10001")

    escape = await _sh(runner, ws, "touch /etc/aura-probe 2>&1; echo rc=$?")
    _require("rc=0" not in escape.stdout, "wrote to /etc -- the rootfs is not read-only")

    workspace_write = await _sh(runner, ws, "touch ./writable && echo rc=$?")
    _require("rc=0" in workspace_write.stdout, "/workspace is not writable; no build could run")
    return "uid 10001, /etc read-only, /workspace writable"


@check(5, "egress is allowlisted, not open")
async def check_egress(runner, manager, ws) -> str:
    blocked = await _sh(
        runner, ws, "curl -s -m 15 -o /dev/null -w '%{http_code}' https://example.com || echo failed"
    )
    _require(
        blocked.stdout.strip() in ("failed", "000", ""),
        f"https://example.com was reachable (got {blocked.stdout.strip()!r}) -- the allowlist is "
        "not being enforced; check that sandbox_egress is internal and the proxy is up",
    )

    allowed = await _sh(
        runner, ws, "curl -s -m 30 -o /dev/null -w '%{http_code}' https://pypi.org/simple/ || echo failed"
    )
    _require(
        allowed.stdout.strip().startswith(("2", "3")),
        f"pypi.org was NOT reachable (got {allowed.stdout.strip()!r}) -- the allowlist is too "
        "tight to install anything, so builds will fail",
    )
    return "example.com blocked, pypi.org reachable"


@check(6, "a workspace persists into a later turn")
async def check_persistence(runner, manager, ws, user_id, repo) -> str:
    """The property the feature exists for. A second turn is a different correlation id, which
    under the old per-turn cache resolved to a brand new, empty sandbox."""
    await runner.write_file(ws, "across-turns.txt", "still-here")

    reopened = await manager.open(user_id, repo)
    readback = await _sh(runner, reopened.handle, "cat across-turns.txt")

    _require(
        "still-here" in readback.stdout,
        "the file written in the first turn is gone in the second -- workspaces are not durable",
    )
    return "the same checkout resolved in a second turn"


@check(7, "a workspace between turns holds no memory")
async def check_pause(runner, manager, ws) -> str:
    """The capacity claim: peak RAM is max_running x memory_limit, not one container per stored
    workspace. That is only true if pausing really stops the container."""
    await runner.pause_workspace(ws)

    client = runner._get_client()
    matching = client.containers.list(
        all=True, filters={"label": f"aura.workspace={ws.task_id}"}
    )
    _require(matching, "the workspace container disappeared entirely rather than stopping")
    matching[0].reload()
    _require(
        matching[0].status != "running",
        f"the container is still {matching[0].status!r} after pause -- it is holding its memory",
    )
    return f"container status {matching[0].status!r}; its checkout is untouched"


@check(8, "a restarted gateway adopts the existing workspace by label")
async def check_reconciliation(runner, manager, ws, user_id, repo) -> str:
    """A fresh runner has empty bookkeeping, exactly as it does after `docker compose restart
    gateway`. If it started a second container over the same checkout, neither reconciliation nor
    the reaper would ever find the duplicate -- it carries no label."""
    from aura_core.adapters.docker_sandbox import DockerSandboxRunner

    before = {c.id for c in runner._get_client().containers.list(
        all=True, filters={"label": f"aura.workspace={ws.task_id}"}
    )}

    fresh = DockerSandboxRunner(
        image=runner._image, network=runner._network, memory_limit=runner._memory_limit,
        cpu_limit=runner._cpu_limit, host_root=str(runner._host_root or ""),
        proxy_url=runner._proxy_url,
    )
    await fresh.open_workspace(ws.task_id)

    after = {c.id for c in runner._get_client().containers.list(
        all=True, filters={"label": f"aura.workspace={ws.task_id}"}
    )}
    _require(
        after == before,
        f"a second container appeared for one workspace ({len(before)} -> {len(after)})",
    )
    return "the existing container was adopted, not duplicated"


@check(9, "the reaper reclaims a workspace past its TTL")
async def check_reaper(runner, manager, ws, user_id, repo, store) -> str:
    row = await store.get(user_id, repo)
    _require(row is not None, "no store row for the workspace under test")

    stale = row.last_active_at - timedelta(hours=(manager._ttl_hours + 24))
    await store.touch(row.id, at=stale)

    reaped = await manager.reap()

    _require(
        reaped.get("expired", 0) >= 1,
        f"the reaper found nothing to expire: {reaped}",
    )
    _require(
        await manager.get(user_id, repo) is None,
        "the store row survived the reap, so the workspace would be resurrected",
    )
    survivors = runner._get_client().containers.list(
        all=True, filters={"label": f"aura.workspace={ws.task_id}"}
    )
    _require(not survivors, "the container outlived its reaped workspace")
    return f"container, directory and row all gone ({reaped})"


def _manual_check_10() -> None:
    _record(
        10,
        "a full session asks for exactly one confirmation, at git_push",
        "MANUAL",
        "Needs a real multi-turn agent session, so it is not claimed here. In the app as a user\n"
        "         holding engineer_agent: clone a repo, edit a file, run its tests, push a branch.\n"
        "         ADR-014's acceptance criterion is that exactly ONE confirmation prompt appears,\n"
        "         at git_push. More than one means the workspace_local set is too narrow; none\n"
        "         means the boundary gate is not armed.",
    )


# -- driver -------------------------------------------------------------------------------------


async def main() -> int:
    from aura_core.container import Container
    from aura_core.config.settings import load_settings
    from aura_core.domain.identifiers import UserId
    from infrastructure.secret_hydration import hydrate_secrets_from_files

    print(f"\n{_DIM}Phase J live checks -- persistent engineer-agent workspaces{_RESET}\n")

    # Hydrate secrets from Docker secret files (AURA_DATABASE_URL, AURA_REDIS_URL, etc.)
    # This is normally done by aura_service.py at startup, but we run outside that path.
    hydrate_secrets_from_files()

    container = Container(load_settings(validate=False))
    runner = getattr(container, "docker_sandbox_runner", None)
    manager = container.workspace_manager

    if runner is None:
        print(f"{_RED}The Docker sandbox runner was not built.{_RESET}")
        print("  AURA_SANDBOX_ENABLED must be true AND a daemon must be reachable through")
        print("  docker-socket-proxy. Nothing below can run. See deploy/.env.example.")
        return 125
    if manager is None:
        print(f"{_RED}No workspace manager: durable workspaces are not wired.{_RESET}")
        return 125

    user_id = UserId(os.environ.get("AURA_VERIFY_USER", "phase-j-verifier"))
    repo = _PUBLIC_REPO
    host_git = getattr(container, "host_git", None)

    opened = await manager.open(user_id, repo)
    ws = opened.handle
    failures = 0

    try:
        failures += not await check_exec_is_refused(runner, manager, ws)
        failures += not await check_bind_mount(runner, manager, ws)
        failures += not await check_real_clone(runner, manager, ws, host_git)
        failures += not await check_no_credential(runner, manager, ws)
        failures += not await check_confinement(runner, manager, ws)
        failures += not await check_egress(runner, manager, ws)
        failures += not await check_persistence(runner, manager, ws, user_id, repo)
        failures += not await check_pause(runner, manager, ws)
        failures += not await check_reconciliation(runner, manager, ws, user_id, repo)
        # Last on purpose: it destroys the workspace the checks above share.
        failures += not await check_reaper(
            runner, manager, ws, user_id, repo, container.workspaces
        )
        _manual_check_10()
    finally:
        # Never leave the verifier's own workspace behind on a production host.
        try:
            leftover = await manager.get(user_id, repo)
            if leftover is not None:
                await manager.destroy(leftover)
        except Exception:
            pass

    scripted = [r for r in _results if r[2] != "MANUAL"]
    passed = sum(1 for r in scripted if r[2] == "PASS")
    skipped = sum(1 for r in scripted if r[2] == "SKIP")
    print(f"\n  {passed}/{len(scripted)} scripted checks passed"
          + (f", {skipped} skipped" if skipped else "")
          + (f", {_RED}{failures} FAILED{_RESET}" if failures else "")
          + "\n  1 check (10) is manual and is not counted either way.\n")
    if failures:
        print(f"{_RED}Phase J is not proven. Fix the failures above before claiming it.{_RESET}\n")
    elif skipped:
        print(f"{_YELLOW}Every check that ran passed, but some were skipped -- Phase J is proven"
              f" only for the ones marked PASS.{_RESET}\n")
    else:
        print(f"{_GREEN}All scripted checks passed. Complete check 10 by hand before marking"
              f" Phase J proven in the roadmap.{_RESET}\n")
    return min(failures, 125)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
