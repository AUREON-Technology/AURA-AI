"""Parse and validate JSON, YAML, TOML and XML payloads.

Hardening notes (review of 2026-11):

* ``parse`` / ``parse_with_line_errors`` / ``convert_format`` always return a result dict. A ``None``
  format, non-text content (bytes are decoded; a PDF is read in full), or oversized content no longer
  raises or exhausts memory.
* **Entity / alias bombs.** XML documents that declare ``<!ENTITY`` are refused (billion laughs), and
  a YAML document whose aliases would expand to more than ``MAX_YAML_NODES`` nodes is refused *before*
  it is expanded.
* ``parse_with_line_errors`` returns the same JSON-safe ``parsed_data`` as ``parse`` (it used to return
  a live ``xml.etree`` ``Element``) and reports line numbers for YAML and XML errors too.
* ``parse_file`` reads a path through ``_input`` (size cap, ``base_dir`` confinement, UTF-8 with BOM
  handling). A **PDF** is read in full, page by page, and its text is parsed like any other text; when
  it is not valid in the requested format the error says the PDF was read completely.
* ``validate_schema`` no longer treats ``True`` as an ``int`` and no longer passes a field whose
  contract type it does not know ("unknown type" is an error, not a silent pass).
* ``flatten_nested_dict`` keeps empty nested dicts, is iterative (no ``RecursionError``) and survives
  self-referencing dicts.
* ``convert_format`` writes non-ASCII text as text (``ensure_ascii=False`` / ``allow_unicode=True``).
  Only JSON and YAML targets exist; TOML output has no stdlib writer and is reported as unsupported.
"""

from __future__ import annotations

import json
import re
import tomllib
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

from ._input import SourceReadError, read_source_bytes, read_source_file

#: Content larger than this (characters) is refused. 20 M characters is ~20 MB of config.
MAX_CONTENT_CHARS = 20_000_000
#: A YAML document that expands past this many nodes (after resolving aliases) is refused.
MAX_YAML_NODES = 2_000_000
_XML_DEPTH_LIMIT = 200

_FORMAT_BY_EXT = {".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml", ".xml": "xml"}


class _Refused(Exception):
    """Content that is refused before parsing (too large, entity/alias bomb)."""


def _fmt(data_format: Any) -> str:
    return data_format.strip().lower() if isinstance(data_format, str) else ""


def _coerce_text(content: Any) -> str:
    """``content`` as text. Bytes are decoded (a PDF is read in full). Raises ``_Refused``/TypeError."""
    if isinstance(content, (bytes, bytearray)):
        try:
            return read_source_bytes(bytes(content)).text
        except SourceReadError as exc:
            raise _Refused(str(exc)) from exc
    if not isinstance(content, str):
        raise TypeError(f"content must be text, got {type(content).__name__}")
    if len(content) > MAX_CONTENT_CHARS:
        raise _Refused(f"Content is too large to parse ({len(content)} characters; limit {MAX_CONTENT_CHARS}).")
    return content


def _yaml_module() -> Any:
    try:
        import yaml  # type: ignore

        return yaml
    except ImportError:
        return None


def _yaml_load(content: str) -> Any:
    yaml = _yaml_module()
    if yaml is None:
        raise ImportError("PyYAML is not installed.")
    loader = yaml.SafeLoader(content)
    try:
        node = loader.get_single_node()
        if node is None:
            return None
        # Size of the *expanded* document, computed on the shared-node graph without expanding it.
        sizes: Dict[int, int] = {}
        seen: set = set()

        def children_of(current: Any) -> List[Any]:
            value = getattr(current, "value", None)
            kids: List[Any] = []
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, tuple):
                        kids.extend(item)
                    elif hasattr(item, "tag"):
                        kids.append(item)
            return kids

        stack = [(node, False)]
        while stack:
            current, done = stack.pop()
            key = id(current)
            if done:
                sizes[key] = 1 + sum(sizes.get(id(c), 1) for c in children_of(current))
                if sizes[key] > MAX_YAML_NODES:
                    raise _Refused("YAML expands to too many nodes through aliases; refusing to load it.")
                continue
            if key in seen:  # shared node already counted, or a recursive alias
                continue
            seen.add(key)
            stack.append((current, True))
            stack.extend((c, False) for c in children_of(current))
        return loader.construct_document(node)
    finally:
        loader.dispose()


