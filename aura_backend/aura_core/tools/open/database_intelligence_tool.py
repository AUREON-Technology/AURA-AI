import re
import sqlite3
from typing import Any, Dict, List, Optional, Set


class DatabaseIntelligenceTool:
    """SQL query inspector for safety checks, destructive operation detection, syntax validation, and index recommendations."""

    DESTRUCTIVE_RE = re.compile(r"\b(DROP\s+(TABLE|DATABASE|INDEX|VIEW)|TRUNCATE\s+TABLE)\b", re.IGNORECASE)
    #: A write statement. Whether it is bounded is decided per statement in ``_unbounded_write``,
    #: after comments and string literals are masked: a ``WHERE`` inside ``/* ... */`` or ``'where'``
    #: is not a filter, and a ``WHERE`` on the next line is.
    MUTATION_RE = re.compile(r"\b(?:DELETE\s+FROM|UPDATE\s+[\w.\"`\[\]]+\s+SET)\b", re.IGNORECASE)
    _MASK_RE = re.compile(r"--[^\n]*|/\*.*?\*/|'(?:[^']|'')*'", re.DOTALL)

    @classmethod
    def _mask(cls, sql: str) -> str:
        """``sql`` with comments and single-quoted string literals blanked out."""
        return cls._MASK_RE.sub(" ", sql)

    @classmethod
    def _unbounded_write(cls, masked_sql: str) -> bool:
        for statement in masked_sql.split(";"):
            if cls.MUTATION_RE.search(statement) and not re.search(r"\bWHERE\b", statement, re.IGNORECASE):
                return True
        return False

    INJECTION_PATTERNS = [
        (re.compile(r"(?:f['\"].*?(?:WHERE|AND|OR|VALUES|SET).*?\{[^\}]+\})", re.DOTALL | re.IGNORECASE), "f-string interpolated into SQL clause"),
        (re.compile(r"(?:['\"][^\"]*?(?:WHERE|AND|OR|VALUES|SET)[^\"]*?%[sdr])", re.IGNORECASE), "%-string formatting into SQL clause"),
        (re.compile(r"\.format\s*\([^\)]+\)\s*(?:;|\)|\])?"), "str.format() used with SQL statement"),
        (re.compile(r"\+\s*[A-Za-z_][A-Za-z0-9_]*\s*\+"), "Direct string concatenation with variable in SQL"),
    ]

    @classmethod
    def inspect_sql(cls, query: str, schema_info: Optional[str] = None) -> Dict[str, Any]:
        """Analyze a SQL statement for destructive patterns, missing WHERE clauses, and SELECT *."""
        q = query.strip()
        warnings: List[str] = []
        masked = cls._mask(q)
        is_destructive = bool(cls.DESTRUCTIVE_RE.search(masked))
        unbounded_write = cls._unbounded_write(masked)

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

    @classmethod
    def validate_sql_syntax(cls, sql_query: str, dialect: str = "sqlite") -> Dict[str, Any]:
        """Validate SQL statement syntax using in-memory SQLite engine for sqlite dialect."""
        clean_q = sql_query.strip().rstrip(";")
        if dialect.lower() == "sqlite":
            try:
                conn = sqlite3.connect(":memory:")
                cur = conn.cursor()
                cur.execute(f"EXPLAIN {clean_q}")
                conn.close()
                return {"valid": True, "dialect": "sqlite", "error": None}
            except sqlite3.OperationalError as exc:
                err_msg = str(exc)
                # If error is merely table not found, syntax itself is valid!
                if "no such table" in err_msg or "no such column" in err_msg:
                    return {"valid": True, "dialect": "sqlite", "note": err_msg}
                return {"valid": False, "dialect": "sqlite", "error": err_msg}
            except Exception as exc:
                return {"valid": False, "dialect": "sqlite", "error": str(exc)}

        # Generic syntax check for postgres / mysql keywords
        has_select_or_mutation = bool(re.match(r"^(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP)\b", clean_q, re.I))
        return {
            "valid": has_select_or_mutation,
            "dialect": dialect,
            "error": None if has_select_or_mutation else "Statement does not begin with standard SQL verb",
        }

    @classmethod
    def detect_injection_risks(cls, code_or_sql_string: str) -> List[Dict[str, Any]]:
        """Identify dangerous SQL string formatting patterns vulnerable to SQL injection."""
        risks: List[Dict[str, Any]] = []
        lines = code_or_sql_string.splitlines()

        for idx, line in enumerate(lines, start=1):
            for pat, desc in cls.INJECTION_PATTERNS:
                if pat.search(line):
                    risks.append({
                        "line_number": idx,
                        "line": line.strip(),
                        "risk_type": desc,
                        "recommendation": "Use parameterized queries (? or :param or prepared statements).",
                    })

        return risks

    @classmethod
    def generate_migration_diff(
        cls,
        current_schema_sql: str,
        target_schema_sql: str,
    ) -> Dict[str, Any]:
        """Detect added or dropped tables between two schema snapshots."""
        cur_tables = set(cls.extract_table_names(current_schema_sql))
        tgt_tables = set(cls.extract_table_names(target_schema_sql))

        added_tables = list(tgt_tables - cur_tables)
        dropped_tables = list(cur_tables - tgt_tables)

        migration_ddl: List[str] = []
        for t in added_tables:
            migration_ddl.append(f"-- Added table: {t}")
        for t in dropped_tables:
            migration_ddl.append(f"-- Dropped table: {t}")

        return {
            "added_tables": added_tables,
            "dropped_tables": dropped_tables,
            "requires_migration": len(added_tables) > 0 or len(dropped_tables) > 0,
            "ddl_scaffold": migration_ddl,
        }

    @classmethod
    def recommend_indexes(cls, queries: List[str]) -> List[Dict[str, str]]:
        """Analyze a collection of queries and aggregate index recommendations."""
        col_counts: Dict[str, int] = {}
        for q in queries:
            for match in re.finditer(r"\bWHERE\s+([A-Za-z_][A-Za-z0-9_.]*)", q, re.I):
                col = match.group(1).lower()
                col_counts[col] = col_counts.get(col, 0) + 1

        recommendations: List[Dict[str, str]] = []
        for col, count in sorted(col_counts.items(), key=lambda x: x[1], reverse=True):
            tbl = col.split(".")[0] if "." in col else "target_table"
            column_name = col.split(".")[-1]
            recommendations.append({
                "column": col,
                "frequency": str(count),
                "suggested_sql": f"CREATE INDEX idx_{tbl}_{column_name} ON {tbl} ({column_name});",
            })

        return recommendations
