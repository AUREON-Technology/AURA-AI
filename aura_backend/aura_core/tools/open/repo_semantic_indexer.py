"""Repository indexer: per-file metadata, line chunks, Python symbols and keyword search.

Hardening notes (review of 2026-11):

* **PDFs are indexed by their full extracted text.** ``.pdf`` is in the default extensions and every
  file is read through ``_input`` (all pages, page by page); ``lines`` / ``chunks`` / keyword search
  are computed on that text. A scanned page cannot be read without OCR: the file entry says
  ``complete: False`` with ``unreadable_pages`` and the result's ``warnings`` name it. Previously a
  PDF passed in ``extensions`` was decoded as UTF-8 with errors ignored -- garbage.
* Nothing is skipped silently: a file that cannot be read (binary, too large, unreadable, outside the
  root) is listed under ``skipped_files`` with the reason; ``truncated`` says the ``max_files`` limit
  stopped the scan. ``status`` is ``"success"`` only when everything was read in full, otherwise
  ``"partial"``.
* "Ignored directory" is judged on the path *below* the root: a repository located under a directory
  named ``build`` or ``dist`` was previously indexed as empty. Ignored directories are pruned while
  walking rather than walked and filtered, and symlinked directories are not followed.
* Symbols: a name defined in several files (``main``, ``__init__``, ``test_x``) overwrote the previous
  one in ``symbols``. All definitions are now kept in ``symbol_locations`` and ``search_symbols`` /
  ``get_index_statistics`` use them; ``symbols`` keeps its old shape.
* ``ast.parse`` failures other than ``SyntaxError`` no longer abort the whole index.
* Keyword search reads files only from inside the indexed root, tokenises with ``\\w`` (non-Latin
  queries work) and clamps ``max_results``.
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Set

from ._input import SourceReadError, read_source_file

_LINE_SPLIT = re.compile(r"\r\n|\r|\n")
_PARSE_ERRORS = (SyntaxError, ValueError, RecursionError, MemoryError)


def _lines(text: str) -> List[str]:
    """Lines as ``ast`` and editors count them (``\\n``, ``\\r\\n``, ``\\r``; not form feed or U+2028)."""
    parts = _LINE_SPLIT.split(text)
    if parts and parts[-1] == "":
        parts.pop()
    return parts


def _int(value: Any, default: int, minimum: int = 1) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= minimum else default


class RepoSemanticIndexer:
    """Semantic repository indexer for scanning files, generating chunk embeddings/windows, and symbol search."""

    DEFAULT_EXTENSIONS: Set[str] = {".py", ".md", ".json", ".yaml", ".yml", ".dart", ".ts", ".js", ".toml", ".pdf"}
    IGNORED_DIRS: Set[str] = {".git", "__pycache__", "venv", ".venv", "node_modules", ".pytest_cache", "dist", "build"}

    @classmethod
    def index_repository(
        cls,
        root_path: str,
        extensions: Optional[List[str]] = None,
        chunk_lines: int = 50,
        max_files: int = 1000,
    ) -> Dict[str, Any]:
        """Scan a repository directory and extract file metadata, chunks, and symbols.

        PDF files are read in full; see the module notes for what is reported when something could
        not be read.
        """
        try:
            root = Path(root_path)
            ok = root.exists() and root.is_dir()
        except (OSError, ValueError, TypeError):
            ok = False
        if not ok:
            return {"status": "error", "error": f"Repository path not found: {root_path}"}

        if isinstance(extensions, str):
            extensions = [extensions]
        if extensions:
            allowed_exts = {
                e.lower() if e.startswith(".") else f".{e.lower()}" for e in extensions if isinstance(e, str) and e
            }
        else:
            allowed_exts = cls.DEFAULT_EXTENSIONS
        step = _int(chunk_lines, 50)
        file_limit = _int(max_files, 1000)

        indexed_files: List[Dict[str, Any]] = []
        symbol_index: Dict[str, Dict[str, Any]] = {}
        symbol_locations: Dict[str, List[Dict[str, Any]]] = {}
        skipped: List[Dict[str, str]] = []
        warnings: List[str] = []
        total_chunks = 0
        scanned = 0
        truncated = False

        for current, dirs, names in os.walk(root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in cls.IGNORED_DIRS)
            for name in sorted(names):
                path = Path(current) / name
                if path.suffix.lower() not in allowed_exts:
                    continue
                if scanned >= file_limit:
                    truncated = True
                    break
                rel_path = path.relative_to(root).as_posix()
                scanned += 1
                try:
                    read = read_source_file(path, base_dir=root)
                except SourceReadError as exc:
                    skipped.append({"file": rel_path, "reason": str(exc)})
                    continue

                lines = _lines(read.text)
                chunks_count = (len(lines) + step - 1) // step
                total_chunks += chunks_count
                entry: Dict[str, Any] = {
                    "file": rel_path,
                    "lines": len(lines),
                    "chunks": chunks_count,
                    "kind": read.kind,
                    "complete": read.complete,
                }
                if read.kind == "pdf":
                    entry["pages"] = read.pages
                    entry["unreadable_pages"] = list(read.unreadable_pages)
                    entry["blank_pages"] = list(read.blank_pages)
                    warnings.extend(f"{rel_path}: {w}" for w in read.warnings())
                indexed_files.append(entry)

                if read.kind == "text" and path.suffix.lower() == ".py":
                    try:
                        tree = ast.parse(read.text)
                    except _PARSE_ERRORS:
                        entry["symbols_parsed"] = False
                        continue
                    entry["symbols_parsed"] = True
                    for node in ast.walk(tree):
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                            kind = "class" if isinstance(node, ast.ClassDef) else "function"
                            info = {"type": kind, "file": rel_path, "line": getattr(node, "lineno", 1)}
                            symbol_index[node.name] = dict(info)
                            symbol_locations.setdefault(node.name, []).append(info)
            if truncated:
                break

        if truncated:
            warnings.append(f"Stopped after {file_limit} files; the repository holds more matching files.")
        if skipped:
            warnings.append(f"{len(skipped)} file(s) could not be read and are not indexed (see skipped_files).")
        incomplete = [f["file"] for f in indexed_files if not f["complete"]]
        complete = not truncated and not skipped and not incomplete

        return {
            "status": "success" if complete else "partial",
            "complete": complete,
            "root": str(root),
            "indexed_files_count": len(indexed_files),
            "total_chunks": total_chunks,
            "symbols_indexed": len(symbol_index),
            "files": indexed_files,
            "symbols": symbol_index,
            "symbol_locations": symbol_locations,
            "skipped_files": skipped,
            "incomplete_files": incomplete,
            "truncated": truncated,
            "warnings": warnings,
        }

    @staticmethod
    def search_symbols(index_result: Dict[str, Any], query: str) -> List[Dict[str, Any]]:
        """Filter indexed symbols by case-insensitive substring match on symbol name.

        Every definition of a matching name is returned (a name defined in three files gives three rows).
        """
        if not isinstance(query, str) or not isinstance(index_result, dict):
            return []
        q = query.strip().lower()
        if not q:
            return []
        locations = index_result.get("symbol_locations")
        matches: List[Dict[str, Any]] = []
        if isinstance(locations, dict) and locations:
            for sym_name, infos in locations.items():
                if q in str(sym_name).lower():
                    matches.extend({"name": sym_name, **info} for info in infos)
            return matches
        for sym_name, info in (index_result.get("symbols") or {}).items():
            if q in str(sym_name).lower():
                matches.append({"name": sym_name, **info})
        return matches

    @staticmethod
    def chunk_file_for_indexing(
        content: str,
        chunk_size_lines: int = 40,
        overlap: int = 10,
    ) -> List[Dict[str, Any]]:
        """Split source text into overlapping chunks with line coordinates for semantic retrieval."""
        if not isinstance(content, str):
            return []
        lines = _lines(content)
        if not lines:
            return []

        size = _int(chunk_size_lines, 40)
        lap = overlap if isinstance(overlap, int) and not isinstance(overlap, bool) and overlap >= 0 else 0
        lap = min(lap, size - 1)

        chunks: List[Dict[str, Any]] = []
        start = 0
        step = max(1, size - lap)

        while start < len(lines):
            end = min(len(lines), start + size)
            chunk_slice = lines[start:end]
            chunks.append({
                "start_line": start + 1,
                "end_line": end,
                "text": "\n".join(chunk_slice),
                "lines_count": len(chunk_slice),
            })
            if end >= len(lines):
                break
            start += step

        return chunks

    @classmethod
    def search_text_bm25_or_keyword(
        cls,
        index_result: Dict[str, Any],
        query: str,
        workspace_root: Optional[str] = None,
        max_results: int = 10,
    ) -> List[Dict[str, Any]]:
        """Rank repository files by term matching score (filename hits and capped content hits).

        File contents are read through ``_input`` (a PDF in full, every page) and only from inside the
        root; a path in ``index_result`` that points elsewhere is ignored.
        """
        if not isinstance(query, str) or not isinstance(index_result, dict):
            return []
        tokens = [t.lower() for t in re.findall(r"\w{2,}", query)]
        if not tokens:
            return []

        root_str = workspace_root or index_result.get("root", "")
        root_path = Path(root_str) if isinstance(root_str, (str, Path)) and root_str else None
        files = index_result.get("files", [])
        scored_files: List[Dict[str, Any]] = []

        for f_info in files if isinstance(files, list) else []:
            rel = f_info.get("file", "") if isinstance(f_info, dict) else ""
            if not isinstance(rel, str) or not rel:
                continue
            score = 0.0

            # Match in filename
            for tok in tokens:
                if tok in rel.lower():
                    score += 5.0

            # Match in file content if root available
            if root_path:
                try:
                    text = read_source_file(root_path / rel, base_dir=root_path).text.lower()
                except SourceReadError:
                    text = ""
                for tok in tokens:
                    score += min(10.0, text.count(tok) * 0.5)

            if score > 0:
                scored_files.append({"file": rel, "relevance_score": round(score, 2), "lines": f_info.get("lines", 0)})

        scored_files.sort(key=lambda x: x["relevance_score"], reverse=True)
        return scored_files[: _int(max_results, 10)]

    @staticmethod
    def get_index_statistics(index_result: Dict[str, Any]) -> Dict[str, Any]:
        """Generate statistical aggregation of repository contents."""
        if not isinstance(index_result, dict):
            index_result = {}
        files = index_result.get("files", [])
        symbols = index_result.get("symbols", {})
        locations = index_result.get("symbol_locations")

        ext_breakdown: Dict[str, int] = {}
        total_lines = 0

        for f in files:
            name = f.get("file", "")
            ext = PurePosixPath(name).suffix.lower() or "no_ext"
            ext_breakdown[ext] = ext_breakdown.get(ext, 0) + 1
            total_lines += f.get("lines", 0)

        if isinstance(locations, dict) and locations:
            definitions = [d for infos in locations.values() for d in infos]
        else:
            definitions = list(symbols.values())
        classes_count = sum(1 for s in definitions if s.get("type") == "class")
        functions_count = sum(1 for s in definitions if s.get("type") == "function")

        return {
            "total_files": len(files),
            "total_lines": total_lines,
            "total_chunks": index_result.get("total_chunks", 0),
            "total_symbols": len(symbols),
            "classes_count": classes_count,
            "functions_count": functions_count,
            "extensions_distribution": ext_breakdown,
        }
