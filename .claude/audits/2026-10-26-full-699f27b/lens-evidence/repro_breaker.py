import asyncio, sys, logging
sys.path.insert(0, sys.argv[1]); logging.disable(logging.CRITICAL)
from aura_core.domain import Conversation, Message, CompletionRequest
from aura_core.domain.errors import ProviderError
from aura_core.llm import ProviderRouter
from aura_core.llm.providers.echo import EchoProvider

class Flaky(EchoProvider):
    mode = "fail"
    async def complete(self, request):
        if Flaky.mode == "fail":
            raise ProviderError("down", kind="unavailable", retryable=True) if True else None
        if Flaky.mode == "hang":
            await asyncio.sleep(3600)
        return await super().complete(request)

t = [0.0]
async def main():
    p = Flaky(name="local")
    r = ProviderRouter([p], clock=lambda: t[0], circuit_failure_threshold=2, circuit_open_seconds=30)
    req = CompletionRequest(conversation=Conversation.of([Message.user("hi")]))
    for _ in range(2):
        try: await r.complete(req)
        except Exception as e: pass
    st = r._stats["local"].breaker
    print("after 2 failures:", st.state)
    t[0] += 31                       # cooldown elapses -> HALF_OPEN probe allowed
    Flaky.mode = "hang"
    task = asyncio.create_task(r.complete(req)); await asyncio.sleep(0.05)
    task.cancel()                    # e.g. client disconnects mid-probe
    try: await task
    except BaseException as e: print("probe ended with", type(e).__name__)
    Flaky.mode = "ok"                # provider fully recovered
    for hours in (1, 24):
        t[0] += hours * 3600
        try:
            out = await r.complete(req); print(f"+{hours}h: OK")
        except Exception as e:
            print(f"+{hours}h: {type(e).__name__}: {str(e)[:90]} | breaker={st.state}")
asyncio.run(main())
