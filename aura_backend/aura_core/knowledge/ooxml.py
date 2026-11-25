"""Hostile-input-safe reading of Office Open XML packages (DOCX, PPTX, XLSX), standard library only.

This platform pins no Office parser (``python-docx``, ``openpyxl``, ... are not in the hash-locked
requirements), and an uploaded or connector-fetched Office file is attacker-controlled bytes. So
what lives here is the part that has to be right whatever the file contains: a ZIP reader with
hard budgets, and an XML reader that cannot be made to expand or fetch anything.

**ZIP.** A package is opened in memory only (nothing is ever extracted to disk, so zip-slip does not
apply). Before any member is read the central directory is checked against :class:`OoxmlLimits`:
member count, each member's declared size, and the declared total. Every read is then bounded by the
*actual* decompressed byte count too, because a directory entry can lie about its size. A package
over a limit is **refused** with :class:`OoxmlLimitError` -- never read partway and returned as if it
were whole. Encrypted members, duplicate member names, a corrupt archive, and the CFB container
Word/Excel use for password-protected files are each refused with a specific message.

**XML.** Parsed with ``pyexpat`` directly, not ``xml.etree``, because that is the only way to hook
the parser itself. A document containing ``<!DOCTYPE`` or ``<!ENTITY`` is refused outright (the
billion-laughs and external-entity vectors both need one; Office never writes one), checked twice:
a byte scan before parsing, and expat's own declaration handlers during it, which also catches a
declaration smuggled in under an exotic declared encoding. External entities and parameter entities
are never resolved. Element depth and element count are capped, and XML that is not UTF-8/ASCII
(UTF-16 would hide a DOCTYPE from the byte scan) is refused.

Parsing is either a lean tree (:meth:`Package.tree`, for documents) or a callback stream
(:meth:`Package.stream`, for spreadsheets and shared strings, where building a node per cell would
cost more memory than the sheet is worth). :func:`read_xlsx` is the spreadsheet reader built on
those; DOCX and PPTX text lives in :mod:`aura_core.knowledge.office`.
"""

from __future__ import annotations

import datetime as dt
import io
import posixpath
import re
import zipfile
import zlib
from dataclasses import dataclass
from typing import Any, Callable
from xml.parsers import expat

from ..domain.errors import KnowledgeError

__all__ = [
    "OoxmlError",
    "OoxmlLimitError",
    "OoxmlLimits",
    "Node",
    "Package",
    "Sheet",
    "read_xlsx",
    "DEFAULT_LIMITS",
    "MAX_XLSX_COLUMNS",
]

_MIB = 1024 * 1024


class OoxmlError(KnowledgeError):
    """An Office file could not be read: corrupt, encrypted, not the format it claims, or hostile.

    A :class:`KnowledgeError`, so every caller that records an unreadable document per document
    (the connector import, the legacy ingest) records this one the same way. 422 rather than the
    base class's 500: the fault is in the upload, not in this service.
    """

    kind = "document_unreadable"
    http_status = 422


class OoxmlLimitError(OoxmlError):
    """A package is within the upload byte limit but over a decompression, structure or size budget."""

    kind = "document_too_large"
    http_status = 413


@dataclass(frozen=True, slots=True)
class OoxmlLimits:
    """Every budget a package is held to. Defaults are generous for real documents and fatal to bombs."""

    #: Entries in the ZIP's central directory (a 1000-slide deck has about 5,000).
    max_members: int = 10_000
    #: Largest single member, declared and actual. Real XML parts are far smaller; media is already
    #: compressed, so it cannot be larger than the upload limit.
    max_member_bytes: int = 64 * _MIB
    #: Sum of every member's declared size, and of the bytes actually decompressed.
    max_total_bytes: int = 256 * _MIB
    #: Element nesting; real documents stay under about 40 even with nested tables.
    max_xml_depth: int = 128
    #: Elements in one part parsed as a stream (a sheet).
    max_xml_elements: int = 3_000_000
    #: Elements in one part parsed as a tree. Lower, because a tree pays memory per element.
    max_tree_elements: int = 1_000_000


