"""Fixtures shared by every module under ``tests/unit``."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _open_tools_start_without_ocr(request: pytest.FixtureRequest):
    """The ``tools/open`` PDF reader holds one process-wide OCR engine, which ``Container`` installs.

    A test that built a container (with tesseract on PATH) would otherwise switch OCR on for every
    open-tool test that runs after it, and "a scanned page is reported as unread" would then depend
    on test order. Each open-tool test therefore starts, and ends, with no engine configured.
    """
    if not request.node.fspath.basename.startswith("test_open_"):
        yield
        return
    from aura_core.tools.open._input import configure_ocr_engine

    configure_ocr_engine(None)
    yield
    configure_ocr_engine(None)
