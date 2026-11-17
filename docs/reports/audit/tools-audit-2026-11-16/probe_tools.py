"""Tool audit probe: build the in-memory container, dump the registry, cross-check persona
grants, and smoke-execute every tool through ToolRegistry.execute (the real dispatch path)."""
import asyncio, json, os, sys, uuid

os.environ.setdefault("TESTING", "True")
from aura_core.config import load_settings  # noqa
from aura_core.container import Container
from aura_core.domain.messages import ToolCall
from aura_core.domain.tools import ToolSensitivity
from aura_core.ports.runtime import ToolContext
from aura_core.domain.identifiers import UserId, ToolCallId

OUT = sys.argv[1]

# Hand-written arguments for tools where a schema-derived dummy would only test validation.
ARGS = {
    "calculator": {"expression": "(1250 * 0.15) + 30"},
    "current_datetime": {"timezone_offset_hours": 2},
    "search_knowledge": {"query": "annual leave"},
    "recall_memory": {"query": "diet"},
    "remember_fact": {"fact": "The user is vegetarian.", "subject": "diet"},
    "prioritize_tasks": {"tasks": [{"title": "pay rent", "due_date": "2026-11-20", "importance": "high"},
                                   {"title": "read book"}]},
    "find_calendar_slots": {"busy": [{"start": "2026-11-17T09:00:00+00:00", "end": "2026-11-17T10:00:00+00:00"}],
                            "search_start": "2026-11-17T08:00:00+00:00",
                            "search_end": "2026-11-17T18:00:00+00:00", "duration_minutes": 30},
    "propose_task": {"title": "Pay rent", "dueDate": "2026-11-20"},
    "create_task": {"title": "Pay rent", "due_date": "2026-11-20", "priority": "high"},
    "run_command": {"command": "echo hi"},
    "edit_file": {"path": "README.md", "find": "a", "replace": "b"},
    "delegate_to_agent": {"target_agent_id": "task_planner", "subtask": "List my tasks."},
}


def dummy(p):
    if p.default is not None:
        return p.default
    t = p.type
    if p.enum:
        return p.enum[0]
    if t == "string":
        if p.format == "date-time":
            return "2026-11-17T09:00:00+00:00"
        if p.format == "date":
            return "2026-11-17"
        return "audit-probe"
    if t == "integer":
        return int(p.minimum) if p.minimum is not None else 1
    if t == "number":
        return float(p.minimum) if p.minimum is not None else 1.0
    if t == "boolean":
        return False
    if t == "array":
        return []
    if t == "object":
        return {}
    return "x"


async def main():
    c = Container(load_settings(refresh=True, validate=False))
    reg = c.tools
    report = {"tools": [], "personas": [], "exec": []}
    names = set(reg.names())
    for n in reg.names():
        s = reg._tools[n].spec
        report["tools"].append({
            "name": n, "category": str(s.category), "sensitivity": s.sensitivity.name,
            "side_effecting": s.side_effecting, "workspace_local": s.workspace_local,
            "capability": s.required_capability, "timeout_s": s.timeout_s,
            "tags": list(s.tags), "params": [(p.name, p.type, p.required) for p in s.parameters],
            "has_preview": s.preview is not None,
        })
    granted_anywhere = set()
    for a in c.agents:
        allowed = None if a.allowed_tools is None else sorted(a.allowed_tools)
        if allowed:
            granted_anywhere |= set(allowed)
        report["personas"].append({
            "id": a.id, "allowed": allowed,
            "dangling": sorted(set(allowed or ()) - names),
            "ceiling": getattr(a, "risk_ceiling", None).name if getattr(a, "risk_ceiling", None) else None,
            "required_caps": sorted(getattr(a, "required_capabilities", ()) or ()),
        })
    report["registered_but_ungranted"] = sorted(names - granted_anywhere)

    caps = ("engineer_agent",)
    user = c.settings  # noqa
    for n in reg.names():
        s = reg._tools[n].spec
        args = ARGS.get(n) or {p.name: dummy(p) for p in s.parameters if p.required}
        ctx = ToolContext(user_id=UserId("audit-user"), correlation_id=f"audit-{n}", deadline_s=30,
                          capabilities=caps)
        try:
            res, rep = await asyncio.wait_for(
                reg.execute(ToolCall(id=ToolCallId(f"c-{uuid.uuid4().hex[:6]}"), name=n, arguments=args), ctx,
                            capabilities=caps, confirmed=True), 40)
            report["exec"].append({"name": n, "args": args, "ok": rep.ok, "allowed": rep.allowed,
                                   "error_kind": rep.error_kind, "policy": rep.policy_reason,
                                   "content": str(getattr(res, "content", ""))[:300]})
        except Exception as e:  # a raise out of execute() is itself a finding
            report["exec"].append({"name": n, "args": args, "raised": f"{type(e).__name__}: {e}"[:300]})
    chain = []
    ctx = ToolContext(user_id=UserId("audit-user"), correlation_id="audit-chain", deadline_s=30)
    async def call(n, a):
        res, rep = await reg.execute(ToolCall(id=ToolCallId(f"c-{uuid.uuid4().hex[:6]}"), name=n, arguments=a), ctx, confirmed=True)
        chain.append({"name": n, "args": a, "ok": rep.ok, "kind": rep.error_kind, "content": str(res.content)[:400]})
        return str(res.content)
    for dom, create_args, upd in [
        ("task", {"title": "Chain task"}, {"title": "Chain task v2"}),
        ("calendar_event", {"title": "Standup", "start_time": "2026-11-17T09:00:00+00:00", "end_time": "2026-11-17T09:15:00+00:00"}, {"title": "Standup v2"}),
        ("finance_record", {"amount": 12.5, "type": "expense", "category": "food", "occurred_at": "2026-11-16"}, {"amount": 13}),
        ("health_record", {"record_date": "2026-11-16", "water_ml": 1500}, {"water_ml": 2000}),
    ]:
        listname = {"task": "list_tasks", "calendar_event": "list_calendar_events",
                    "finance_record": "list_finance_records", "health_record": "list_health_records"}[dom]
        out = await call(f"create_{dom}", create_args)
        import re
        m = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|(?:id[\"': =]+)([A-Za-z0-9_-]{6,})", out)
        rid = (m.group(1) or m.group(0)) if m else None
        await call(listname, {})
        if rid:
            await call(f"update_{dom}", {"record_id": rid, **upd})
            await call(f"delete_{dom}", {"record_id": rid})
            await call(listname, {})
    report["chain"] = chain
    json.dump(report, open(OUT, "w"), indent=1, default=str)
    print("tools", len(names))


asyncio.run(main())
