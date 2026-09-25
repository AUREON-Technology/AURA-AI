"""The Phase 3 scenarios, each a sequence of real v3 requests one virtual user makes.

Routes and payloads are the gateway's own (``aura_core/api/v3.py``): ``ChatRequest`` fields for
``/chat/stream``, multipart ``file`` for ``/voice/transcribe``, ``SpeakRequest`` for
``/voice/speak``, ``IngestRequest`` for ``/knowledge/documents``, ``SearchRequest`` for
``/knowledge/search``. The Engineer flow resumes a turn exactly the way the Flutter client does
(``chat_repository.dart::confirmTaintedTools``): the same message again, with
``confirmed_tools`` and ``confirmed_call_ids`` naming the pending calls it approves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping

from .client import VU, Gateway
from .config import RunConfig
from .stats import Sample

HANDBOOK_ID = "loadtest-handbook"
CORPUS_ID = "loadtest-corpus"

HANDBOOK = """# Employee Handbook

## Leave

### Annual Leave
Annual leave is 21 days per year for all permanent staff. Requests must be submitted at least
14 days in advance through the HR portal. Unused days expire on 31 December.

### Sick Leave
Sick leave is uncapped but requires a medical certificate after three consecutive days.

## Expenses
Expense claims under $250 are approved automatically. Anything above requires manager approval.
Sarah Chen is the director of Finance Operations. Ticket AURA-4419 tracks the reimbursement
backlog for the Cairo office.
"""

_CORPUS_TOPICS = (
    ("Remote work", "Remote work from another country requires HR and Legal approval at least "
     "30 days in advance, and is limited to 60 days per calendar year."),
    ("Travel", "Economy class is the default for flights under six hours. Hotel bookings go "
     "through the travel portal and are reimbursed up to the city rate card."),
    ("Training", "Engineering staff receive $1,500 per year for approved conferences and "
     "courses; unused budget does not roll over."),
    ("Performance reviews", "Annual reviews occur each March with mid-year check-ins in "
     "September. Calibration happens at the department level."),
    ("Parental leave", "Primary caregivers receive 12 weeks of paid leave; secondary caregivers "
     "receive 4 weeks, to be taken within the first year."),
    ("Security", "Laptops must use full-disk encryption. Lost devices are reported to IT within "
     "4 hours. Passwords are reset through the self-service portal."),
    ("Benefits", "Open enrollment runs November 1-15. New hires enroll within 30 days of their "
     "start date. Dental and vision are optional add-ons."),
    ("Procurement", "Purchases above $5,000 need two quotes. Vendors are onboarded by Finance "
     "Operations after a security review."),
)


def corpus_document() -> str:
    """~20 KB of policy prose -- enough chunks that retrieval does real ranking work."""
    sections = ["# Company Policy Compendium\n"]
    for round_ in range(6):
        for title, body in _CORPUS_TOPICS:
            sections.append(
                f"\n## {title} (part {round_ + 1})\n{body} This section was revised in cycle "
                f"{round_ + 1}; earlier revisions are archived by the People team. Questions go "
                f"to the policy owner listed on the intranet page for {title.lower()}.\n"
            )
    return "".join(sections)


SHORT_PROMPTS = (
    "How many days of annual leave do I get?",
    "When do unused leave days expire?",
    "Do I need a medical certificate for sick leave?",
    "Which expense claims are approved automatically?",
    "Who is the director of Finance Operations?",
    "How far in advance must I request leave?",
)

AGENT_PROMPTS = (
    "First search my knowledge base for the annual leave policy, then use the calculator to work "
    "out 21 * 7 / 12, and tell me how many leave days I accrue in seven months.",
    "Look up the expense approval rule in my documents, then calculate how much of a $1,040 "
    "claim is above the $250 auto-approval limit, and summarise both results.",
)

VOICE_FALLBACK_PROMPT = "What is the annual leave policy?"

#: Engineer tools the harness will approve when a turn asks for confirmation. Workspace-local
#: only: nothing here leaves the sandbox. ``git_push``, ``git_commit`` and every ``github_*``
#: write are deliberately absent -- a load run must never push, commit or open PRs.
ENGINEER_AUTO_CONFIRM = frozenset({
    "open_workspace", "list_workspaces", "clone_repository", "write_file", "edit_file",
    "run_command", "git_status", "git_diff",
})


def long_context_message(chars: int) -> str:
    """A pasted multi-turn transcript of about ``chars`` characters, then a question over it."""
    chars = max(500, min(chars, 30_000))  # ChatRequest.message is capped at 32,000
    lines: list[str] = ["Here is our earlier conversation, for context:\n"]
    size = len(lines[0])
    turn = 0
    while size < chars:
        topic, body = _CORPUS_TOPICS[turn % len(_CORPUS_TOPICS)]
        user = f"User: Can you remind me what the {topic.lower()} policy says, point {turn + 1}?\n"
        bot = f"Assistant: {body} (Recap {turn + 1}.)\n"
        lines += [user, bot]
        size += len(user) + len(bot)
        turn += 1
    lines.append(
        "\nGiven all of that and my policy documents, summarise the leave, expense and remote "
        "work rules that apply to me in five bullet points."
    )
    return "".join(lines)


def chat_payload(cfg: RunConfig, vu: VU, message: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "message": message,
        "session_id": vu.session_id,
        "agent": "orchestrator",
        "use_knowledge": True,
        "use_memory": True,
        "use_tools": True,
        "max_output_tokens": cfg.max_output_tokens,
        "temperature": 0.3,
    }
    payload.update(overrides)
    return payload


# -- scenarios ------------------------------------------------------------------------

ScenarioFn = Callable[[Gateway, VU], Awaitable[list[Sample]]]


async def scenario_chat(gw: Gateway, vu: VU) -> list[Sample]:
    """A: a short question answered with retrieval over the user's own documents."""
    sample, result = await gw.chat_stream(
        vu, scenario="chat", op="chat_stream",
        payload=chat_payload(gw.cfg, vu, vu.rng.choice(SHORT_PROMPTS)),
    )
    if sample.ok and not result.text.strip():
        gw.count("chat.empty_answers")
    return [sample]


