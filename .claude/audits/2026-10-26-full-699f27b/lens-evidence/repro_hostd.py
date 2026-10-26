# Fake daemon mirroring aura_hostd/lib/src/rpc_client.dart: for method "run" it sends a
# {"type":"stream"} frame per output chunk, then {"type":"result"}.
import asyncio, sys, logging
sys.path.insert(0, sys.argv[1]); logging.disable(logging.CRITICAL)
from aura_core.adapters.host_agent import HostAgentSession, HostAgentRegistry, HostAgentSandboxRunner
from aura_core.domain.identifiers import UserId

async def main():
    holder = {}
    async def send(frame):
        s = holder["s"]
        async def daemon():
            await asyncio.sleep(0)
            if frame["method"] == "create_task":
                s.resolve({"type": "result", "id": frame["id"], "result": {"task_id": "t1"}}); return
            s.resolve({"type": "stream", "id": frame["id"], "stream": "stdout", "data": "diff --git a/x b/x\n"})
            s.resolve({"type": "result", "id": frame["id"], "result": {"stdout": "diff --git a/x b/x\n", "stderr": "", "exit_code": 0}})
        asyncio.create_task(daemon())
    s = HostAgentSession(UserId("u1"), send); holder["s"] = s
    reg = HostAgentRegistry(); await reg.register(s)
    runner = HostAgentSandboxRunner(reg)
    h = await runner.create_task(user_id=UserId("u1"))
    r = await runner.run(h, ["git", "diff", "HEAD"], timeout_s=5)
    print("non-streaming run ->", repr(r.stdout), r.exit_code)
    evs = [e async for e in runner.run_streaming(h, ["git", "diff", "HEAD"], timeout_s=5)]
    print("streaming run ->", [(e.kind, getattr(e, "exit_code", None)) for e in evs])
asyncio.run(main())