DEFAULT_LIMITS = OoxmlLimits()

#: Excel's own column ceiling (``XFD``). A cell reference beyond it is corruption, and honouring it
#: would allocate a row that wide.
MAX_XLSX_COLUMNS = 16_384

#: The magic of an OLE2 / compound file: what Office writes for password-protected OOXML, and what
#: the legacy ``.doc``/``.xls``/``.ppt`` formats are.
_CFB_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_DECLARATION = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)", re.IGNORECASE)


# -- XML ---------------------------------------------------------------------------------------


class _Stop(Exception):
    """Raised from a stream callback to end parsing early on purpose (not an error)."""


def _refuse_declaration(*_args: Any) -> None:
    raise OoxmlError(
        "the document contains an XML DOCTYPE or ENTITY declaration, which Office files never "
        "do; it is refused as a possible entity-expansion attack"
    )


def _check_xml_bytes(data: bytes, part: str) -> None:
    head = data[:4]
    if head[:2] in (b"\xff\xfe", b"\xfe\xff") or b"\x00" in head:
        raise OoxmlError(f"{part} is not UTF-8 XML (UTF-16 content is refused)", details={"part": part})
    if _DECLARATION.search(data):
        _refuse_declaration()


class Node:
    """One element of a parsed part. ``attrs`` keys keep their namespace as ``"uri local"``."""

    __slots__ = ("tag", "attrs", "children", "text")

    def __init__(self, tag: str, attrs: dict[str, str]) -> None:
        self.tag = tag
        self.attrs = attrs
        self.children: list[Node] = []
        self.text = ""

    def attr(self, local: str, default: str | None = None) -> str | None:
        """An attribute by local name, ignoring its namespace (``w:val`` and ``val`` alike)."""
        value = self.attrs.get(local)
        if value is not None:
            return value
        suffix = " " + local
        for key, found in self.attrs.items():
            if key.endswith(suffix):
                return found
        return default

    def rel_id(self) -> str | None:
        """The ``r:id`` / ``r:embed`` style relationship reference, whichever namespace spelling."""
        for key, value in self.attrs.items():
            if "relationships" in key and key.rpartition(" ")[2] in ("id", "embed", "link"):
                return value
        return None

    def child(self, tag: str) -> "Node | None":
        for node in self.children:
            if node.tag == tag:
                return node
        return None

    def iter_all(self, tag: str):
        """Every descendant named ``tag``, in document order."""
        stack = list(reversed(self.children))
        while stack:
            node = stack.pop()
            if node.tag == tag:
                yield node
            stack.extend(reversed(node.children))


class _Names(dict):
    """Memoised ``"uri local"`` -> ``"local"``: parsing calls this once per element."""

    def __missing__(self, key: str) -> str:
        value = self[key] = key.rpartition(" ")[2]
        return value


def _drive(
    data: bytes,
    part: str,
    limits: OoxmlLimits,
    max_elements: int,
    on_start: Callable[[str, dict[str, str]], None],
    on_end: Callable[[str], None],
    on_text: Callable[[str], None] | None,
) -> bool:
    """Run expat over ``data`` with every guard on. Returns True when a callback stopped it early."""
    _check_xml_bytes(data, part)
    parser = expat.ParserCreate(namespace_separator=" ")
    parser.buffer_text = True
    local = _Names()
    depth = 0
    count = 0

    def start(name: str, attrs: dict[str, str]) -> None:
        nonlocal depth, count
        depth += 1
        count += 1
        if depth > limits.max_xml_depth:
            raise OoxmlLimitError(
                f"{part} nests elements more than {limits.max_xml_depth} deep",
                details={"part": part, "limit_depth": limits.max_xml_depth},
            )
        if count > max_elements:
            raise OoxmlLimitError(
                f"{part} has more than {max_elements} elements",
                details={"part": part, "limit_elements": max_elements},
            )
        on_start(local[name], attrs)

    def end(name: str) -> None:
        nonlocal depth
        depth -= 1
        on_end(local[name])

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    if on_text is not None:
        parser.CharacterDataHandler = on_text
    parser.StartDoctypeDeclHandler = _refuse_declaration
    parser.EntityDeclHandler = _refuse_declaration
    parser.UnparsedEntityDeclHandler = _refuse_declaration
    parser.NotationDeclHandler = _refuse_declaration
    parser.ExternalEntityRefHandler = _refuse_declaration
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)

    view = memoryview(data)
    try:
        for offset in range(0, len(view), 65_536):
            parser.Parse(bytes(view[offset : offset + 65_536]), False)
        parser.Parse(b"", True)
    except _Stop:
        return True
    except expat.ExpatError as exc:
        raise OoxmlError(f"{part} is not well-formed XML", details={"part": part}) from exc
    return False