async def scenario_long_context(gw: Gateway, vu: VU) -> list[Sample]:
    """B: a long pasted history plus retrieval over the larger corpus, in a session that keeps
    accumulating its own episodic history across iterations."""
    message = vu.state.get("long_message")
    if message is None:
        message = vu.state["long_message"] = long_context_message(gw.cfg.long_context_chars)
    sample, _ = await gw.chat_stream(
        vu, scenario="long_context", op="chat_stream",
        payload=chat_payload(gw.cfg, vu, message, session_id=f"{vu.session_id}-long"),
    )
    return [sample]


async def scenario_agent(gw: Gateway, vu: VU) -> list[Sample]:
    """C: planning forced on (``plan: true``) with a request that needs search + calculator, so
    the turn runs plan -> tool -> observe -> respond through the kernel's execution path."""
    sample, result = await gw.chat_stream(
        vu, scenario="agent", op="chat_stream",
        payload=chat_payload(gw.cfg, vu, vu.rng.choice(AGENT_PROMPTS), plan=True),
    )
    gw.count("agent.step_frames", result.steps)
    if sample.ok and result.steps == 0:
        gw.count("agent.turns_without_steps")
    return [sample]


async def scenario_engineer(gw: Gateway, vu: VU) -> list[Sample]:
    """D: the Engineer persona reads, edits and tests inside its sandboxed workspace.

    Pending confirmations for workspace-local tools are approved (up to
    ``engineer_max_confirm_rounds``) the way a user tapping "Confirm" would; anything outside
    :data:`ENGINEER_AUTO_CONFIRM` is left unconfirmed and counted.
    """
    cfg = gw.cfg
    message = (
        f"Open a workspace for the repository {cfg.engineer_repo}. Read README.md, then create "
        f"the file LOADTEST_{vu.index + 1:03d}.md containing the single line 'load test', then "
        f"run `{cfg.engineer_test_command}` and report the result."
    )
    base = chat_payload(
        cfg, vu, message, agent="engineer", session_id=f"{vu.session_id}-eng", use_knowledge=False,
    )
    samples: list[Sample] = []
    sample, result = await gw.chat_stream(vu, scenario="engineer", op="chat_stream", payload=base)
    samples.append(sample)
    for _round in range(cfg.engineer_max_confirm_rounds):
        if not sample.ok or result.done is None:
            break
        pending = list(result.done.get("pending_tool_calls") or [])
        approve = [p for p in pending if p.get("tool") in ENGINEER_AUTO_CONFIRM and p.get("call_id")]
        refused = len(pending) - len(approve)
        if refused:
            gw.count("engineer.pending_not_auto_confirmed", refused)
        if not approve:
            break
        gw.count("engineer.confirm_rounds")
        payload = dict(base)
        payload["confirmed_tools"] = sorted({p["tool"] for p in approve})
        payload["confirmed_call_ids"] = [p["call_id"] for p in approve]
        sample, result = await gw.chat_stream(
            vu, scenario="engineer", op="confirm_stream", payload=payload,
        )
        samples.append(sample)
    return samples


async def scenario_voice(gw: Gateway, vu: VU) -> list[Sample]:
    """E: speech in, turn, speech out -- the three v3 voice-note calls the client makes."""
    wav = gw.voice_wav
    stt, stt_result = await gw.request(
        vu, scenario="voice", op="transcribe", kind="speech", method="POST",
        path="/api/v3/voice/transcribe", files={"file": ("question.wav", wav, "audio/wav")},
        timeout_s=gw.cfg.request_timeout_s,
    )
    samples = [stt]
    if not stt.ok:
        return samples
    transcript = str((stt_result.body or {}).get("text") or "").strip()
    if len(transcript.split()) < 3:
        gw.count("voice.transcript_fallbacks")
        transcript = VOICE_FALLBACK_PROMPT
    turn, result = await gw.chat_stream(
        vu, scenario="voice", op="chat_stream",
        payload=chat_payload(gw.cfg, vu, transcript, session_id=f"{vu.session_id}-voice"),
    )
    samples.append(turn)
    if not turn.ok:
        return samples
    answer = result.text.strip()[:400] or "Here is your answer."
    tts, _ = await gw.request(
        vu, scenario="voice", op="speak", kind="speech", method="POST",
        path="/api/v3/voice/speak", json_body={"text": answer},
        timeout_s=gw.cfg.request_timeout_s,
    )
    samples.append(tts)
    return samples


