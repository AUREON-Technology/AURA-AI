# ADR-040 — Lint and format checks, web search, and structured document, image and audio readers

**Status:** Proposed (built, not proven) · **Date:** 2026-11-23 · **Related:** ADR-006 (trust
channels, confirmation), ADR-011/013/014 (the Engineer's sandbox and `workspace_local`), ADR-030
(self-hosted inference only), ADR-031 (image understanding), ADR-036 (open analysis tools) ·
**Inventory rows:** 16, 56, 64, 74, 75, 76, 79

## Context

The tools inventory marked seven capabilities PARTIAL. In every case something existed but stopped
short of being a tool an agent could rely on:

| Row | What existed | What was missing |
|---|---|---|
| 16 lint / format / typecheck | `run_command` could run `ruff` | Output was a screen of text; "tool not installed" looked like "no findings" |
| 56 web research | `fetch_url` | No way to *find* a page; the Engineer could not even fetch |
| 76 image OCR | A structured OCR port and Tesseract adapter | No tool; nothing returned confidences or boxes to an agent |
| 75 spreadsheet reader | `read_file` printed workbook cells | No schema or statistics |
| 74 document parser | `read_file` flattened PDF/DOCX/PPTX to text | No structure, tables, page references or coverage |
| 64 multimodal analyzer | `VisionEngine` described images in prose | No structured requirements from a screenshot or mockup |
| 79 audio/video transcriber | A timestamped transcript port and adapter | Only the plain-text route; no segments over HTTP, no tool |

## Decision

Seven additions, none of which changes an existing behaviour. Everything is opt-in on a setting or
a dependency that is already in the deployment, and every tool is registered **only when what it
needs is present**, so the model is never offered a capability that cannot work.

### 1. `lint_code` and `check_format` (`tools/devtools.py`) -- row 16

Engineer tools built inside `build_sandbox_tools`, so they share the `_SessionAdapter` and the
`SandboxRunner` that `run_command` uses: a paired desktop first, the server sandbox otherwise. Argv
is assembled from validated parts (no shell string; a path that starts with `-` or escapes the
workspace is refused).

* `lint_code`: `ruff check --output-format json`, `mypy --show-column-numbers --no-error-summary`,
  `pyflakes`, `eslint -f json`. `linter=auto` runs ruff (pyflakes if ruff is absent) for Python and
  eslint when the paths name JS/TS files. Each finding is `{linter, file, line, column, end_line,
  end_column, code, rule_name, severity, message, fixable}`; the report adds counts, `truncated` and
  `withheld`.
* `check_format`: `ruff format --check` and `prettier --check`; one record per file that would change
  or could not be parsed.
* **A linter that is not installed, timed out, crashed or printed something unparseable is
  reported with that status and never as clean.** `clean` / `formatted` is true only when every
  requested tool actually ran and found nothing. (ESLint 9+ with no config exits non-zero without
  JSON: that is `failed`, not `ok`.)
* Neither tool writes (no `--fix`, no `--write`; a test pins it), which is why both are declared
  `workspace_local` exactly as `run_command` is. The pin in
  `tests/architecture/test_workspace_local_tools.py` was extended by name: the effect is a program
  started in the workspace, nothing `run_command` could not already do.

### 2. `web_search` (`tools/websearch.py`) -- row 56

Search is provided the way everything here is: by something the operator runs. The only backend is a
**SearXNG** instance named by `AURA_WEB_SEARCH_URL` (empty = the tool is not registered). A hosted
search API would be a vendor dependency the self-hosting invariant forbids.

* The request goes to `<AURA_WEB_SEARCH_URL>/search` and nowhere else; the model's `query` is one
  URL-encoded parameter value and cannot change host, port, scheme, path or any other parameter.
  Redirects are never followed. The `fetch_url` private-address rule is deliberately **not** applied:
  that rule protects against *model-chosen* hosts, and an internal SearXNG (`http://searxng:8080`) is
  the expected deployment.
* Results are untrusted third-party text: tagged `untrusted_content`, every field length-capped and
  stripped of control characters, `javascript:` / `data:` / `file:` URLs dropped, duplicates removed.
* The body is streamed to at most 1 MB; an oversize body is an error, not a partial parse.
* The Engineer and the Research Agent hold `web_search` and `fetch_url`.

### 3. The file readers (`tools/media.py`) -- rows 74, 75, 76, 64, 79

All take a path under the same root as `read_file` (`AURA_TOOLS_FILES_ROOT`; one shared confinement
function, `tools/confinement.py`, which `read_file` now also uses) and all are tagged
`untrusted_content`. Results are JSON that is valid by construction: when one would exceed its budget
the tool degrades in stated steps and records them under `trimmed`.

* **`parse_document`** (74) -- PDF, DOCX, PPTX to `outline` (a tree; from DOCX heading styles, PPTX
  slide titles and PDF bookmarks -- *headings are never guessed from PDF layout*), `blocks`
  (`heading`/`paragraph`/`table`/`notes`/`unreadable_page`, each with `ref`: page, slide or section),
  `images` (page/slide, and whether the page's text came from OCR) and `coverage` (pages read,
  unreadable, blank, OCR'd, `complete`). PDF tables come from a new `find_text_tables` in
  `knowledge/documents.py` that finds table *regions* inside a page and returns their cells; the
  conservative whole-page `_looks_tabular` is unchanged, so ingestion is byte-identical. Output is
  windowed by block index; a long table is split by rows and a long paragraph by characters, so a
  window never cuts anything silently and `continue` names the next offset.
* **`inspect_spreadsheet`** (75) -- CSV/TSV/XLSX to per-sheet column types, counts, numeric
  statistics, date ranges, top values and sample rows. The type inference, header detection and date
  normalisation moved from the files connector into `knowledge/tabular.py` and both now import it, so
  a column has the same type whichever door it came in by (pinned by a test).
* **`ocr_image`** (76) -- text, mean confidence, per-line text/confidence/box, word boxes (capped),
  low-confidence words flagged, per-call `lang`. The container injects the OCR engine; `tools/`
  imports no adapter. Refuses non-images (PDFs are pointed at `parse_document`) and images over 30
  megapixels with the reason. An image with no text has `mean_confidence: null`, not 0.
* **`analyze_image_requirements`** (64) -- screens, regions, components, texts and open questions as
  validated JSON (`vision/requirements.py`). With a vision model: a strict-JSON prompt, validation,
  **one repair turn that quotes the exact schema errors**, then a fall back to the deterministic path
  with a warning. Without one: structured-OCR rules (wording and position decide button / input /
  link / heading / checkbox / navigation; regions come from the image bands). `source` is `vlm` or
  `ocr_heuristic`, and the heuristic's open questions say it is a guess from text.
  `VisionEngine.analyze` is the new general entry point (it keeps the SENSITIVE classification and the
  "no silent text-only answer" rule).
* **`transcribe_audio`** (79) -- segments with start/end/text/confidence, optional word times,
  language, duration; windowed by segment. Video is accepted **only if `ffmpeg` is on `PATH`**
  (audio extracted to 16 kHz mono, capped at 13 minutes and said to be cut); otherwise a clear error.
  `POST /api/v3/voice/transcribe` gained `detail=segments` (and `words`) returning the same
  structure; the default response is unchanged, and an engine that cannot report timestamps answers
  503 rather than returning an untimed transcript as if it were timed.

**Speaker diarization is not done.** faster-whisper cannot say who is speaking; every segment's
`speaker` is `null` and the responses say `diarization.available: false`. Filling it needs a
separate diarization model merged by timestamp. This is why row 79 stays PARTIAL.

### Who sees what

`engineer`: all of the above plus `lint_code`/`check_format`. `research_agent`: `parse_document`,
`inspect_spreadsheet`, `ocr_image`, `transcribe_audio`, `web_search`. The orchestrator sees whatever
is registered. `coding_agent` and the personal-data personas get none of the `untrusted_content`
readers: `tests/unit/test_tools_review_engine.py` already pins that those personas hold no tool whose
output taints the turn, and a coding persona that can also write memory should not read files that
anyone may have authored. The names are literals in `agents/registry.py`; `tests/unit/test_media_tools.py`
and `tests/unit/test_agents.py` pin them to the registered tools.

## Consequences

* New settings: `AURA_WEB_SEARCH_URL` (+ `_TIMEOUT_S`, `_ENGINES`, `_LANGUAGE`). Nothing else:
  the readers ride on `AURA_TOOLS_FILES_ROOT` and the existing OCR/vision/speech settings. Documented
  in `aura_backend/README.md` and `deploy/.env.example`, and passed through both compose files.
* Planner prompt: with a files root configured the reading tools add roughly 5 descriptions to the
  tool list the Engineer's planner renders. Not measured on the CPU model; `TestPlannerPromptBudget`
  in `tests/unit/test_analysis_tools.py` still holds because the default configuration registers none.
* `ocr_image`/`analyze_image_requirements` cost CPU seconds on the gateway for large images
  (bounded by a 120 s timeout and the 30 MP cap).

## What is *not* proven

"Built, not proven." What has been run: the unit and real-substrate tests listed in the report
(real ruff, mypy, eslint, prettier, tesseract 5.3 with Arabic data, ffmpeg, Pillow-rendered images,
byte-built PDF/DOCX/PPTX/XLSX), and each tool called through `Container.tools.execute`. What has not:

* `web_search` against a real SearXNG (the HTTP boundary is `httpx.MockTransport`).
* `transcribe_audio` / `detail=segments` against real faster-whisper (not installable in the build
  environment; the model's result shape is faked and the real mapping runs over it).
* `lint_code` / `check_format` on a **paired desktop** or in the Docker sandbox: only the in-memory
  runner (real subprocesses in a temp directory) was exercised. Whether the sandbox image carries
  ruff/mypy/eslint is a deployment question the tool answers per call with `not_installed`.
* The vision-model path of `analyze_image_requirements` against a real VLM: the router and
  `VisionEngine` are real, the model is the scripted echo provider. At 5-10 tok/s a 900-token JSON
  reply is ~100 s against the 120 s `perception_timeout_s`; the GPU Space is far faster.
* Speaker diarization (above).
