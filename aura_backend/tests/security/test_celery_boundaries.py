"""Celery task authorisation boundaries: a task's arguments are data from the broker, and a
crafted set of them must not let the task act on another user's data or on files it was never
meant to touch.

Inventory of ``tasks.py`` tasks that take caller-derived arguments:

* ``ingest_pdf_task(file_id, file_name, local_path, user_id)`` -- reads ``local_path``, ingests
  it under ``user_id``, then **deletes** ``local_path``. Covered below.
* ``build_lora_dataset_task(user_id)`` -- ``user_id`` is only logged; the dataset is the global
  curated one and the only caller (``api/learning.py``) is ``require_role("Admin")``.
* ``finetune_job_task(job_id)`` -- ``FineTuneJobs.run`` refuses any job that is not ``QUEUED``
  and re-checks its approval against the dataset digest
  (``tests/unit/test_finetune_loop_b.py::test_cannot_launch_without_approval``).
* the beat tasks take no arguments.

``ingest_pdf_task`` checked only that ``local_path`` was lexically under ``data_dir``
(``abspath``, which does not resolve symlinks), so crafted arguments could: ingest a file outside
the volume through a symlink; ingest another user's staged PDF under your own user id; and --
because the task deletes whatever it was given -- remove any file in the shared data volume.
Staged files now live in a per-user directory, and the task accepts only a regular file directly
inside the directory of the ``user_id`` it was given, resolved through symlinks.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import tasks
from services import drive as drive_mod


@pytest.fixture
def volume(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(tasks.settings, "data_dir", str(data))
    monkeypatch.setattr(drive_mod.settings, "data_dir", str(data))
    return data


@pytest.fixture
def ingested(monkeypatch):
    seen: list[dict] = []

    def fake_ingest(*, doc_id, pdf_bytes, metadata):
        seen.append({"doc_id": doc_id, "bytes": pdf_bytes, "user": metadata["user_id"]})
        return True

    # The legacy vector ingestion is the downstream boundary; what is under test is what the
    # task agrees to read, and on whose behalf.
    monkeypatch.setattr(tasks.rag_service, "ingest_pdf", fake_ingest)
    return seen


def _stage(monkeypatch, user_id: str, content: bytes = b"%PDF-1.4 alice") -> Path:
    monkeypatch.setattr(drive_mod, "download_drive_file", lambda *a, **k: content)
    return Path(drive_mod.stage_drive_pdf("tok", "file1", user_id=user_id))


class TestIngestPdfTask:
    def test_the_normal_path_still_works(self, volume, ingested, monkeypatch) -> None:
        staged = _stage(monkeypatch, "alice")
        assert tasks.ingest_pdf_task.run("file1", "a.pdf", str(staged), "alice") is True
        assert ingested == [{"doc_id": "file1", "bytes": b"%PDF-1.4 alice", "user": "alice"}]
        assert not staged.exists()

    def test_another_users_staged_file_cannot_be_claimed(
        self, volume, ingested, monkeypatch
    ) -> None:
        alice_file = _stage(monkeypatch, "alice")
        assert tasks.ingest_pdf_task.run("file1", "a.pdf", str(alice_file), "bob") is False
        assert ingested == [], "bob's task ingested alice's staged document"
        assert alice_file.exists(), "bob's task deleted alice's staged document"

    def test_a_symlink_out_of_the_volume_is_not_followed(
        self, volume, ingested, monkeypatch, tmp_path
    ) -> None:
        secret = tmp_path / "outside-secret.pdf"
        secret.write_bytes(b"%PDF credentials")
        staging = Path(drive_mod.staging_dir("alice"))
        staging.mkdir(parents=True)
        link = staging / "innocent.pdf"
        link.symlink_to(secret)

        assert tasks.ingest_pdf_task.run("file1", "a.pdf", str(link), "alice") is False
        assert ingested == []
        assert secret.exists()

    @pytest.mark.parametrize("victim", ["train_lora.json", "models/registry.json", "aura.key"])
    def test_a_crafted_path_cannot_delete_other_files_in_the_volume(
        self, volume, ingested, victim: str
    ) -> None:
        target = volume / victim
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"%PDF-important")

        assert tasks.ingest_pdf_task.run("file1", "a.pdf", str(target), "alice") is False
        assert target.exists(), f"crafted task arguments deleted {victim}"
        assert ingested == []

    def test_a_dotdot_path_is_judged_after_resolution(self, volume, ingested, monkeypatch) -> None:
        alice_file = _stage(monkeypatch, "alice")
        bob_dir = Path(drive_mod.staging_dir("bob"))
        bob_dir.mkdir(parents=True, exist_ok=True)
        crafted = os.path.join(str(bob_dir), "..", "alice", alice_file.name)

        assert tasks.ingest_pdf_task.run("file1", "a.pdf", crafted, "bob") is False
        assert alice_file.exists() and ingested == []

    @pytest.mark.parametrize("user_id", ["../alice", "alice/../bob", "", "global/.."])
    def test_a_user_id_cannot_steer_the_staging_directory(self, volume, user_id: str) -> None:
        staging = Path(drive_mod.staging_dir(user_id)).resolve()
        root = (volume / "drive_ingest").resolve()
        assert staging.parent == root
