import re
from typing import Any, Dict, List, Optional


class DatabaseIntelligenceTool:
    """SQL query inspector for safety checks, destructive operation detection, and schema/index hints."""

    DESTRUCTIVE_RE = re.compile(r"\b(DROP\s+(TABLE|DATABASE|INDEX|VIEW)|TRUNCATE\s+TABLE)\b", re.IGNORECASE)
    UNBOUNDED_MUTATION_RE = re.compile(r"\b(DELETE\s+FROM|UPDATE\s+\w+\s+SET)\b(?![^\n;]*\bWHERE\b)", re.IGNORECASE)

    @classmethod
    def inspect_sql(cls, query: str, schema_info: Optional[str] = None) -> Dict[str, Any]:
        """Analyze a SQL statement for destructive patterns, missing WHERE clauses, and SELECT *."""
        q = query.strip()
        warnings: List[str] = []
        is_destructive = bool(cls.DESTRUCTIVE_RE.search(q))
        unbounded_write = bool(cls.UNBOUNDED_MUTATION_RE.search(q))

        if is_destructive:
            warnings.append("Destructive DDL statement (DROP/TRUNCATE) detected.")
        if unbounded_write:
            warnings.append("Write/Delete statement without a WHERE clause detected.")
        if re.search(r"\bSELECT\s+\*\s+FROM\b", q, re.IGNORECASE):
            warnings.append("SELECT * detected; consider projecting explicit columns.")

        referenced_tables: List[str] = []
        for match in re.finditer(r"\b(?:FROM|JOIN|UPDATE|INTO)\s+([A-Za-z_][A-Za-z0-9_.]*)", q, re.IGNORECASE):
            tbl = match.group(1)
            if tbl not in referenced_tables:
                referenced_tables.append(tbl)

        schema_mentions: List[str] = []
        if schema_info:
            for tbl in referenced_tables:
                base_tbl = tbl.split(".")[-1]
                if re.search(rf"\b{re.escape(base_tbl)}\b", schema_info, re.IGNORECASE):
                    schema_mentions.append(base_tbl)

        is_safe = not is_destructive and not unbounded_write
        return {
            "query": q,
            "is_safe_sql": is_safe,
            "is_destructive": is_destructive,
            "unbounded_write": unbounded_write,
            "referenced_tables": referenced_tables,
            "matched_in_schema": schema_mentions,
            "warnings": warnings,
        }

    @staticmethod
    def extract_table_names(sql_script: str) -> List[str]:
        """Extract unique table names referenced across one or more SQL statements."""
        tables: List[str] = []
        for match in re.finditer(r"\b(?:FROM|JOIN|UPDATE|INTO|TABLE)\s+([A-Za-z_][A-Za-z0-9_.]*)", sql_script, re.IGNORECASE):
            name = match.group(1)
            if name not in tables:
                tables.append(name)
        return tables

    @staticmethod
    def suggest_index_hints(query: str) -> List[str]:
        """Suggest basic index hints based on WHERE equality/IN filters and JOIN keys."""
        hints: List[str] = []
        for match in re.finditer(r"\bWHERE\s+([A-Za-z_][A-Za-z0-9_.]*)\s*(?:=|IN\b)", query, re.IGNORECASE):
            col = match.group(1)
            hints.append(f"Consider index on '{col}' for WHERE filtering.")
        for match in re.finditer(r"\bON\s+([A-Za-z_][A-Za-z0-9_.]*)\s*=\s*([A-Za-z_][A-Za-z0-9_.]*)", query, re.IGNORECASE):
            left_col, right_col = match.group(1), match.group(2)
            hints.append(f"Ensure join keys '{left_col}' and '{right_col}' are indexed.")
        return list(dict.fromkeys(hints))