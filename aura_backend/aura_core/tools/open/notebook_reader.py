"""Reader for Jupyter (``.ipynb``) notebooks: code and markdown cells, outputs, runnable-script export.

Hardening notes (review of 2026-11):

* ``path_or_json`` is either a path or the notebook JSON itself. The old test ``Path(text).exists()``
  raised ``OSError: File name too long`` for any inline notebook longer than ~255 characters, so every
  realistic inline notebook was reported as invalid JSON. Text that starts with ``{`` is JSON; anything
  else is a path.
* Files are read through ``_input``: UTF-8 (BOM tolerated) with a Latin-1 fallback, size-capped, and an
  optional ``base_dir`` confinement. A **PDF** given as the notebook is read in full (every page) and
  then refused with an error that says so -- a PDF is not a notebook.
* Malformed notebooks (cells that are not objects, ``source`` lists with non-strings, outputs of the
  wrong shape) no longer raise ``AttributeError`` / ``TypeError``.
* ``extract_cell_outputs`` also returns ``error`` outputs (name, value, traceback) which it used to
  drop; ``extract_cell_outputs_report`` tells an empty notebook from an unreadable one.
* ``extract_runnable_python`` comments out the *whole* body of a ``%%cell magic`` cell (it used to
  comment only the first line and emit the shell/SQL body as Python).
* ``strip_notebook_outputs`` raises ``ValueError`` (documented) for input that is not a notebook rather
  than a bare ``JSONDecodeError`` / ``AttributeError``, and keeps non-Latin text readable
  (``ensure_ascii=False``) so a diff of an Arabic notebook is not rewritten as ``\\u0627`` escapes.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from ._input import SourceReadError, read_source_file

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _text(value: Any) -> str:
    """A notebook text field (string, or list of strings) as one string."""
    if isinstance(value, list):
        return "".join(item if isinstance(item, str) else str(item) for item in value)
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def _load(path_or_json: Any, base_dir: Any = None) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[Dict[str, Any]]]:
    """``(notebook, None, input_info)`` or ``(None, user-safe error, input_info)``."""
    if isinstance(path_or_json, bytes):
        try:
            path_or_json = path_or_json.decode("utf-8-sig")
        except UnicodeDecodeError:
            return None, "Notebook bytes are not valid UTF-8 text.", None
    if not isinstance(path_or_json, str) or not path_or_json.strip():
        return None, "Notebook input must be a non-empty path or JSON string.", None

    info: Optional[Dict[str, Any]] = None
    stripped = path_or_json.lstrip("﻿ \t\r\n")
    if stripped.startswith("{") or "\n" in path_or_json.strip():
        raw_text = path_or_json
    else:
        try:
            read = read_source_file(path_or_json, base_dir=base_dir)
        except SourceReadError as exc:
            return None, str(exc), None
        info = read.describe()
        if read.kind == "pdf":
            return (
                None,
                f"The PDF's {read.pages} page(s) were read in full, but a PDF is not a Jupyter notebook; "
                "pass an .ipynb file or the notebook JSON.",
                info,
            )
        raw_text = read.text

    try:
        nb = json.loads(raw_text)
    except (ValueError, RecursionError) as exc:
        return None, f"Failed to parse notebook JSON: {exc}", info
    if not isinstance(nb, dict) or not isinstance(nb.get("cells"), list):
        return None, "Invalid notebook structure: missing 'cells' list.", info
    return nb, None, info


def _cells(nb: Dict[str, Any]) -> List[Tuple[int, Dict[str, Any]]]:
    return [(i, c) for i, c in enumerate(nb["cells"]) if isinstance(c, dict)]


class NotebookReader:
    """Reader for parsing Jupyter (.ipynb) notebooks, extracting code, markdown, outputs, and stripping outputs."""

    @staticmethod
    def parse_notebook(path_or_json: str, base_dir: Optional[str] = None) -> Dict[str, Any]:
        """Parse notebook JSON and separate code cells and markdown cells cleanly."""
        nb, error, info = _load(path_or_json, base_dir)
        if nb is None:
            result: Dict[str, Any] = {
                "valid": False,
                "error": error,
                "code_cells": [],
                "markdown_cells": [],
            }
            if info is not None:
                result["input"] = info
            return result

        code_cells: List[Dict[str, Any]] = []
        markdown_cells: List[Dict[str, Any]] = []

        for idx, cell in _cells(nb):
            ctype = cell.get("cell_type", "")
            source_text = _text(cell.get("source", ""))
            if ctype == "code":
                code_cells.append({
                    "cell_index": idx,
                    "execution_count": cell.get("execution_count"),
                    "code": source_text,
                    "has_output": bool(cell.get("outputs")),
                })
            elif ctype == "markdown":
                markdown_cells.append({
                    "cell_index": idx,
                    "markdown": source_text,
                })

        result = {
            "valid": True,
            "total_cells": len(nb["cells"]),
            "code_cells_count": len(code_cells),
            "markdown_cells_count": len(markdown_cells),
            "code_cells": code_cells,
            "markdown_cells": markdown_cells,
        }
        if info is not None:
            result["input"] = info
        return result

    @classmethod
    def extract_runnable_python(cls, path_or_json: str, base_dir: Optional[str] = None) -> str:
        """Convert all notebook code cells into a standalone runnable Python script."""
        res = cls.parse_notebook(path_or_json, base_dir)
        if not res["valid"]:
            return f"# Error parsing notebook: {res.get('error')}\n"

        py_lines = [
            "# Auto-generated Python script from notebook",
            f"# Total Code Cells: {res['code_cells_count']}",
            "",
        ]

        for cell in res["code_cells"]:
            idx = cell["cell_index"]
            py_lines.append(f"# --- [Cell {idx}] ---")
            lines = cell["code"].splitlines()
            first = next((ln.strip() for ln in lines if ln.strip()), "")
            cell_magic = first.startswith("%%")
            for line in lines:
                stripped = line.strip()
                if cell_magic:
                    # The whole body belongs to the magic (bash, sql, html, ...), not to Python.
                    py_lines.append(f"# {line}  # ipython cell magic" if stripped else "#")
                elif stripped.startswith("%") or stripped.startswith("!"):
                    py_lines.append(f"# {line}  # ipython magic")
                else:
                    py_lines.append(line)
            py_lines.append("")

        return "\n".join(py_lines)

    @classmethod
    def extract_markdown_document(cls, path_or_json: str, base_dir: Optional[str] = None) -> str:
        """Extract narrative text from markdown cells as a clean documentation document."""
        res = cls.parse_notebook(path_or_json, base_dir)
        if not res["valid"]:
            return f"# Error: {res.get('error')}\n"

        md_lines = [
            "# Notebook Documentation Extract",
            "",
        ]
        for cell in res["markdown_cells"]:
            md_lines.append(cell["markdown"])
            md_lines.append("")

        return "\n".join(md_lines).strip()

    @staticmethod
    def extract_cell_outputs_report(path_or_json: str, base_dir: Optional[str] = None) -> Dict[str, Any]:
        """Outputs of every code cell, or ``valid: False`` with the reason (not an empty list)."""
        nb, error, info = _load(path_or_json, base_dir)
        if nb is None:
            result: Dict[str, Any] = {"valid": False, "error": error, "outputs": []}
            if info is not None:
                result["input"] = info
            return result

        outputs: List[Dict[str, Any]] = []
        for idx, cell in _cells(nb):
            if cell.get("cell_type") != "code" or not isinstance(cell.get("outputs"), list):
                continue
            for out in cell["outputs"]:
                if not isinstance(out, dict):
                    continue
                out_type = out.get("output_type", "")
                data = out.get("data") if isinstance(out.get("data"), dict) else {}
                entry: Dict[str, Any] = {"cell_index": idx, "output_type": out_type}

                if out_type == "error":
                    text = f"{_text(out.get('ename'))}: {_text(out.get('evalue'))}".strip(": ")
                    tb = out.get("traceback")
                    entry["traceback"] = _ANSI.sub(
                        "", "\n".join(_text(t) for t in tb) if isinstance(tb, list) else _text(tb)
                    )
                elif "text" in out:
                    text = _text(out["text"])
                elif "text/plain" in data:
                    text = _text(data["text/plain"])
                else:
                    text = ""
                if out_type == "stream":
                    entry["stream"] = out.get("name", "")

                entry["text"] = text.strip()
                entry["has_image"] = any("image/" in str(k) for k in data)
                outputs.append(entry)

        result = {"valid": True, "outputs": outputs}
        if info is not None:
            result["input"] = info
        return result

    @classmethod
    def extract_cell_outputs(cls, path_or_json: str, base_dir: Optional[str] = None) -> List[Dict[str, Any]]:
        """Extract captured stdout, stderr, errors, and image display metadata from notebook cells.

        Returns ``[]`` for an unreadable notebook; use :meth:`extract_cell_outputs_report` to tell the
        two apart.
        """
        return list(cls.extract_cell_outputs_report(path_or_json, base_dir)["outputs"])

    @staticmethod
    def strip_notebook_outputs(path_or_json: str, base_dir: Optional[str] = None) -> str:
        """Clear all output cells and execution counts for clean git commit diffs.

        Raises ``ValueError`` (message is user-safe) when the input is not a readable notebook.
        """
        nb, error, _info = _load(path_or_json, base_dir)
        if nb is None:
            raise ValueError(error)

        for _idx, cell in _cells(nb):
            if cell.get("cell_type") == "code":
                cell["outputs"] = []
                cell["execution_count"] = None

        return json.dumps(nb, indent=1, ensure_ascii=False, sort_keys=True) + "\n"
