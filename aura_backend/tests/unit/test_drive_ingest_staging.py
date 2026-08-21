"""Drive PDF ingest must not put a Google token on the Celery broker (AURA-004)."""

from __future__ import annotations

from pathlib import Path

import tasks
from services import drive as drive_mod


class _FakeTask:
    id = "task-1"


def test_delay_receives_a_workspace_path_not_the_token(tmp_path, monkeypatch) -> None:
    token = "ya29.secret-google-oauth-token"
    monkeypatch.setattr(drive_mod.settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(drive_mod, "download_drive_file", lambda *a, **k: b"%PDF-1.4 fake")

    captured: dict = {}

    def fake_delay(*args):
        captured["args"] = args
        return _FakeTask()

    monkeypatch.setattr(tasks.ingest_pdf_task, "delay", fake_delay)

    result = drive_mod.queue_drive_pdf_ingest("file1", "doc.pdf", token, "user-1")
    assert result["status"] == "queued"
    args = captured["args"]
    assert token not in args
    assert all(token not in str(value) for value in args)
    assert args[0] == "file1"
    assert args[1] == "doc.pdf"
    assert args[3] == "user-1"
    local_path = Path(args[2])
    assert local_path.exists()
    assert tmp_path in local_path.parents
    assert local_path.read_bytes() == b"%PDF-1.4 fake"


def test_ingest_task_reads_the_path_and_deletes_it(tmp_path, monkeypatch) -> None:
    staged = tmp_path / "drive_ingest"
    staged.mkdir()
    pdf = staged / "doc.pdf"
    pdf.write_bytes(b"%PDF-bytes")
    monkeypatch.setattr(tasks.settings, "data_dir", str(tmp_path))

    ingested: dict = {}

    def fake_ingest(*, doc_id, pdf_bytes, metadata):
        ingested["doc_id"] = doc_id
        ingested["bytes"] = pdf_bytes
        ingested["metadata"] = metadata
        return True

    monkeypatch.setattr(tasks.rag_service, "ingest_pdf", fake_ingest)

    assert tasks.ingest_pdf_task.run("file1", "doc.pdf", str(pdf), "user-1") is True
    assert ingested["bytes"] == b"%PDF-bytes"
    assert ingested["metadata"]["user_id"] == "user-1"
    assert not pdf.exists()


def test_ingest_task_refuses_a_path_outside_the_workspace(tmp_path, monkeypatch) -> None:
    outsider = tmp_path / "outside.pdf"
    outsider.write_bytes(b"%PDF")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(tasks.settings, "data_dir", str(workspace))
    monkeypatch.setattr(
        tasks.rag_service, "ingest_pdf", lambda **k: (_ for _ in ()).throw(AssertionError("must not ingest"))
    )

    assert tasks.ingest_pdf_task.run("file1", "doc.pdf", str(outsider), "user-1") is False
    assert outsider.exists()