# -- the package ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Relationship:
    id: str
    type: str  # the last path segment of the relationship type URI, e.g. "worksheet"
    target: str  # resolved to a package part name


def _resolve(base_part: str, target: str) -> str:
    """A relationship target as a package part name; refuses one that climbs out of the package."""
    path = target.lstrip("/") if target.startswith("/") else posixpath.join(posixpath.dirname(base_part), target)
    path = posixpath.normpath(path)
    if path == ".." or path.startswith("../"):
        raise OoxmlError("a relationship points outside the package", details={"target": target[:200]})
    return path


class Package:
    """An opened OOXML package: budgets enforced, parts read lazily, nothing written anywhere."""

    def __init__(self, data: bytes, *, limits: OoxmlLimits | None = None) -> None:
        self.limits = limits or DEFAULT_LIMITS
        if not data:
            raise OoxmlError("the file is empty")
        if data.startswith(_CFB_MAGIC):
            raise OoxmlError(
                "the file is a password-protected or legacy Office document, not an Open XML "
                "package; remove the password or save it as .docx/.pptx/.xlsx"
            )
        try:
            self._zip = zipfile.ZipFile(io.BytesIO(data))
            infos = self._zip.infolist()
        except (zipfile.BadZipFile, zipfile.LargeZipFile, ValueError, OSError, EOFError, zlib.error) as exc:
            raise OoxmlError("the file is not a valid Office Open XML (ZIP) package") from exc
        if len(infos) > self.limits.max_members:
            raise OoxmlLimitError(
                f"the package has {len(infos)} entries; at most {self.limits.max_members} are read",
                details={"members": len(infos), "limit_members": self.limits.max_members},
            )
        self._infos: dict[str, zipfile.ZipInfo] = {}
        declared = 0
        for info in infos:
            if info.is_dir():
                continue
            if info.flag_bits & 0x1:
                raise OoxmlError("the package is encrypted", details={"member": info.filename[:200]})
            key = info.filename.lower()
            if key in self._infos:
                raise OoxmlError(
                    "the package lists the same part twice", details={"member": info.filename[:200]}
                )
            if info.file_size > self.limits.max_member_bytes:
                raise OoxmlLimitError(
                    f"a part of the package expands to {info.file_size} bytes; the limit is "
                    f"{self.limits.max_member_bytes}",
                    details={"member": info.filename[:200], "limit_member_bytes": self.limits.max_member_bytes},
                )
            declared += info.file_size
            if declared > self.limits.max_total_bytes:
                raise OoxmlLimitError(
                    f"the package expands to more than {self.limits.max_total_bytes} bytes",
                    details={"limit_total_bytes": self.limits.max_total_bytes},
                )
            self._infos[key] = info
        self._read_total = 0

    # -- parts -----------------------------------------------------------------------------

    def has(self, name: str) -> bool:
        return name.lower() in self._infos

    def names(self) -> list[str]:
        return [info.filename for info in self._infos.values()]

    def read(self, name: str) -> bytes:
        """A part's bytes, never more than the per-member and total budgets allow."""
        info = self._infos.get(name.lower())
        if info is None:
            raise OoxmlError("the package is missing a required part", details={"part": name[:200]})
        cap = self.limits.max_member_bytes
        try:
            with self._zip.open(info) as handle:
                data = handle.read(cap + 1)
        except (zipfile.BadZipFile, zlib.error, EOFError, OSError, NotImplementedError, RuntimeError) as exc:
            raise OoxmlError("a part of the package is corrupt", details={"part": name[:200]}) from exc
        if len(data) > cap:
            raise OoxmlLimitError(
                f"{name} expands past {cap} bytes",
                details={"part": name[:200], "limit_member_bytes": cap},
            )
        self._read_total += len(data)
        if self._read_total > self.limits.max_total_bytes:
            raise OoxmlLimitError(
                f"the package expands past {self.limits.max_total_bytes} bytes while being read",
                details={"limit_total_bytes": self.limits.max_total_bytes},
            )
        return data

    def tree(self, name: str, *, leaf_tags: frozenset[str] = frozenset()) -> Node:
        """A part as a lean element tree. Elements named in ``leaf_tags`` keep their attributes but
        none of their children (run properties and the like, which are most of a Word file)."""
        data = self.read(name)
        root = Node("", {})
        stack = [root]
        skipping = 0

        def start(tag: str, attrs: dict[str, str]) -> None:
            nonlocal skipping
            if skipping:
                skipping += 1
                return
            node = Node(tag, attrs)
            stack[-1].children.append(node)
            stack.append(node)
            if tag in leaf_tags:
                skipping = 1

        def end(_tag: str) -> None:
            nonlocal skipping
            if skipping:
                skipping -= 1
                if skipping:
                    return
            stack.pop()

        def text(chunk: str) -> None:
            if not skipping and len(stack) > 1:
                stack[-1].text += chunk

        cap = min(self.limits.max_tree_elements, self.limits.max_xml_elements)
        _drive(data, name, self.limits, cap, start, end, text)
        if not root.children:
            raise OoxmlError(f"{name} has no content", details={"part": name[:200]})
        return root.children[0]

    def stream(
        self,
        name: str,
        on_start: Callable[[str, dict[str, str]], None],
        on_end: Callable[[str], None],
        on_text: Callable[[str], None] | None = None,
    ) -> bool:
        """Feed a part's events to callbacks. A callback may raise :class:`_Stop` to end early; the
        return value says whether it did."""
        return _drive(self.read(name), name, self.limits, self.limits.max_xml_elements, on_start, on_end, on_text)

    # -- relationships -----------------------------------------------------------------------

    def relationships(self, part: str) -> list[_Relationship]:
        """The internal relationships of ``part`` (``""`` for the package root), targets resolved."""
        directory, _, base = part.rpartition("/")
        rels_name = f"{directory + '/' if directory else ''}_rels/{base}.rels"
        if not self.has(rels_name):
            return []
        out: list[_Relationship] = []

        def start(tag: str, attrs: dict[str, str]) -> None:
            if tag != "Relationship" or attrs.get("TargetMode", "").lower() == "external":
                return
            rid, rtype, target = attrs.get("Id"), attrs.get("Type", ""), attrs.get("Target")
            if rid and target:
                out.append(_Relationship(rid, rtype.rpartition("/")[2], _resolve(part, target)))

        self.stream(rels_name, start, lambda _t: None)
        return out

    def main_part(self, rel_type: str, default: str) -> str:
        """The part the package root points at for ``rel_type`` (``officeDocument``), else ``default``."""
        for rel in self.relationships(""):
            if rel.type == rel_type and self.has(rel.target):
                return rel.target
        if self.has(default):
            return default
        raise OoxmlError("the package has no main document part", details={"expected": default})


