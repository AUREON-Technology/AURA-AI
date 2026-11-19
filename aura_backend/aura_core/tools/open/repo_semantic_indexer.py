import ast
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


class RepoSemanticIndexer:
    """Semantic repository indexer for scanning files, line chunks, and Python symbols."""

    DEFAULT_EXTENSIONS: Set[str] = {".py", ".md", ".json", ".yaml", ".yml", ".dart", ".ts", ".js", ".toml"}
    IGNORED_DIRS: Set[str] = {".git", "__pycache__", "venv", ".venv", "node_modules", ".pytest_cache", "dist", "build"}

    @classmethod
    def index_repository(
        cls,
        root_path: str,
        extensions: Optional[List[str]] = None,
        chunk_lines: int = 50,
        max_files: int = 1000,
    ) -> Dict[str, Any]:
        """Scan a repository directory and extract file metadata, chunks, and symbols."""
        root = Path(root_path)
        if not root.exists() or not root.is_dir():
            return {"status": "error", "error": f"Repository path not found: {root_path}"}

        allowed_exts = {ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in extensions} if extensions else cls.DEFAULT_EXTENSIONS
        step = max(1, chunk_lines)

        indexed_files: List[Dict[str, Any]] = []
        symbol_index: Dict[str, Dict[str, Any]] = {}
        total_chunks = 0
        scanned = 0

        for path in root.rglob("*"):
            if any(part in cls.IGNORED_DIRS for part in path.parts):
                continue
            if not path.is_file() or path.suffix.lower() not in allowed_exts:
                continue

            scanned += 1
            if scanned > max_files:
                break

            rel_path = path.relative_to(root).as_posix()
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue

            lines = content.splitlines()
            chunks_count = max(1, (len(lines) + step - 1) // step) if lines else 1
            total_chunks += chunks_count
            indexed_files.append({
                "file": rel_path,
                "lines": len(lines),
                "chunks": chunks_count,
            })

            if path.suffix.lower() == ".py":
                try:
                    tree = ast.parse(content)
                    for node in ast.walk(tree):
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                            kind = "class" if isinstance(node, ast.ClassDef) else "function"
                            symbol_index[node.name] = {
                                "type": kind,
                                "file": rel_path,
                                "line": getattr(node, "lineno", 1),
                            }
                except SyntaxError:
                    continue

        return {
            "status": "success",
            "root": str(root),
            "indexed_files_count": len(indexed_files),
            "total_chunks": total_chunks,
            "symbols_indexed": len(symbol_index),
            "files": indexed_files,
            "symbols": symbol_index,
        }

    @staticmethod
    def search_symbols(index_result: Dict[str, Any], query: str) -> List[Dict[str, Any]]:
        """Filter indexed symbols by case-insensitive substring match on symbol name."""
        q = query.strip().lower()
        if not q:
            return []
        symbols = index_result.get("symbols", {})
        matches: List[Dict[str, Any]] = []
        for sym_name, info in symbols.items():
            if q in sym_name.lower():
                matches.append({"name": sym_name, **info})
        return matches