"""Not exposed (ADR-036, 2026-11-21) -- deliberately NOT registered as a tool. Every method first treats
its argument as a path and reads the file if it exists, so on the gateway it is a file reader
(KAN-34).

Reachable from no persona: nothing under ``aura_core`` imports this module, which
``tests/architecture/test_open_tools_classification.py`` checks against
``tools/open/_classification.py``. Kept as reference code; change that classification and
ADR-036 before wiring it in.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


class NotebookReader:
    """Reader for parsing Jupyter (.ipynb) notebooks, extracting code, markdown, outputs, and stripping outputs."""

    @staticmethod
    def _read(path_or_json: str) -> str:
        """The notebook text: the file's contents if the argument names a file, else the argument.

        Probing a long JSON string as a path raises ``OSError`` (``File name too long``) rather
        than returning False, which made every inline notebook over ~255 characters unreadable.
        """
        try:
            candidate = Path(path_or_json)
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8")
        except (OSError, ValueError):
            pass  # too long to be a file name, or contains a NUL: it is JSON text
        return path_or_json

    @staticmethod
    def parse_notebook(path_or_json: str) -> Dict[str, Any]:
        """Parse notebook JSON and separate code cells and markdown cells cleanly."""
        try:
            nb = json.loads(NotebookReader._read(path_or_json))
        except Exception as exc:
            return {
                "valid": False,
                "error": f"Failed to parse notebook JSON: {exc}",
                "code_cells": [],
                "markdown_cells": [],
            }

        if not isinstance(nb, dict) or not isinstance(nb.get("cells"), list):
            return {
                "valid": False,
                "error": "Invalid notebook structure: missing 'cells' list.",
                "code_cells": [],
                "markdown_cells": [],
            }

        code_cells: List[Dict[str, Any]] = []
        markdown_cells: List[Dict[str, Any]] = []

        for idx, cell in enumerate(nb["cells"]):
            if not isinstance(cell, dict):
                continue
            ctype = cell.get("cell_type", "")
            src_field = cell.get("source", "")
            source_text = "".join(src_field) if isinstance(src_field, list) else str(src_field)

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

        return {
            "valid": True,
            "total_cells": len(nb["cells"]),
            "code_cells_count": len(code_cells),
            "markdown_cells_count": len(markdown_cells),
            "code_cells": code_cells,
            "markdown_cells": markdown_cells,
        }

    @classmethod
    def extract_runnable_python(cls, path_or_json: str) -> str:
        """Convert all notebook code cells into a standalone runnable Python script."""
        res = cls.parse_notebook(path_or_json)
        if not res["valid"]:
            return f"# Error parsing notebook: {res.get('error')}\n"

        py_lines = [
            f"# Auto-generated Python script from notebook",
            f"# Total Code Cells: {res['code_cells_count']}",
            "",
        ]

        for cell in res["code_cells"]:
            idx = cell["cell_index"]
            py_lines.append(f"# --- [Cell {idx}] ---")
            raw_code = cell["code"]
            # Comment out ipython magic lines (% or !)
            for line in raw_code.splitlines():
                stripped = line.strip()
                if stripped.startswith("%") or stripped.startswith("!"):
                    py_lines.append(f"# {line}  # ipython magic")
                else:
                    py_lines.append(line)
            py_lines.append("")

        return "\n".join(py_lines)

    @classmethod
    def extract_markdown_document(cls, path_or_json: str) -> str:
        """Extract narrative text from markdown cells as a clean documentation document."""
        res = cls.parse_notebook(path_or_json)
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
    def extract_cell_outputs(path_or_json: str) -> List[Dict[str, Any]]:
        """Extract captured stdout, stderr, errors, and image display metadata from notebook cells."""
        try:
            nb = json.loads(NotebookReader._read(path_or_json))
        except Exception:
            return []
        if not isinstance(nb, dict):
            return []

        outputs = []
        for idx, cell in enumerate(nb.get("cells", [])):
            if cell.get("cell_type") != "code":
                continue
            for out in cell.get("outputs", []):
                out_type = out.get("output_type", "")
                text = ""
                if "text" in out:
                    text = "".join(out["text"]) if isinstance(out["text"], list) else str(out["text"])
                elif "data" in out and "text/plain" in out["data"]:
                    t = out["data"]["text/plain"]
                    text = "".join(t) if isinstance(t, list) else str(t)

                has_image = "data" in out and any("image/" in k for k in out["data"])
                outputs.append({
                    "cell_index": idx,
                    "output_type": out_type,
                    "text": text.strip(),
                    "has_image": has_image,
                })
        return outputs

    @staticmethod
    def strip_notebook_outputs(path_or_json: str) -> str:
        """Clear all output cells and execution counts for clean git commit diffs."""
        nb = json.loads(NotebookReader._read(path_or_json))  # invalid JSON raises ValueError

        for cell in nb.get("cells", []):
            if cell.get("cell_type") == "code":
                cell["outputs"] = []
                cell["execution_count"] = None

        return json.dumps(nb, indent=2)
