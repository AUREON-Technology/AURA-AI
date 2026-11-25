"""Voice to a structured task (ADR-043, roadmap row 88).

Two layers: the deterministic extractor (English and Arabic cues; pure, so it is tested directly on
many phrasings) and ``POST /api/v3/voice/task`` over the real app, with the real in-memory STT and
the scripted echo provider standing in for the model refinement.
"""

from __future__ import annotations

import json

import pytest

from aura_core.adapters.in_memory_speech import InMemoryStt
from aura_core.domain.identifiers import UserId
from aura_core.llm.providers.echo import ScriptedTurn
from aura_core.voice.task_extractor import extract_task, task_goal_text

AUDIO = ("note.wav", b"RIFF-not-really-a-wav", "audio/wav")


def _fields(text: str, **kw) -> dict:
    return extract_task(text, **kw).as_dict()


def _asks_about(task: dict) -> set[str]:
    return {q["field"] for q in task["questions"]}


def _has_arabic(text: str) -> bool:
    return any("؀" <= ch <= "ۿ" for ch in text)


class TestEnglish:
    def test_a_complete_spoken_request_is_split_into_its_parts(self) -> None:
        t = _fields("hey aura, can you fix the login bug in acme/web and open a PR, no rush, "
                    "and don't touch the payments code")
        assert t["goal"] == "fix the login bug in acme/web and open a PR"
        assert t["repository"] == "acme/web"
        assert t["deliverable"] == "pull_request"
        assert t["priority"] == "low"
        assert t["constraints"] == ["don't touch the payments code"]
        assert t["ambiguities"] == [] and t["questions"] == [] and t["needs_clarification"] is False

    def test_a_github_url_is_the_repository(self) -> None:
        t = _fields("Refactor the invoice exporter in https://github.com/acme/billing.git as a pull "
                    "request. Make sure the tests pass. It's urgent.")
        assert t["repository"] == "acme/billing" and t["deliverable"] == "pull_request"
        assert t["priority"] == "high" and t["constraints"] == ["Make sure the tests pass"]
        assert not t["needs_clarification"]

    def test_constraints_after_a_pause(self) -> None:
        t = _fields("I need you to prepare a presentation about Q3 results, keep it under ten slides, "
                    "whenever you get a chance")
        assert t["goal"] == "prepare a presentation about Q3 results"
        assert t["constraints"] == ["keep it under ten slides"]
        assert t["deliverable"] == "presentation" and t["priority"] == "low"

    @pytest.mark.parametrize("phrase,expected", [
        ("do this asap", "high"), ("it's urgent", "high"), ("whenever", "low"), ("no rush", "low"),
        ("normal priority", "normal"), ("right now", "high"),
    ])
    def test_priority_cues(self, phrase: str, expected: str) -> None:
        assert _fields(f"write a summary of the release notes, {phrase}")["priority"] == expected

    def test_no_priority_cue_means_none_not_normal(self) -> None:
        assert _fields("write a report about last month's sales")["priority"] is None

    def test_a_question_implies_an_answer_and_says_it_assumed_so(self) -> None:
        t = _fields("what is the status of the deployment")
        assert t["deliverable"] == "answer" and not t["needs_clarification"]
        assert t["assumptions"] and "question" in t["assumptions"][0]

    def test_summarising_implies_a_summary(self) -> None:
        t = _fields("Please summarize the meeting notes from yesterday")
        assert t["deliverable"] == "summary" and t["goal"] == "summarize the meeting notes from yesterday"
        assert not t["needs_clarification"]


