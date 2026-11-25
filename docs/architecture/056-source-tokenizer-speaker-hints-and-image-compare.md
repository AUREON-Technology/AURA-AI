# ADR-056 / 057 / 058: source tokenizer and complexity, speaker turn hints, mobile image summary and image compare

Status: Proposed (built, tested locally, not proven on a device or in CI)

## ADR-056: tokenizer-based blanking and cyclomatic complexity

`scan_source` used regexes that were fooled by comments and strings. `_blank` in
`tools/open_pure_logic.py` is now a small tokenizer (strings, comments, per language), and the
scan reports a McCabe `complexity` per function. Java/C# methods declared without a modifier are
found by a second pattern that excludes control-flow keywords and requires a `{` (Java) or `{`/`=>` (C#);
an abstract/interface method without a modifier (ends in `;`) is still not listed in Java.

## ADR-057: speaker turn hints

`transcribe_audio` does not identify speakers. `speaker_turn_hints()` (`tools/media.py`) marks
`turn_break_before` on a segment after a long pause and lists them under `diarization.turn_hints`.
These are pause-based hints, not speaker identities; the result says so.

## ADR-058: image summary for mobile and `compare_images`

- `summarize_for_mobile` takes an optional `image_summary` string. It never opens an image: the
  model gets the text from `analyze_image_requirements` / `ocr_image` and passes it in.
- `compare_images(before, after, threshold)` compares two same-size workspace images pixel by
  pixel (Pillow, imported lazily; paths confined to the workspace) and returns identical/ratio,
  the changed bounding box and a 4x4 grid of where it changed. It does not capture screenshots
  (that needs a browser or the desktop daemon) and does not say *what* changed.
- `tests/architecture/test_superseded_open_tools_unused.py` banned any `gpu_` tool name; it now
  bans GPU allocation/scheduling names only, so the read-only `gpu_status` (ADR-055) is allowed.
