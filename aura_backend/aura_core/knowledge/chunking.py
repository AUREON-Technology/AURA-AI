"""Structure-aware chunking.

The legacy chunker slices at raw character offsets (``services/rag.py:276``): 1000 characters
with 100 of overlap, cutting mid-sentence and mid-word, discarding all document structure. A
chunk that begins "…days of annual leave, provided that" is a poor retrieval unit and a worse
piece of context.

This chunker respects the hierarchy that exists in real documents:

1. Split on Markdown/underline headings, keeping the heading path with each section -- so a
   retrieved chunk can cite "Leave > Annual" rather than "chunk 7".
2. Within a section, split on paragraphs, then on sentences.
3. Accumulate to a target token count, never crossing a heading boundary.
4. Overlap by whole sentences rather than characters, so context carries over intact.

An oversized single sentence (a minified line, a table row, a base64 blob) is hard-split as a
last resort, and marked so the failure is visible rather than silent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator, Sequence

__all__ = ["ChunkingConfig", "chunk_text", "split_sentences", "TextPiece"]


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    target_tokens: int = 320
    max_tokens: int = 512
    min_tokens: int = 48
    overlap_sentences: int = 1

    def __post_init__(self) -> None:
        if self.target_tokens > self.max_tokens:
            raise ValueError("target_tokens cannot exceed max_tokens")
        if self.overlap_sentences < 0:
            raise ValueError("overlap_sentences cannot be negative")


@dataclass(frozen=True, slots=True)
class TextPiece:
    """One chunk: content plus the heading path it came from."""

    content: str
    heading_path: tuple[str, ...] = ()
    hard_split: bool = False


_ATX_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")
_SETEXT_UNDERLINE = re.compile(r"^(=+|-{2,})\s*$")

# Candidate sentence boundary: terminal punctuation, optional closers, whitespace, then an
# opening character. Abbreviations are excluded in Python rather than with a look-behind --
# `re` only supports fixed-width look-behind, and the abbreviation list is not fixed width.
# Arabic has no capitals, so a boundary followed by an Arabic letter (or an opening guillemet)
# counts too, and the Arabic question mark and full stop end a sentence like their Latin forms.
_SENTENCE_BOUNDARY = re.compile(
    r"([.!?\u061F\u06D4][\"')\]\u00BB]*)\s+(?=[A-Z0-9\u00C0-\u024F\u0621-\u064A\u0660-\u0669\u00AB])"
)
_ABBREVIATIONS = frozenset(
    """Mr Mrs Ms Dr Prof Sr Jr St vs etc cf Fig No Inc Ltd Co Corp approx al Ave Rd
    Jan Feb Mar Apr Jun Jul Aug Sep Sept Oct Nov Dec""".split()
)
_TRAILING_WORD = re.compile(r"([A-Za-z]+)\.$")


def _ends_with_abbreviation(fragment: str) -> bool:
    """True when a candidate boundary is really an abbreviation's full stop.

    Also treats a single trailing initial ("J.") as an abbreviation, which is what keeps
    "J. Smith reported..." from splitting after the initial.
    """
    match = _TRAILING_WORD.search(fragment.rstrip())
    if match is None:
        return False
    word = match.group(1)
    return word in _ABBREVIATIONS or len(word) == 1


def _estimate_tokens(text: str) -> int:
    """Local estimate so chunking has no dependency on the tokenizer module.

    Deliberately the same cheap ratio used in the domain layer: chunk sizing only needs to be
    approximately right, and coupling the ingestion pipeline to a tokenizer would mean changing
    the chat model could change chunk boundaries.
    """
    return max(1, (len(text) + 3) // 4)


def _split_on_boundaries(text: str) -> list[str]:
    """Split one line into sentences, re-joining false boundaries after abbreviations."""
    parts: list[str] = []
    cursor = 0
    for match in _SENTENCE_BOUNDARY.finditer(text):
        end = match.end(1)
        candidate = text[cursor:end]
        if _ends_with_abbreviation(candidate):
            continue
        stripped = candidate.strip()
        if stripped:
            parts.append(stripped)
        cursor = match.end()
    remainder = text[cursor:].strip()
    if remainder:
        parts.append(remainder)
    return parts


def split_sentences(text: str) -> list[str]:
    """Split into sentences, tolerating the shapes real documents contain.

    Lines that are clearly not prose -- list items, table rows, code -- are kept whole. Applying
    sentence splitting to a Markdown table produces fragments that retrieve badly and read worse.
    """
    if not text.strip():
        return []

    pieces: list[str] = []
    for block in text.split("\n"):
        stripped = block.strip()
        if not stripped:
            continue
        # List items, table rows, and code-ish lines are atomic.
        if re.match(r"^([-*+]|\d+[.)]|\|)", stripped) or stripped.startswith(("    ", "\t")):
            pieces.append(stripped)
            continue
        parts = _split_on_boundaries(stripped)
        pieces.extend(parts or [stripped])
    return pieces


def _iter_sections(text: str) -> Iterator[tuple[tuple[str, ...], str]]:
    """Yield ``(heading_path, body)`` for each section.

    Handles both ATX (``## Heading``) and setext (underlined) forms. A document with no headings
    yields a single section with an empty path, which keeps the caller free of special cases.
    """
    lines = text.splitlines()
    path: list[str] = []
    body: list[str] = []
    index = 0

    def flush() -> Iterator[tuple[tuple[str, ...], str]]:
        content = "\n".join(body).strip()
        if content:
            yield (tuple(path), content)

    while index < len(lines):
        line = lines[index]
        atx = _ATX_HEADING.match(line.strip())
        setext = (
            index + 1 < len(lines)
            and line.strip()
            and _SETEXT_UNDERLINE.match(lines[index + 1].strip())
        )

        if atx or setext:
            yield from flush()
            body = []
            if atx:
                level, title = len(atx.group(1)), atx.group(2).strip()
                index += 1
            else:
                title = line.strip()
                level = 1 if lines[index + 1].strip().startswith("=") else 2
                index += 2
            # Truncate the path to this heading's depth, then append -- this is what produces a
            # correct breadcrumb for nested sections.
            path = path[: level - 1] + [title]
            continue

        body.append(line)
        index += 1

    yield from flush()


def _hard_split(sentence: str, max_tokens: int) -> list[str]:
    """Last-resort split for a single oversized unit.

    Splits on whitespace where possible so words stay intact; falls back to characters only for
    text with no whitespace at all (minified JSON, a base64 blob).
    """
    max_chars = max_tokens * 4
    if " " not in sentence:
        return [sentence[i : i + max_chars] for i in range(0, len(sentence), max_chars)]

    parts: list[str] = []
    current: list[str] = []
    length = 0
    for word in sentence.split(" "):
        if length + len(word) + 1 > max_chars and current:
            parts.append(" ".join(current))
            current, length = [], 0
        current.append(word)
        length += len(word) + 1
    if current:
        parts.append(" ".join(current))
    return parts


def chunk_text(text: str, config: ChunkingConfig | None = None) -> list[TextPiece]:
    """Chunk a document, preserving structure and heading provenance."""
    cfg = config or ChunkingConfig()
    pieces: list[TextPiece] = []

    for heading_path, body in _iter_sections(text):
        sentences = split_sentences(body)
        if not sentences:
            continue

        buffer: list[str] = []
        buffer_tokens = 0

        def flush(force: bool = False) -> None:
            nonlocal buffer, buffer_tokens
            if not buffer:
                return
            content = " ".join(buffer).strip()
            # A stub below min_tokens is merged into the previous chunk of the same section
            # rather than emitted: a 10-token chunk retrieves noisily and wastes an index slot.
            if not force and buffer_tokens < cfg.min_tokens and pieces and pieces[-1].heading_path == heading_path:
                previous = pieces[-1]
                pieces[-1] = TextPiece(
                    content=f"{previous.content} {content}".strip(),
                    heading_path=heading_path,
                    hard_split=previous.hard_split,
                )
            elif content:
                pieces.append(TextPiece(content=content, heading_path=heading_path))
            buffer, buffer_tokens = [], 0

        for sentence in sentences:
            sentence_tokens = _estimate_tokens(sentence)

            if sentence_tokens > cfg.max_tokens:
                flush(force=True)
                for fragment in _hard_split(sentence, cfg.max_tokens):
                    pieces.append(
                        TextPiece(content=fragment, heading_path=heading_path, hard_split=True)
                    )
                continue

            if buffer_tokens + sentence_tokens > cfg.target_tokens and buffer:
                overlap = buffer[-cfg.overlap_sentences :] if cfg.overlap_sentences else []
                flush()
                # Overlap by whole sentences so the carried context is readable, unlike a
                # character-offset overlap that starts mid-word.
                buffer = list(overlap)
                buffer_tokens = sum(_estimate_tokens(s) for s in buffer)

            buffer.append(sentence)
            buffer_tokens += sentence_tokens

        flush(force=True)

    return pieces


def chunk_with_headings(text: str, config: ChunkingConfig | None = None) -> list[tuple[str, tuple[str, ...]]]:
    """Adapter to the ``(content, heading_path)`` pairs
    :func:`aura_core.domain.knowledge.build_chunks` expects."""
    return [(piece.content, piece.heading_path) for piece in chunk_text(text, config)]


def estimate_chunk_count(text: str, config: ChunkingConfig | None = None) -> int:
    """Cheap pre-flight estimate, used to reject an ingestion that would exceed a quota before
    doing any embedding work."""
    cfg = config or ChunkingConfig()
    return max(1, _estimate_tokens(text) // max(1, cfg.target_tokens))


def merge_short_pieces(pieces: Sequence[TextPiece], min_tokens: int = 48) -> list[TextPiece]:
    """Second pass that merges runt chunks across section boundaries.

    Used when a document's headings are so dense that per-section chunking leaves many stubs
    (a glossary, an FAQ). Kept separate because it discards the one-section-per-chunk property,
    which is usually worth keeping.
    """
    merged: list[TextPiece] = []
    for piece in pieces:
        if merged and _estimate_tokens(piece.content) < min_tokens:
            previous = merged[-1]
            merged[-1] = TextPiece(
                content=f"{previous.content}\n\n{piece.content}",
                heading_path=previous.heading_path,
                hard_split=previous.hard_split or piece.hard_split,
            )
        else:
            merged.append(piece)
    return merged
