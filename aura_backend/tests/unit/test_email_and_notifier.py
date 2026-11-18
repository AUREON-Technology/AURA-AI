"""SaaS P1-F1: the EmailSender port, its adapters, and the Notifier that renders customer email."""

from __future__ import annotations

from dataclasses import fields, replace
from datetime import datetime, timezone

import pytest

from aura_core.adapters.email import InMemoryEmailSender, SmtpEmailSender
from aura_core.adapters.runtime import InMemoryAuditSink
from aura_core.config.settings import AuraSettings, EmailSettings
from aura_core.domain.errors import ConfigurationError
from aura_core.notifications import Notifier
from aura_core.ports.email import EmailMessage

AT = datetime(2026, 11, 18, 9, tzinfo=timezone.utc)
SETTINGS = EmailSettings(enabled=True, from_address="AURA <billing@aureon-ai.tech>", smtp_host="smtp.example",
                         app_base_url="https://app.example")


@pytest.fixture
def audit(clock):
    return InMemoryAuditSink(clock=clock)


def _notifier(sender, audit, clock, settings=SETTINGS) -> Notifier:
    return Notifier(sender=sender, settings=settings, audit=audit, clock=clock)


async def _audit_text(audit) -> str:
    return repr([(e.action, e.detail) for e in await audit.query(limit=100)])


async def test_invite_email_carries_the_link_and_no_token_in_the_audit(audit, clock) -> None:
    sender = InMemoryEmailSender()
    await _notifier(sender, audit, clock).invite("bea@example.com", "Acme", "own@example.com", "member", "T0K")
    [message] = sender.outbox
    assert message.to == "bea@example.com" and "Acme" in message.subject and message.tag == "invite"
    assert "https://app.example/invite?token=T0K" in message.text
    assert "T0K" not in await _audit_text(audit)


async def test_receipt_formats_egp(audit, clock) -> None:
    sender = InMemoryEmailSender()
    await _notifier(sender, audit, clock).receipt("own@example.com", "Personal", "pro", 15000, AT, AT, "pay-1")
    assert "EGP 150.00" in sender.outbox[0].text and sender.outbox[0].tag == "receipt"


async def test_renewal_and_past_due_messages(audit, clock) -> None:
    sender = InMemoryEmailSender()
    notifier = _notifier(sender, audit, clock)
    await notifier.renewal_due("own@example.com", "Acme", 36000, "912345678", AT)
    await notifier.past_due("own@example.com", "Acme", AT)
    assert "912345678" in sender.outbox[0].text and "EGP 360.00" in sender.outbox[0].text
    assert [m.tag for m in sender.outbox] == ["renewal_due", "past_due"]


async def test_sender_failure_is_swallowed_and_audited(audit, clock) -> None:
    class Broken:
        async def send(self, message):
            raise OSError("smtp down")

    await _notifier(Broken(), audit, clock).invite("bea@example.com", "Acme", None, "member", "T0K")
    events = await audit.query(limit=10)
    assert any(e.action == "email.failed" and e.detail.get("tag") == "invite" for e in events)
    assert "T0K" not in await _audit_text(audit)


@pytest.mark.parametrize("to,name", [("a@b.c\r\nBcc: x@y.z", "Acme"), ("not-an-address", "Acme")])
async def test_bad_recipient_refused(audit, clock, to, name) -> None:
    sender = InMemoryEmailSender()
    await _notifier(sender, audit, clock).invite(to, name, None, "member", "T0K")
    assert sender.outbox == []
    assert any(e.action == "email.failed" for e in await audit.query(limit=10))


async def test_subject_is_one_line_whatever_the_workspace_name(audit, clock) -> None:
    sender = InMemoryEmailSender()
    await _notifier(sender, audit, clock).invite("bea@example.com", "Acme\r\nBcc: x@y.z", None, "member", "T")
    assert "\n" not in sender.outbox[0].subject and "\r" not in sender.outbox[0].subject


async def test_smtp_sender_builds_a_starttls_message(monkeypatch) -> None:
    calls = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self, context=None):
            calls.append(("starttls",))

        def login(self, user, password):
            calls.append(("login", user, password))

        def send_message(self, msg):
            calls.append(("send", msg["From"], msg["To"], msg["Subject"], msg.get_content()))

    sender = SmtpEmailSender("smtp.example", 587, "user", "pw", "AURA <billing@aureon-ai.tech>",
                             smtp_factory=FakeSMTP)
    await sender.send(EmailMessage(to="bea@example.com", subject="Hello", text="Body", tag="t"))
    assert calls[0] == ("connect", "smtp.example", 587) and ("starttls",) in calls
    assert ("login", "user", "pw") in calls
    send = next(c for c in calls if c[0] == "send")
    assert send[1:4] == ("AURA <billing@aureon-ai.tech>", "bea@example.com", "Hello") and "Body" in send[4]


def test_email_on_without_host_or_from_is_refused_at_startup() -> None:
    settings = replace(AuraSettings.from_env(), email=EmailSettings(enabled=True))
    with pytest.raises(ConfigurationError) as exc:
        settings.validate()
    assert any("AURA_SMTP_HOST" in p or "AURA_EMAIL_FROM" in p for p in exc.value.details["problems"])


def test_smtp_password_is_a_property_not_a_field(monkeypatch) -> None:
    assert "smtp_password" not in {f.name for f in fields(EmailSettings)}
    monkeypatch.setenv("AURA_SMTP_PASSWORD", "s3cret")
    assert EmailSettings().smtp_password == "s3cret"
    assert "s3cret" not in repr(AuraSettings.from_env().as_dict())


def test_container_has_no_notifier_when_email_is_off(container) -> None:
    assert container.notifier is None
