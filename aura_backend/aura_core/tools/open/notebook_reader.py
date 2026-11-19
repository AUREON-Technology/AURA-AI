import json
from pathlib import Path
from typing import Any, Dict, List


class NotebookReader:
    """Reader for parsing Jupyter (.ipynb) notebooks from file path or JSON string."""

    @staticmethod
    def parse_notebook(path_or_json: str) -> Dict[str, Any]:
        """Parse notebook JSON and separate code cells and markdown cells cleanly."""
        candidate = Path(path_or_json)
        try:
            if candidate.exists() and candidate.is_file():
                raw_text = candidate.read_text(encoding="utf-8")
            else:
                raw_text = path_or_json
            nb = json.loads(raw_text)
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