"""The reply follows the user's language (docs/plans/arabic-language_plan.md, stage 1; owner
decisions 2, 3 and 5).

The app reports the language it is showing in ``X-Client-Language``. A chat reply mirrors the
language the message is written in, and only a message with no clear language of its own (a link,
code, a number) falls back to the app's language. Arabic replies are Egyptian unless the user
writes فصحى. Everything here is additive: no header, and every turn behaves as it did before.
"""

from __future__ import annotations

import pytest

from aura_core.api import Principal, build_app
from aura_core.domain import UserId
from aura_core.prompts.language import reply_language
from aura_core.prompts.library import BUILTIN_PROMPTS
from aura_core.text.language import normalize_language
from services.user_language import remember_language, resolve_language

_TURN = {"plan": False, "use_tools": False, "use_memory": False, "use_knowledge": False}


class TestNormalizeLanguage:
    @pytest.mark.parametrize(("tag", "expected"), [
        ("ar", "ar"), ("AR", "ar"), ("ar-EG", "ar"), ("ar_EG", "ar"), (" en-US ", "en"),
        ("en", "en"), ("zh-Hant-TW", None), ("fr", None),
    ])
    def test_supported_tags_reduce_to_their_language(self, tag: str, expected: str | None) -> None:
        assert normalize_language(tag) == expected

    @pytest.mark.parametrize("value", [None, "", "   ", "a", "arabic", "../etc", "ar;q=0.9",
                                       "ar-" + "x" * 40, 3])
    def test_anything_else_is_refused(self, value: object) -> None:
        assert normalize_language(value) is None


class _User:
    language: str | None = None


class TestRememberLanguage:
    def test_a_supported_language_is_stored_and_a_bad_one_never_overwrites_it(self) -> None:
        user = _User()
        assert remember_language(user, "ar-EG") is True and user.language == "ar"
        for junk in (None, "fr", "../etc", 7):
            assert remember_language(user, junk) is False
            assert user.language == "ar"
        assert remember_language(user, "ar") is False
        assert remember_language(user, "en") is True and user.language == "en"

    def test_a_user_who_never_reported_one_reads_as_english(self) -> None:
        assert resolve_language(None) == "en"
        assert resolve_language("garbage") == "en"
        assert resolve_language("ar") == "ar"


class TestReplyLanguage:
    @pytest.mark.parametrize(("message", "preferred", "expected"), [
        ("ازيك عامل ايه النهارده؟", "en", "ar"),      # Arabic message in an English app
        ("what is on my calendar today?", "ar", "en"),  # English message in an Arabic app
        ("https://example.com/report.pdf", "ar", "ar"),  # a link alone: the app's language
        ("`git status`", "ar", "ar"),                   # code alone
        ("12500", "en", "en"),
        ("12500", None, None),                          # nothing decides: left to the model
        ("https://example.com", "fr", None),            # an unsupported preference is ignored
        ("شوف الرابط ده https://example.com/en/docs", "en", "ar"),
    ])
    def test_the_message_decides_and_the_app_language_breaks_ties(
        self, message: str, preferred: str | None, expected: str | None
    ) -> None:
        assert reply_language(message, preferred) == expected



_ARABIC_LINE = "Reply in Egyptian Arabic, or Modern Standard Arabic if the user writes or asks for it."


class TestPersonaPrompts:
    def _render(self, template, **extra):
        values = {v.name: "x" for v in template.variables if v.required}
        values["fence_rules"] = ""
        return template.render(values | extra).text

    def test_every_persona_can_be_told_to_reply_in_arabic(self) -> None:
        personas = [t for t in BUILTIN_PROMPTS if t.id.startswith("system.")]
        assert len(personas) >= 9
        for template in personas:
            assert "arabic_reply" in {v.name for v in template.variables}, template.id
            assert _ARABIC_LINE in " ".join(self._render(template, arabic_reply="true").split()), template.id

    def test_an_english_reply_adds_nothing_to_the_prompt(self) -> None:
        """The CPU tier's prompt budget is 400 tokens; English turns must not pay for Arabic."""
        for template in (t for t in BUILTIN_PROMPTS if t.id.startswith("system.")):
            plain = self._render(template)
            assert "Arabic" not in plain, template.id
            assert self._render(template, arabic_reply="") == plain


@pytest.fixture
def client(container):
    from fastapi.testclient import TestClient

    async def authenticate(request) -> Principal:
        return Principal(user_id=UserId("lang-user"), email="lang@example.com")

    with TestClient(build_app(container, authenticator=authenticate)) as test_client:
        yield test_client


@pytest.fixture
def echo(container):
    provider = container.router.get("echo")
    provider.reset()
    return provider


def _told_arabic(echo) -> bool:
    return _ARABIC_LINE in " ".join(echo.last_system_prompt().split())


class TestChatTurns:
    def test_an_arabic_message_gets_an_arabic_reply_instruction(self, client, echo) -> None:
        response = client.post("/api/v3/chat", json={**_TURN, "message": "ازيك؟ عامل ايه النهارده"})
        assert response.status_code == 200, response.text
        assert _told_arabic(echo)

    def test_the_app_language_decides_only_for_a_message_without_one(self, client, echo) -> None:
        headers = {"X-Client-Language": "ar-EG"}
        client.post("/api/v3/chat", json={**_TURN, "message": "https://example.com/q3.pdf"},
                    headers=headers)
        assert _told_arabic(echo)

        client.post("/api/v3/chat", json={**_TURN, "message": "summarise my week"}, headers=headers)
        assert not _told_arabic(echo)

    def test_no_header_changes_nothing_for_an_english_turn(self, client, echo) -> None:
        """An older app sends no header; an English turn's prompt is what it always was."""
        response = client.post("/api/v3/chat", json={**_TURN, "message": "hello there"})
        assert response.status_code == 200
        assert "Arabic" not in echo.last_system_prompt()

    def test_a_junk_header_is_ignored_not_rejected(self, client, echo) -> None:
        response = client.post("/api/v3/chat", json={**_TURN, "message": "12500"},
                               headers={"X-Client-Language": "../../etc/passwd"})
        assert response.status_code == 200
        assert not _told_arabic(echo)