class TestItAsksInsteadOfGuessing:
    def test_nothing_said(self) -> None:
        for silent in ("", "   ", "um uh"):
            t = _fields(silent)
            assert t["needs_clarification"] and _asks_about(t) == {"goal"}
            assert t["goal"] == "" and t["repository"] is None

    def test_a_goal_that_is_only_a_pronoun(self) -> None:
        t = _fields("fix it")
        assert t["needs_clarification"]
        assert {"goal", "repository", "deliverable"} <= _asks_about(t)
        assert t["repository"] is None and t["deliverable"] is None

    def test_a_code_task_without_a_repository(self) -> None:
        t = _fields("fix the login bug and open a pull request")
        assert _asks_about(t) == {"repository"} and t["repository"] is None
        question = next(q for q in t["questions"] if q["field"] == "repository")
        assert "owner/name" in question["question"]

    def test_a_repository_named_without_its_owner_is_not_guessed(self) -> None:
        t = _fields("fix the login bug in the web repo and open a pull request")
        assert t["repository"] is None and t["repository_hint"] == "web"
        assert _asks_about(t) == {"repository"}
        assert "“web”" in t["questions"][0]["question"]

    def test_an_unnamed_repository(self) -> None:
        t = _fields("update the repo")
        reasons = {(a["field"], a["reason"]) for a in t["ambiguities"]}
        assert ("repository", "unspecified") in reasons

    def test_two_repositories_are_offered_as_options(self) -> None:
        t = _fields("compare acme/web and acme/api and tell me which one has the older dependencies")
        assert t["repository"] is None
        [question] = [q for q in t["questions"] if q["field"] == "repository"]
        assert question["options"] == ["acme/web", "acme/api"]

    def test_a_work_request_with_no_deliverable(self) -> None:
        t = _fields("write something")
        assert "deliverable" in _asks_about(t)
        t = _fields("refactor the invoice exporter in acme/billing")
        assert t["repository"] == "acme/billing" and _asks_about(t) == {"deliverable"}
        assert t["questions"][0]["options"]

    def test_alternatives_are_not_resolved_for_the_person(self) -> None:
        t = _fields("either fix the bug or rewrite the module in acme/web, deliver a patch")
        assert ("scope", "alternatives") in {(a["field"], a["reason"]) for a in t["ambiguities"]}
        assert t["needs_clarification"]

    def test_a_non_code_request_never_asks_for_a_repository(self) -> None:
        t = _fields("write a report about last month's sales")
        assert t["repository"] is None and "repository" not in _asks_about(t)

    @pytest.mark.parametrize("not_a_repo", ["call him and/or her", "open 24/7", "yes/no answer", "on 12/05"])
    def test_ordinary_slashes_are_not_repositories(self, not_a_repo: str) -> None:
        assert _fields(f"fix the bug, {not_a_repo}")["repository"] is None

    def test_extraction_is_deterministic(self) -> None:
        text = "fix the login bug in acme/web and open a PR, no rush"
        assert extract_task(text) == extract_task(text)


class TestArabic:
    def test_egyptian_arabic_request(self) -> None:
        t = _fields("أصلح باج تسجيل الدخول في ريبو web وافتح بول ريكوست ضروري بس بدون ما تلمس كود الدفع")
        assert t["language"] == "ar"
        assert t["deliverable"] == "pull_request" and t["priority"] == "high"
        assert t["constraints"] == ["بدون ما تلمس كود الدفع"]
        # The repository was named without an owner: asked, in Arabic, not guessed.
        assert t["repository"] is None and t["repository_hint"] == "web"
        [question] = t["questions"]
        assert question["field"] == "repository" and "owner/name" in question["question"]
        assert _has_arabic(question["question"])

    def test_complete_arabic_request(self) -> None:
        t = _fields("نفذ الميزة الجديدة في acme/mobile وافتح بول ريكوست")
        assert t["repository"] == "acme/mobile" and t["deliverable"] == "pull_request"
        assert not t["needs_clarification"]

    def test_a_noun_request_is_its_own_deliverable_and_low_priority(self) -> None:
        t = _fields("عايز تقرير عن مبيعات الشهر الماضي مش مستعجل")
        assert t["goal"] == "تقرير عن مبيعات الشهر الماضي"
        assert t["deliverable"] == "report" and t["priority"] == "low" and not t["needs_clarification"]

    def test_summarise_and_a_vague_reference(self) -> None:
        t = _fields("ممكن تلخص لي التقرير ده")
        assert t["deliverable"] == "summary"
        assert ("goal", "reference") in {(a["field"], a["reason"]) for a in t["ambiguities"]}
        assert "ده" in t["questions"][0]["question"]

    def test_the_language_hint_wins_over_detection_for_the_questions(self) -> None:
        assert _fields("fix it", language="ar")["questions"][0]["question"].startswith("الطلب")


class TestGoalText:
    def test_every_part_survives_into_the_text_the_agent_receives(self) -> None:
        task = extract_task("fix the login bug in acme/web and open a PR, no rush, and don't touch "
                            "the payments code")
        text = task_goal_text(task)
        assert "fix the login bug" in text and "Repository: acme/web" in text
        assert "Deliverable: PR" in text and "Constraint: don't touch the payments code" in text


# -- the endpoint ---------------------------------------------------------------------------------


def _client(container, *, user: str = "talker"):
    from fastapi.testclient import TestClient

    from aura_core.api import Principal, build_app

    async def authenticator(request):
        return Principal(user_id=UserId(user), email="t@example.com", role="User")

    return TestClient(build_app(container, authenticator=authenticator))


ROUTE = "/api/v3/voice/task"