# -- XLSX ----------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Sheet:
    """One worksheet: its non-empty rows as typed values, and whether the row cap cut it short.

    Cells are ``None`` (empty), ``bool``, ``int``, ``float``, ``str``, :class:`datetime.datetime`
    (a date/time-formatted number) or :class:`datetime.time` (a time-only format). Rows are as wide
    as their last non-empty cell; a gap in a row is ``None``.
    """

    name: str
    rows: tuple[tuple[Any, ...], ...]
    truncated: bool = False
    state: str = "visible"


#: Built-in number format ids Excel renders as dates/times (ECMA-376 18.8.30).
_BUILTIN_DATE_FORMATS = frozenset({14, 15, 16, 17, 22, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 50, 51, 52, 53,
                                   54, 55, 56, 57, 58})
_BUILTIN_TIME_FORMATS = frozenset({18, 19, 20, 21, 45, 46, 47})

_QUOTED = re.compile(r'"[^"]*"|\\.|_.|\*.')
_BRACKETS = re.compile(r"\[[^\]]*\]")
_ELAPSED = re.compile(r"\[(?:h+|m+|s+)\]", re.IGNORECASE)
_INTEGER = re.compile(r"^-?[0-9]{1,18}$")


def _format_kind(code: str) -> str | None:
    """``"date"``, ``"time"`` or ``None`` (a plain number) for a custom number-format code."""
    if _ELAPSED.search(code):
        return None  # an elapsed-duration format ([h]:mm) is a number of hours, not a clock time
    cleaned = _BRACKETS.sub("", _QUOTED.sub("", code))
    cleaned = re.sub(r"(?i)am/pm|a/p", "", cleaned)
    lowered = cleaned.lower()
    if "y" in lowered or "d" in lowered:
        return "date"
    if "h" in lowered or "s" in lowered:
        return "time"
    if "m" in lowered:
        return "date"  # a lone m is a month
    return None