def _xml_to_dict(root: ET.Element) -> Dict[str, Any]:
    """The element tree as nested dicts (``tag``, ``attrib``, ``text``, ``children``), depth-capped."""

    def convert(el: ET.Element, depth: int) -> Dict[str, Any]:
        node: Dict[str, Any] = {
            "tag": el.tag,
            "attrib": dict(el.attrib),
            "children_count": len(list(el)),
            "text": el.text.strip() if el.text else "",
        }
        if depth >= _XML_DEPTH_LIMIT:
            node["children"] = []
            node["truncated"] = len(list(el)) > 0
        else:
            node["children"] = [convert(child, depth + 1) for child in el]
        return node

    return convert(root, 0)


def _parse_text(content: str, fmt: str) -> Any:
    """Parse ``content``; raises the parser's own exception or ``_Refused``."""
    if fmt == "json":
        return json.loads(content)
    if fmt in ("yaml", "yml"):
        return _yaml_load(content)
    if fmt == "toml":
        return tomllib.loads(content)
    if fmt == "xml":
        if "<!ENTITY" in content:
            raise _Refused("XML entity declarations (<!ENTITY) are not accepted.")
        return _xml_to_dict(ET.fromstring(content))  # noqa: S314 -- entity declarations refused above
    raise ValueError(f"Unsupported format '{fmt}'.")