class TestEndpoint:
    def test_typed_text_gives_the_structured_task_and_starts_nothing(self, container) -> None:
        with _client(container) as client:
            response = client.post(ROUTE, data={
                "text": "fix the login bug in acme/web and open a PR, no rush", "refine": "false"})
            body = response.json()
            assert response.status_code == 200, response.text
            assert body["goal"] == "fix the login bug in acme/web and open a PR"
            assert body["repository"] == "acme/web" and body["deliverable"] == "pull_request"
            assert body["priority"] == "low" and body["needs_clarification"] is False
            assert body["ambiguities"] == [] and body["questions"] == [] and body["constraints"] == []
            assert body["source"] == "deterministic" and body["started"] is False
            assert body["refinement"] == {"requested": False, "status": "not_requested"}
            assert body["suggested_request"] == {
                "goal": "fix the login bug in acme/web and open a PR.\nRepository: acme/web\nDeliverable: PR",
                "priority": "low", "persona_hint": "engineer"}
            # Nothing was started: no task exists for this person.
            assert client.get("/api/v3/agents/tasks").json()["tasks"] == []

    def test_a_gap_is_returned_as_questions_and_no_suggested_request(self, container) -> None:
        with _client(container) as client:
            body = client.post(ROUTE, data={"text": "fix it", "refine": "false"}).json()
        assert body["needs_clarification"] is True and body["suggested_request"] is None
        assert {q["field"] for q in body["questions"]} >= {"goal", "repository", "deliverable"}

    def test_audio_is_transcribed_then_extracted(self, container) -> None:
        container.stt_engine = InMemoryStt(
            default="please summarize the meeting notes, urgent", detected_language="en")
        with _client(container) as client:
            body = client.post(ROUTE, files={"file": AUDIO}, data={"refine": "false"}).json()
        assert body["transcript"] == "please summarize the meeting notes, urgent"
        assert body["deliverable"] == "summary" and body["priority"] == "high"
        assert body["language"] == "en" and body["started"] is False

    def test_arabic_audio_is_questioned_in_arabic(self, container) -> None:
        container.stt_engine = InMemoryStt(default="أصلح الباج", detected_language="ar")
        with _client(container) as client:
            body = client.post(ROUTE, files={"file": AUDIO}, data={"language": "ar", "refine": "false"}).json()
        assert body["language"] == "ar" and body["needs_clarification"] is True
        assert all(_has_arabic(q["question"]) for q in body["questions"])

    def test_exactly_one_of_audio_or_text(self, container) -> None:
        with _client(container) as client:
            assert client.post(ROUTE, data={}).status_code == 422
            assert client.post(ROUTE, data={"text": "  "}).status_code == 422
            both = client.post(ROUTE, files={"file": AUDIO}, data={"text": "hi"})
            assert both.status_code == 422 and "not both" in both.json()["message"]
            assert client.post(ROUTE, data={"text": "x", "language": "klingon"}).status_code == 422

    def test_audio_without_an_stt_engine_is_a_clear_503_but_text_still_works(self, container) -> None:
        container.stt_engine = None
        with _client(container) as client:
            assert client.post(ROUTE, files={"file": AUDIO}).status_code == 503
            assert client.post(ROUTE, data={"text": "summarize the notes", "refine": "false"}
                               ).status_code == 200

    def test_requires_authentication(self, container) -> None:
        from fastapi.testclient import TestClient

        from aura_core.api import build_app
        from aura_core.domain.errors import AuthenticationError

        async def refusing(request):
            raise AuthenticationError("no")

        with TestClient(build_app(container, authenticator=refusing)) as client:
            assert client.post(ROUTE, data={"text": "x"}).status_code == 401

    def test_the_existing_transcribe_route_still_answers_as_before(self, container) -> None:
        container.stt_engine = InMemoryStt(default="مرحبا", detected_language="ar")
        with _client(container) as client:
            response = client.post("/api/v3/voice/transcribe", files={"file": AUDIO}, data={"language": "ar"})
        assert response.status_code == 200
        assert response.json() == {"text": "مرحبا", "engine": "in-memory-stt", "language": "ar"}