def _excel_datetime(serial: float, date1904: bool) -> dt.datetime | None:
    """A serial day number as a datetime, or ``None`` when it is not a representable date."""
    if serial < 0 or serial >= 2_958_466:  # Excel's own range: up to 9999-12-31
        return None
    if date1904:
        epoch = dt.datetime(1904, 1, 1)
    elif serial < 61:
        # Excel treats 1900 as a leap year (it was not); its day 1 is 1900-01-01 and day 60 is the
        # nonexistent Feb 29. Days before 61 are therefore one day off from the 1899-12-30 epoch.
        if serial >= 60 and serial < 61:
            return None
        epoch = dt.datetime(1899, 12, 31)
    else:
        epoch = dt.datetime(1899, 12, 30)
    millis = round(serial * 86_400_000)
    try:
        return epoch + dt.timedelta(milliseconds=millis)
    except OverflowError:
        return None


def _column_index(ref: str) -> int:
    """``"B7"`` -> 1 (zero-based column), ``-1`` when the reference has no column letters."""
    index = 0
    seen = False
    for char in ref:
        if "A" <= char <= "Z":
            index = index * 26 + (ord(char) - 64)
            seen = True
        elif "a" <= char <= "z":
            index = index * 26 + (ord(char) - 96)
            seen = True
        else:
            break
    return index - 1 if seen else -1


def _shared_strings(package: Package, part: str) -> list[str]:
    strings: list[str] = []
    buf: list[str] = []
    in_si = False
    in_t = False
    phonetic = 0

    def start(tag: str, _attrs: dict[str, str]) -> None:
        nonlocal in_si, in_t, phonetic
        if tag == "si":
            in_si, buf[:] = True, []
        elif tag == "rPh":
            phonetic += 1
        elif tag == "t" and in_si and not phonetic:
            in_t = True

    def end(tag: str) -> None:
        nonlocal in_si, in_t, phonetic
        if tag == "si":
            strings.append("".join(buf))
            in_si = False
        elif tag == "rPh":
            phonetic = max(0, phonetic - 1)
        elif tag == "t":
            in_t = False

    def text(chunk: str) -> None:
        if in_t:
            buf.append(chunk)

    package.stream(part, start, end, text)
    return strings