class StructuredDataParser:
    """Parser and validator for JSON, YAML, TOML, and XML structured payloads with schema validation and line diagnostics."""

    @staticmethod
    def parse(content: str, data_format: str = "json") -> Dict[str, Any]:
        """Parse structured text in json, yaml/yml, toml, or xml format."""
        fmt = _fmt(data_format)
        if fmt not in ("json", "yaml", "yml", "toml", "xml"):
            return {"valid": False, "format": fmt, "error": f"Unsupported format '{data_format}'."}
        try:
            text = _coerce_text(content)
            parsed = _parse_text(text, fmt)

            if fmt == "xml":
                summary_count = parsed["children_count"]
            elif isinstance(parsed, (dict, list)):
                summary_count = len(parsed)
            else:
                summary_count = 1

            return {
                "valid": True,
                "format": fmt,
                "parsed_data": parsed,
                "items_count": summary_count,
            }
        except ImportError as exc:
            return {"valid": False, "format": fmt, "error": str(exc)}
        except (_Refused, TypeError) as exc:
            return {"valid": False, "format": fmt, "error": str(exc)}
        except Exception as exc:
            return {
                "valid": False,
                "format": fmt,
                "error": f"{type(exc).__name__}: {exc}",
            }

    @classmethod
    def parse_file(
        cls,
        file_path: str,
        data_format: Optional[str] = None,
        base_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Parse a file. The format defaults to the extension (``.json``, ``.yaml``, ``.toml``, ``.xml``).

        A PDF is read in full (all pages) and its extracted text is parsed; ``input`` reports pages read
        and any scanned page that needs OCR.
        """
        try:
            read = read_source_file(file_path, base_dir=base_dir)
        except SourceReadError as exc:
            return {"valid": False, "error": str(exc)}

        fmt = _fmt(data_format)
        if not fmt:
            from pathlib import PurePath

            fmt = _FORMAT_BY_EXT.get(PurePath(str(file_path)).suffix.lower(), "json")
        result = cls.parse(read.text, fmt)
        result["input"] = read.describe()
        if read.warnings():
            result["warnings"] = read.warnings()
        if read.kind == "pdf" and not result.get("valid"):
            result["error"] = (
                f"{result.get('error')} (the PDF's {read.pages} page(s) were read in full, but text "
                f"extracted from a PDF is rarely valid {fmt.upper()})"
            )
        return result

    @staticmethod
    def parse_with_line_errors(content: str, data_format: str = "json") -> Dict[str, Any]:
        """Parse content and extract exact line number and error context when parsing fails."""
        fmt = _fmt(data_format)
        if fmt not in ("json", "yaml", "yml", "toml", "xml"):
            return {"valid": False, "error": f"Unsupported format: {fmt}", "line": 0}
        try:
            text = _coerce_text(content)
        except (_Refused, TypeError) as exc:
            return {"valid": False, "error": str(exc), "line": 0}
        lines = text.splitlines()

        def at(line_no: Optional[int]) -> str:
            return lines[line_no - 1] if line_no and 0 < line_no <= len(lines) else ""

        try:
            parsed = _parse_text(text, fmt)
            return {"valid": True, "error": None, "line": None, "parsed_data": parsed}
        except ImportError:
            return {"valid": False, "error": "PyYAML not installed", "line": 0}
        except _Refused as exc:
            return {"valid": False, "error": str(exc), "line": 0}
        except json.JSONDecodeError as exc:
            line_no = exc.lineno
            col_no = exc.colno
            return {
                "valid": False,
                "error": exc.msg,
                "line": line_no,
                "column": col_no,
                "offending_line": at(line_no),
                "context": lines[max(0, line_no - 2) : min(len(lines), line_no + 1)],
            }
        except tomllib.TOMLDecodeError as exc:
            # Extract line number from toml error message if available
            m = re.search(r"line\s+(\d+)", str(exc))
            line_no = int(m.group(1)) if m else 1
            return {
                "valid": False,
                "error": str(exc),
                "line": line_no,
                "offending_line": at(line_no),
            }
        except ET.ParseError as exc:
            position = getattr(exc, "position", None)
            if position:
                line_no, col_no = int(position[0]), int(position[1]) + 1
            else:
                m = re.search(r"line\s+(\d+),\s*column\s+(\d+)", str(exc))
                line_no = int(m.group(1)) if m else 1
                col_no = int(m.group(2)) if m else 1
            return {
                "valid": False,
                "error": str(exc),
                "line": line_no,
                "column": col_no,
                "offending_line": at(line_no),
            }
        except Exception as exc:
            mark = getattr(exc, "problem_mark", None)  # PyYAML errors carry the position
            if mark is not None:
                line_no = int(mark.line) + 1
                return {
                    "valid": False,
                    "error": getattr(exc, "problem", None) or str(exc),
                    "line": line_no,
                    "column": int(mark.column) + 1,
                    "offending_line": at(line_no),
                }
            return {
                "valid": False,
                "error": str(exc),
                "line": None,
            }

    @staticmethod
    def validate_schema(
        data: Any,
        required_keys: Optional[List[str]] = None,
        type_contracts: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Validate parsed dictionary against required keys and data type contracts.

        Supported contract types: ``str``, ``int``, ``float`` (int or float), ``bool``, ``list``,
        ``dict``. ``bool`` is not accepted where ``int`` / ``float`` is required. A contract that
        names any other type is reported as an error rather than ignored.
        """
        if not isinstance(data, dict):
            return {"valid": False, "errors": ["Root data is not a dictionary / object."]}

        errors: List[str] = []
        if isinstance(required_keys, str):
            required_keys = [required_keys]
        reqs = required_keys or []
        for k in reqs:
            if k not in data:
                errors.append(f"Missing required key: '{k}'")

        type_map = {
            "str": str,
            "int": int,
            "float": (int, float),
            "bool": bool,
            "list": list,
            "dict": dict,
        }

        if isinstance(type_contracts, dict):
            for field, expected_type_name in type_contracts.items():
                name = expected_type_name.lower() if isinstance(expected_type_name, str) else None
                target_cls = type_map.get(name) if name else None
                if target_cls is None:
                    errors.append(f"Unknown type '{expected_type_name}' in contract for field '{field}'")
                    continue
                if field in data and data[field] is not None:
                    value = data[field]
                    ok = isinstance(value, target_cls)
                    if name in ("int", "float") and isinstance(value, bool):
                        ok = False
                    if not ok:
                        errors.append(f"Field '{field}' expected {expected_type_name}, got {type(value).__name__}")

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "checked_keys": list(data.keys()),
        }

    @classmethod
    def convert_format(
        cls,
        content: str,
        source_format: str,
        target_format: str,
    ) -> Dict[str, Any]:
        """Convert structured content between JSON and YAML (reading JSON, YAML, TOML or XML).

        Values JSON/YAML cannot represent natively (TOML/YAML dates, bytes) are written as strings;
        that is the one lossy step and is flagged in ``warnings``.
        """
        parsed_res = cls.parse(content, data_format=source_format)
        if not parsed_res["valid"]:
            return {"converted": False, "error": f"Failed to parse source: {parsed_res['error']}"}

        data = parsed_res["parsed_data"]
        tgt = _fmt(target_format)
        warnings: List[str] = []

        try:
            if tgt == "json":
                lossy: List[str] = []

                def fallback(value: Any) -> str:
                    lossy.append(type(value).__name__)
                    return str(value)

                out = json.dumps(data, indent=2, default=fallback, ensure_ascii=False)
                if lossy:
                    warnings.append(f"Converted non-JSON value type(s) to strings: {sorted(set(lossy))}")
            elif tgt in ("yaml", "yml"):
                yaml = _yaml_module()
                if yaml is None:
                    return {"converted": False, "error": "PyYAML not installed."}
                out = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
            else:
                return {"converted": False, "error": f"Unsupported target format: {tgt}"}

            result: Dict[str, Any] = {
                "converted": True,
                "source_format": source_format,
                "target_format": tgt,
                "output_text": out,
            }
            if warnings:
                result["warnings"] = warnings
            return result
        except Exception as exc:
            return {"converted": False, "error": str(exc)}

    @staticmethod
    def flatten_nested_dict(
        data: Dict[str, Any],
        prefix: str = "",
        separator: str = ".",
    ) -> Dict[str, Any]:
        """Flatten a deeply nested dictionary into dot-separated paths.

        Empty nested dicts are kept as ``{}`` values; a dict that contains itself is cut at the repeat.
        """
        flat: Dict[str, Any] = {}
        if not isinstance(data, dict):
            return flat
        sep = separator if isinstance(separator, str) else "."
        stack = [(iter(data.items()), prefix, frozenset([id(data)]))]
        while stack:
            items, pfx, ancestors = stack[-1]
            try:
                k, v = next(items)
            except StopIteration:
                stack.pop()
                continue
            key = f"{pfx}{sep}{k}" if pfx else str(k)
            if isinstance(v, dict):
                if id(v) in ancestors:
                    flat[key] = "<circular reference>"
                elif not v:
                    flat[key] = {}
                else:
                    stack.append((iter(v.items()), key, ancestors | {id(v)}))
            else:
                flat[key] = v
        return flat
        sep = separator if isinstance(separator, str) else "."
        stack = [(data, prefix, frozenset([id(data)]))]
        while stack:
            current, pfx, ancestors = stack.pop()
            if not current and pfx:
                flat[pfx] = {}
                continue
            for k, v in current.items():
                key = f"{pfx}{sep}{k}" if pfx else str(k)
                if isinstance(v, dict) and id(v) not in ancestors:
                    stack.append((v, key, ancestors | {id(v)}))
                else:
                    flat[key] = v if not isinstance(v, dict) else "<circular reference>"
        return flat