async def scenario_api(gw: Gateway, vu: VU) -> list[Sample]:
    """Non-LLM API traffic the client generates between turns -- what the API thresholds gate."""
    choice = vu.rng.randrange(4)
    if choice == 0:
        sample, _ = await gw.request(
            vu, scenario="api", op="list_documents", kind="api", method="GET",
            path="/api/v3/knowledge/documents", params={"limit": 20},
        )
    elif choice == 1:
        sample, _ = await gw.request(
            vu, scenario="api", op="search", kind="api", method="POST",
            path="/api/v3/knowledge/search",
            json_body={"query": vu.rng.choice(SHORT_PROMPTS), "limit": 6},
        )
    elif choice == 2:
        sample, _ = await gw.request(
            vu, scenario="api", op="list_memory", kind="api", method="GET", path="/api/v3/memory",
        )
    else:
        sample, _ = await gw.request(
            vu, scenario="api", op="list_agents", kind="api", method="GET", path="/api/v3/agents",
        )
    return [sample]


SCENARIO_FUNCS: Mapping[str, ScenarioFn] = {
    "chat": scenario_chat,
    "long_context": scenario_long_context,
    "agent": scenario_agent,
    "engineer": scenario_engineer,
    "voice": scenario_voice,
    "api": scenario_api,
}


# -- preflight --------------------------------------------------------------------------


@dataclass
class Preflight:
    """What the harness learned about the target before loading it."""

    health: Mapping[str, Any] | None = None
    health_status: int | None = None
    agents: list[Mapping[str, Any]] = field(default_factory=list)
    workspaces_enabled: bool | None = None
    probe_provider: str | None = None
    probe_model: str | None = None
    probe_error: str | None = None
    available: dict[str, tuple[bool, str]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        health = dict(self.health or {})
        return {
            "health_status": self.health_status,
            "environment": health.get("environment"),
            "providers": {
                name: bool((info or {}).get("healthy"))
                for name, info in (health.get("providers") or {}).items()
            },
            "voice": health.get("voice"),
            "agents_usable": {a.get("id"): a.get("usable") for a in self.agents},
            "workspaces_enabled": self.workspaces_enabled,
            "probe_provider": self.probe_provider,
            "probe_model": self.probe_model,
            "probe_error": self.probe_error,
            "scenarios": {k: {"available": v[0], "reason": v[1]} for k, v in self.available.items()},
        }


def scenario_availability(name: str, cfg: RunConfig, pre: Preflight) -> tuple[bool, str]:
    """Whether a scenario can run against this target, and if not, exactly why."""
    if name == "engineer":
        if not cfg.engineer_repo:
            return False, "no --engineer-repo given"
        engineer = next((a for a in pre.agents if a.get("id") == "engineer"), None)
        if engineer is None:
            return False, "target lists no `engineer` persona"
        if not engineer.get("usable"):
            return False, (
                "`engineer` persona not usable by the load-test identity (its role must be in "
                "the target's AURA_ENGINEER_AGENT_ROLES; mint with --mint-role Developer)"
            )
        if not pre.workspaces_enabled:
            return False, "target has no sandbox/workspaces (AURA_SANDBOX_ENABLED is off)"
        return True, f"repo {cfg.engineer_repo}"
    if name == "voice":
        voice = (pre.health or {}).get("voice")
        if not isinstance(voice, Mapping):
            return False, "target's /health/ready does not report voice engines"
        missing = [k for k in ("stt", "tts") if not voice.get(k)]
        if missing:
            return False, f"target has no {' / '.join(missing).upper()} engine configured"
        return True, f"stt={voice.get('stt')}, tts={voice.get('tts')}"
    return True, ""


async def setup_user_documents(gw: Gateway, vu: VU) -> list[Sample]:
    """Unmeasured setup: give one identity the documents the RAG scenarios retrieve from.

    Idempotent per identity (fixed ``document_id`` values): re-running a matrix does not grow
    the corpus, and knowledge is owner-scoped so every identity needs its own copy.
    """
    samples = []
    for doc_id, title, content in (
        (HANDBOOK_ID, "Load test handbook", HANDBOOK),
        (CORPUS_ID, "Load test policy compendium", corpus_document()),
    ):
        sample, _ = await gw.request(
            vu, scenario="setup", op="ingest", kind="api", method="POST",
            path="/api/v3/knowledge/documents",
            json_body={"title": title, "content": content, "document_id": doc_id},
            timeout_s=max(gw.cfg.api_timeout_s, 120.0),
        )
        samples.append(sample)
    return samples