def _cell_formats(package: Package, part: str) -> dict[int, str]:
    """Style index -> ``"date"``/``"time"`` for the styles whose number format is a date or time."""
    custom: dict[int, str] = {}
    xf_formats: list[int] = []
    in_numfmts = False
    in_cellxfs = False

    def start(tag: str, attrs: dict[str, str]) -> None:
        nonlocal in_numfmts, in_cellxfs
        if tag == "numFmts":
            in_numfmts = True
        elif tag == "cellXfs":
            in_cellxfs = True
        elif tag == "numFmt" and in_numfmts:
            try:
                custom[int(attrs.get("numFmtId", ""))] = attrs.get("formatCode", "")
            except ValueError:
                pass
        elif tag == "xf" and in_cellxfs:
            try:
                xf_formats.append(int(attrs.get("numFmtId", "0")))
            except ValueError:
                xf_formats.append(0)

    def end(tag: str) -> None:
        nonlocal in_numfmts, in_cellxfs
        if tag == "numFmts":
            in_numfmts = False
        elif tag == "cellXfs":
            in_cellxfs = False

    package.stream(part, start, end)
    kinds: dict[int, str] = {}
    for index, fmt in enumerate(xf_formats):
        kind: str | None = None
        if fmt in custom:
            kind = _format_kind(custom[fmt])
        elif fmt in _BUILTIN_DATE_FORMATS:
            kind = "date"
        elif fmt in _BUILTIN_TIME_FORMATS:
            kind = "time"
        if kind:
            kinds[index] = kind
    return kinds


def _number(text: str) -> Any:
    s = text.strip()
    if not s:
        return None
    if _INTEGER.match(s):
        return int(s)
    try:
        value = float(s)
    except ValueError:
        return text  # not a number after all: keep what the file said rather than dropping it
    return value if value == value and value not in (float("inf"), float("-inf")) else text


def _read_sheet(
    package: Package,
    part: str,
    shared: list[str],
    kinds: dict[int, str],
    date1904: bool,
    max_rows: int | None,
    max_cells: int,
) -> tuple[tuple[tuple[Any, ...], ...], bool]:
    rows: list[tuple[Any, ...]] = []
    truncated = False
    cells = 0
    row: dict[int, Any] = {}
    next_col = 0
    cell: dict[str, Any] | None = None
    value_buf: list[str] = []
    inline_buf: list[str] = []
    in_v = in_is = in_t = False
    phonetic = 0

    def finish_cell() -> None:
        nonlocal cell, next_col, cells
        assert cell is not None
        col, ctype, style = cell["col"], cell["t"], cell["s"]
        raw = "".join(value_buf)
        value: Any
        if ctype == "s":
            try:
                value = shared[int(raw)]
            except (ValueError, IndexError):
                value = None
        elif ctype == "inlineStr":
            value = "".join(inline_buf)
        elif ctype in ("str", "d"):
            value = raw
        elif ctype == "b":
            value = raw.strip() in ("1", "true")
        elif ctype == "e":
            value = None
        else:
            value = _number(raw)
            kind = kinds.get(style) if style is not None else None
            if kind and isinstance(value, (int, float)) and not isinstance(value, bool):
                moment = _excel_datetime(float(value), date1904)
                if moment is not None:
                    value = moment.time() if kind == "time" and 0 <= value < 1 else moment
        if value == "":
            value = None
        if value is not None:
            row[col] = value
            cells += 1
        next_col = col + 1
        cell = None
        value_buf.clear()
        inline_buf.clear()

    def start(tag: str, attrs: dict[str, str]) -> None:
        nonlocal row, next_col, cell, in_v, in_is, in_t, phonetic
        if tag == "row":
            row, next_col = {}, 0
        elif tag == "c":
            ref = attrs.get("r")
            col = _column_index(ref) if ref else next_col
            if col < 0:
                col = next_col
            if col >= MAX_XLSX_COLUMNS:
                raise OoxmlError("a cell lies beyond the last spreadsheet column", details={"part": part})
            style = attrs.get("s")
            cell = {"col": col, "t": attrs.get("t"), "s": int(style) if style and style.isdigit() else None}
            value_buf.clear()
            inline_buf.clear()
        elif cell is not None:
            if tag == "v":
                in_v = True
            elif tag == "is":
                in_is = True
            elif tag == "rPh":
                phonetic += 1
            elif tag == "t" and in_is and not phonetic:
                in_t = True

    def end(tag: str) -> None:
        nonlocal truncated, in_v, in_is, in_t, phonetic
        if tag == "v":
            in_v = False
        elif tag == "is":
            in_is = False
        elif tag == "rPh":
            phonetic = max(0, phonetic - 1)
        elif tag == "t":
            in_t = False
        elif tag == "c" and cell is not None:
            finish_cell()
        elif tag == "row" and row:
            if max_rows is not None and len(rows) >= max_rows:
                truncated = True
                raise _Stop
            width = max(row) + 1
            rows.append(tuple(row.get(i) for i in range(width)))
            if cells > max_cells:
                truncated = True
                raise _Stop

    def text(chunk: str) -> None:
        if in_v:
            value_buf.append(chunk)
        elif in_t:
            inline_buf.append(chunk)

    package.stream(part, start, end, text)
    return tuple(rows), truncated