class TestModelRefinement:
    TEXT = "can you look after the Q3 invoices thing for me, and keep it short, by Friday"

    def _post(self, container, text=None, **data):
        with _client(container) as client:
            return client.post(ROUTE, data={"text": text or self.TEXT, **data}).json()

    def test_a_valid_refinement_is_applied_and_labelled(self, container) -> None:
        container.router.get("echo").queue(ScriptedTurn(text=json.dumps({
            "goal": "Review the Q3 invoices", "constraints": ["keep it short", "by Friday"],
            "repository": None, "deliverable": "a short report", "priority": "normal",
            "ambiguities": []})))
        body = self._post(container, "please review the Q3 invoices and write me a short report, "
                                     "keep it short, by Friday")
        assert body["refinement"]["status"] == "applied" and body["source"] == "llm"
        assert body["goal"] == "Review the Q3 invoices"
        assert body["constraints"] == ["keep it short", "by Friday"]
        assert body["deliverable"] == "report" and body["deliverable_text"] == "a short report"
        assert body["priority"] == "normal" and body["needs_clarification"] is False

    def test_invalid_json_is_repaired_once(self, container) -> None:
        echo = container.router.get("echo")
        echo.queue(
            ScriptedTurn(text="Sure! Here is your task: review the invoices"),
            ScriptedTurn(text=json.dumps({"goal": "Review the invoices", "constraints": [],
                                          "repository": None, "deliverable": "report",
                                          "priority": None, "ambiguities": []})),
        )
        body = self._post(container, "review the invoices and write a report", refine="true")
        assert body["refinement"]["status"] == "applied" and body["source"] == "llm"
        assert echo.call_count == 2
        # The second call told the model what was wrong with the first.
        assert "not valid" in echo.calls[1].conversation.last_user_message.content

    def test_still_invalid_after_the_repair_falls_back_to_the_deterministic_result(self, container) -> None:
        echo = container.router.get("echo")
        echo.queue(ScriptedTurn(text="no json here"), ScriptedTurn(text='{"goal": 7}'))
        body = self._post(container, "write a report about last month's sales")
        assert body["refinement"]["status"] == "invalid_output" and body["source"] == "deterministic"
        assert body["goal"] == "write a report about last month's sales"
        assert body["deliverable"] == "report" and echo.call_count == 2

    def test_a_repository_the_person_never_said_is_dropped(self, container) -> None:
        container.router.get("echo").queue(ScriptedTurn(text=json.dumps({
            "goal": "Fix the login bug", "constraints": [], "repository": "evil/corp",
            "deliverable": "pull request", "priority": None, "ambiguities": []})))
        body = self._post(container, "fix the login bug and open a pull request")
        assert body["repository"] is None, "a model-invented repository was believed"
        assert body["needs_clarification"] is True
        assert {q["field"] for q in body["questions"]} == {"repository"}

    def test_the_model_cannot_claim_the_task_is_clear(self, container) -> None:
        """Clarification is recomputed from the merged fields; whatever the model says about
        ambiguity is not what decides it."""
        container.router.get("echo").queue(ScriptedTurn(text=json.dumps({
            "goal": "fix it", "constraints": [], "repository": None, "deliverable": None,
            "priority": None, "ambiguities": []})))
        body = self._post(container, "fix it")
        assert body["needs_clarification"] is True and body["source"] == "llm"

    def test_an_invented_constraint_is_discarded(self, container) -> None:
        container.router.get("echo").queue(ScriptedTurn(text=json.dumps({
            "goal": "Write the quarterly report", "constraints": ["delete the production database"],
            "repository": None, "deliverable": "a report", "priority": None, "ambiguities": []})))
        body = self._post(container, "write the quarterly report as a report")
        assert "delete the production database" not in " ".join(body["constraints"])

    def test_no_eligible_model_leaves_the_deterministic_result(self, container) -> None:
        async def failing(request):
            raise RuntimeError("no provider")

        container.router.complete = failing
        body = self._post(container, "summarize the meeting notes")
        assert body["refinement"]["status"] == "unavailable" and body["source"] == "deterministic"
        assert body["deliverable"] == "summary"

    def test_the_refinement_switch_turns_it_off(self, monkeypatch) -> None:
        from aura_core.config import load_settings
        from aura_core.container import Container

        monkeypatch.setenv("AURA_VOICE_TASK_REFINE", "false")
        container = Container(load_settings(refresh=True, validate=False))
        monkeypatch.delenv("AURA_VOICE_TASK_REFINE")
        container.router.get("echo").queue(ScriptedTurn(text="{}"))
        body = self._post(container, "summarize the meeting notes", refine="true")
        assert body["refinement"] == {"requested": True, "status": "disabled"}
        assert container.router.get("echo").call_count == 0
        load_settings(refresh=True, validate=False)

    def test_the_transcript_is_sent_to_the_model_as_unclassified_sensitive_data(self, container) -> None:
        """An unclassified generation counts as sensitive (llm/sensitivity.py), so a hosted provider
        never receives a transcript; only a provider cleared for it does -- here the local echo."""
        echo = container.router.get("echo")
        echo.queue(ScriptedTurn(text=json.dumps({
            "goal": "Summarize the notes", "constraints": [], "repository": None,
            "deliverable": "a summary", "priority": None, "ambiguities": []})))
        self._post(container, "summarize the notes into a summary")
        assert echo.call_count == 1
