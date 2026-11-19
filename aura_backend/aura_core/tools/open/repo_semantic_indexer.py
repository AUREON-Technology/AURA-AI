import ast
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


class RepoSemanticIndexer:
    """Semantic repository indexer for scanning files, generating chunk embeddings/windows, and symbol search."""

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

    @staticmethod
    def chunk_file_for_indexing(
        content: str,
        chunk_size_lines: int = 40,
        overlap: int = 10,
    ) -> List[Dict[str, Any]]:
        """Split source text into overlapping chunks with line coordinates for semantic retrieval."""
        lines = content.splitlines()
        if not lines:
            return []

        chunks: List[Dict[str, Any]] = []
        start = 0
        step = max(1, chunk_size_lines - overlap)

        while start < len(lines):
            end = min(len(lines), start + chunk_size_lines)
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
        """Rank repository files by term matching score."""
        tokens = [t.lower() for t in re.findall(r"\b[A-Za-z0-9_]{2,}\b", query)]
        if not tokens:
            return []

        root_str = workspace_root or index_result.get("root", "")
        root_path = Path(root_str) if root_str else None
        files = index_result.get("files", [])
        scored_files: List[Dict[str, Any]] = []

        for f_info in files:
            rel = f_info.get("file", "")
            score = 0.0

            # Match in filename
            for tok in tokens:
                if tok in rel.lower():
                    score += 5.0

            # Match in file content if root available
            if root_path:
                fp = root_path / rel
                if fp.exists() and fp.is_file():
                    try:
                        text = fp.read_text(encoding="utf-8", errors="ignore").lower()
                        for tok in tokens:
                            score += min(10.0, text.count(tok) * 0.5)
                    except OSError:
                        pass

            if score > 0:
                scored_files.append({"file": rel, "relevance_score": round(score, 2), "lines": f_info.get("lines", 0)})

        scored_files.sort(key=lambda x: x["relevance_score"], reverse=True)
        return scored_files[:max_results]

    @staticmethod
    def get_index_statistics(index_result: Dict[str, Any]) -> Dict[str, Any]:
        """Generate statistical aggregation of repository contents."""
        files = index_result.get("files", [])
        symbols = index_result.get("symbols", {})

        ext_breakdown: Dict[str, int] = {}
        total_lines = 0

        for f in files:
            name = f.get("file", "")
            ext = "." + name.split(".")[-1] if "." in name else "no_ext"
            ext_breakdown[ext] = ext_breakdown.get(ext, 0) + 1
            total_lines += f.get("lines", 0)

        classes_count = sum(1 for s in symbols.values() if s.get("type") == "class")
        functions_count = sum(1 for s in symbols.values() if s.get("type") == "function")

        return {
            "total_files": len(files),
            "total_lines": total_lines,
            "total_chunks": index_result.get("total_chunks", 0),
            "total_symbols": len(symbols),
            "classes_count": classes_count,
            "functions_count": functions_count,
            "extensions_distribution": ext_breakdown,
        }