def read_xlsx(
    data: bytes,
    *,
    max_rows_per_sheet: int | None = None,
    max_cells: int = 5_000_000,
    limits: OoxmlLimits | None = None,
) -> list[Sheet]:
    """Every worksheet of a workbook, in workbook order, with typed cells.

    ``max_rows_per_sheet`` stops reading a sheet once it has that many non-empty rows and another
    one follows; the sheet then comes back with ``truncated=True``, so the caller can report that
    rows were dropped instead of passing a clipped table off as whole. ``max_cells`` is the same
    safety valve across the whole workbook. Raises :class:`OoxmlError` for a file that is not a
    readable workbook and :class:`OoxmlLimitError` for one that is over a budget.
    """
    package = Package(data, limits=limits)
    workbook_part = package.main_part("officeDocument", "xl/workbook.xml")
    root = package.tree(workbook_part)
    if root.tag != "workbook":
        raise OoxmlError("the file is not an Excel workbook")
    relations = {r.id: r for r in package.relationships(workbook_part)}
    properties = root.child("workbookPr")
    date1904 = bool(properties and (properties.attr("date1904") or "").lower() in ("1", "true"))

    shared_part = next((r.target for r in relations.values() if r.type == "sharedStrings"), "xl/sharedStrings.xml")
    shared = _shared_strings(package, shared_part) if package.has(shared_part) else []
    styles_part = next((r.target for r in relations.values() if r.type == "styles"), "xl/styles.xml")
    kinds = _cell_formats(package, styles_part) if package.has(styles_part) else {}

    sheets_node = root.child("sheets")
    declared = list(sheets_node.children) if sheets_node else []
    sheets: list[Sheet] = []
    budget = max_cells
    for entry in declared:
        if entry.tag != "sheet":
            continue
        relation = relations.get(entry.rel_id() or "")
        if relation is None or relation.type != "worksheet":
            continue  # a chart sheet or dialog sheet: no cells
        if not package.has(relation.target):
            raise OoxmlError("the workbook lists a sheet whose part is missing", details={"part": relation.target})
        rows, truncated = _read_sheet(package, relation.target, shared, kinds, date1904, max_rows_per_sheet, budget)
        budget = max(0, budget - sum(len(r) for r in rows))
        sheets.append(Sheet(entry.attr("name") or f"Sheet{len(sheets) + 1}", rows, truncated,
                            entry.attr("state") or "visible"))
    if not sheets:
        raise OoxmlError("the workbook has no worksheets")
    return sheets
