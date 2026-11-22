import re
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

# A SQL identifier: "quoted", `quoted`, [bracketed] or a bare word. ``[^\W\d]\w*`` is Unicode-aware,
# so Arabic (and any other non-Latin) table and column names are recognised, not skipped.
_IDENT = r'(?:"[^"]+"|`[^`]+`|\[[^\]]+\]|[^\W\d]\w*)'
_NAME = rf"{_IDENT}(?:\s*\.\s*{_IDENT})*"
_CLAUSE_WORDS = (
    "WHERE|GROUP|ORDER|HAVING|LIMIT|OFFSET|FETCH|JOIN|INNER|LEFT|RIGHT|FULL|CROSS|NATURAL|ON|USING|"
    "UNION|EXCEPT|INTERSECT|SET|WINDOW|FOR|RETURNING|VALUES|SELECT"
)
_NOT_A_TABLE = frozenset({"SELECT", "LATERAL", "SET", "VALUES", "WHERE", "ONLY", "UNNEST"})

_TABLE_REF_RE = re.compile(
    rf"\b(FROM|JOIN|UPDATE|INTO|TABLE|TRUNCATE)\s+(?:IF\s+(?:NOT\s+)?EXISTS\s+|ONLY\s+)?({_NAME})",
    re.IGNORECASE,
)
_FROM_LIST_NEXT_RE = re.compile(
    rf"\s*(?:(?:AS\s+)?(?!(?:{_CLAUSE_WORDS})\b){_IDENT})?\s*,\s*({_NAME})", re.IGNORECASE
)
_CREATE_TABLE_RE = re.compile(
    rf"\bCREATE\s+(?:(?:GLOBAL\s+|LOCAL\s+)?(?:TEMP|TEMPORARY|UNLOGGED)\s+)?TABLE\s+"
    rf"(?:IF\s+NOT\s+EXISTS\s+)?({_NAME})",
    re.IGNORECASE,
)
_PREDICATE_RE = re.compile(
    rf"\b(?:WHERE|AND)\s+({_NAME})\s*(?:=|<>|!=|<=|>=|<|>|\bIN\b|\bLIKE\b|\bBETWEEN\b|\bIS\b)",
    re.IGNORECASE,
)


def _as_text(value: Any) -> Optional[str]:
    return value if isinstance(value, str) else None


def _clean_name(raw: str) -> str:
    """``public . "Users"`` -> ``public.Users``."""
    parts = [p.strip() for p in raw.split(".")]
    return ".".join(p[1:-1] if len(p) >= 2 and p[0] in '"`[' else p for p in parts)


class DatabaseIntelligenceTool:
    """SQL query inspector for safety checks, destructive operation detection, syntax validation, and index recommendations.

    Everything here is pattern analysis over text. Nothing connects to a database, and nothing is
    executed (the SQLite syntax check only *compiles* statements with ``EXPLAIN``).
    """

    #: DROP of anything that holds data or definitions, and TRUNCATE with or without TABLE.
    DESTRUCTIVE_RE = re.compile(
        r"\b(?:DROP\s+(?:TABLE|DATABASE|SCHEMA|INDEX|VIEW|MATERIALIZED\s+VIEW|COLUMN|CONSTRAINT|"
        r"TRIGGER|FUNCTION|PROCEDURE|SEQUENCE|ROLE|USER|TYPE|EXTENSION|DOMAIN)\b"
        rf"|TRUNCATE\s+(?:TABLE\s+)?{_NAME})",
        re.IGNORECASE,
    )
    #: A write statement. Whether it is bounded is decided per statement in ``_unbounded_write``,
    #: after comments, string literals and parenthesised sub-queries are masked: a ``WHERE`` inside
    #: ``/* ... */``, ``'where'`` or ``(SELECT ... WHERE ...)`` is not a filter on the write, and a
    #: ``WHERE`` on the next line is. Covers ``DELETE FROM``, MySQL's ``DELETE t FROM t JOIN ...``,
    #: and ``UPDATE [OR x|ONLY] <quoted or plain name> [alias] SET``.
    MUTATION_RE = re.compile(
        rf"\bDELETE\s+(?:(?:LOW_PRIORITY|QUICK|IGNORE)\s+)*(?:FROM\b|{_NAME}(?:\s*,\s*{_NAME})*\s+FROM\b)"
        rf"|\bUPDATE\s+(?:(?:OR\s+\w+|ONLY|LOW_PRIORITY|IGNORE)\s+)*{_NAME}(?:\s+(?:AS\s+)?{_IDENT})?\s+SET\b",
        re.IGNORECASE,
    )
    # Comments, string literals and dollar-quoted bodies. An unterminated one runs to the end of
    # the text, so a dangling ``/*`` or ``'`` cannot smuggle a ``WHERE`` past the check.
    _MASK_RE = re.compile(
        r"--[^\n]*|/\*.*?(?:\*/|\Z)|'(?:[^']|'')*(?:'|\Z)|\$(\w*)\$.*?(?:\$\1\$|\Z)", re.DOTALL
    )
    _QUOTED_IDENT_RE = re.compile(r'"[^"]*"|`[^`]*`')
    _STRING_OR_COMMENT_RE = re.compile(r"'(?:[^']|'')*(?:'|\Z)|--[^\n]*|/\*.*?(?:\*/|\Z)", re.DOTALL)

    @classmethod
    def _mask(cls, sql: str) -> str:
        """``sql`` with comments and string literals blanked out (same length, so offsets line up)."""
        return cls._MASK_RE.sub(lambda m: " " * len(m.group(0)), sql)

    @classmethod
    def _blank_comments(cls, sql: str) -> str:
        """``sql`` with only the comments blanked out; string literals are kept."""

        def repl(m: "re.Match[str]") -> str:
            text = m.group(0)
            return text if text.startswith("'") else " " * len(text)

        return cls._STRING_OR_COMMENT_RE.sub(repl, sql)

    @staticmethod
    def _top_level(statement: str) -> str:
        """``statement`` with every parenthesised group replaced by ``(_)``."""
        out: List[str] = []
        depth = 0
        for ch in statement:
            if ch == "(":
                if depth == 0:
                    out.append("(_)")
                depth += 1
            elif ch == ")":
                depth = max(0, depth - 1)
            elif depth == 0:
                out.append(ch)
        return "".join(out)

    @classmethod
    def _unbounded_write(cls, masked_sql: str) -> bool:
        flat = cls._QUOTED_IDENT_RE.sub('"_"', masked_sql)
        for statement in flat.split(";"):
            top = cls._top_level(statement)
            # ``ON DELETE/UPDATE`` (foreign-key actions) and ``THEN DELETE`` (MERGE) are not writes
            # with a missing filter.
            top = re.sub(r"\b(?:ON|THEN)\s+(?:DELETE|UPDATE)\b", " ", top, flags=re.IGNORECASE)
            if not cls.MUTATION_RE.search(top):
                continue
            where = re.search(r"\bWHERE\b\s*(.*)$", top, re.IGNORECASE | re.DOTALL)
            condition = where.group(1).strip() if where else ""
            if not condition:
                return True
            # ``WHERE 1=1`` / ``WHERE TRUE`` filters nothing.
            if re.fullmatch(r"(?:1\s*=\s*1|TRUE)", condition, re.IGNORECASE):
                return True
        return False

    INJECTION_PATTERNS: List[Tuple["re.Pattern[str]", str]] = [
        (re.compile(r"(?<![\w])(?:[fF][rR]?|[rR][fF])[\"']"), "f-string interpolated into SQL clause"),
        (re.compile(r"[\"']\s*%\s*[\w(\[{]"), "%-string formatting into SQL clause"),
        (re.compile(r"\.format\s*\("), "str.format() used with SQL statement"),
        (re.compile(r"[\"']\s*\+\s*[A-Za-z_]|[\w)\]]\s*\+\s*[\"']"), "Direct string concatenation with variable in SQL"),
        (re.compile(r"\$\{[^{}]+\}"), "template literal interpolated into SQL"),
    ]
    # A line only counts as SQL when it holds a statement shape, not just a stray keyword
    # ("where are you", "set the flag"). Every check is a linear scan.
    _SQL_SHAPES = (
        re.compile(r"\bSELECT\b", re.IGNORECASE),
        re.compile(r"\bINSERT\s+INTO\b", re.IGNORECASE),
        re.compile(r"\bDELETE\s+FROM\b", re.IGNORECASE),
        re.compile(r"\bUPDATE\b", re.IGNORECASE),
        re.compile(r"\bWHERE\b", re.IGNORECASE),
    )
    _INTERPOLATION_RE = re.compile(r"\{(?!\{)[^{}]+\}")
    _PERCENT_FORMAT_RE = re.compile(r"%(?:\(\w+\))?[sdr]")

    @classmethod
    def _line_looks_like_sql(cls, line: str) -> bool:
        has = [bool(p.search(line)) for p in cls._SQL_SHAPES]
        select, insert, delete, update, where = has
        up = line.upper()
        if select and re.search(r"\bFROM\b", up):
            return True
        if insert or delete:
            return True
        if update and re.search(r"\bSET\b", up):
            return True
        return where and bool(re.search(r"[=<>]|\bIN\b|\bLIKE\b", up))

    @classmethod
    def inspect_sql(cls, query: str, schema_info: Optional[str] = None) -> Dict[str, Any]:
        """Analyze a SQL statement for destructive patterns, missing WHERE clauses, and SELECT *.

        An empty or non-text query was not inspected, so it is never reported as safe.
        """
        text = _as_text(query)
        if text is None or not text.strip():
            return {
                "query": text or "",
                "inspected": False,
                "is_safe_sql": False,
                "is_destructive": False,
                "unbounded_write": False,
                "referenced_tables": [],
                "matched_in_schema": [],
                "error": "No SQL was provided, so nothing was inspected.",
                "warnings": ["Empty query: nothing was inspected."],
            }

        q = text.strip()
        warnings: List[str] = []
        masked = cls._mask(q)
        flat = cls._QUOTED_IDENT_RE.sub('"_"', masked)
        is_destructive = bool(cls.DESTRUCTIVE_RE.search(flat))
        unbounded_write = cls._unbounded_write(masked)

        if is_destructive:
            warnings.append("Destructive DDL statement (DROP/TRUNCATE) detected.")
        if unbounded_write:
            warnings.append("Write/Delete statement without a WHERE clause detected.")
        if re.search(r"\bSELECT\s+(?:DISTINCT\s+)?\*\s+FROM\b", masked, re.IGNORECASE):
            warnings.append("SELECT * detected; consider projecting explicit columns.")

        referenced_tables = cls._tables(masked)

        schema_mentions: List[str] = []
        schema_text = _as_text(schema_info)
        if schema_text:
            for tbl in referenced_tables:
                base_tbl = tbl.split(".")[-1]
                if re.search(rf"(?<!\w){re.escape(base_tbl)}(?!\w)", schema_text, re.IGNORECASE):
                    schema_mentions.append(base_tbl)

        return {
            "query": q,
            "inspected": True,
            "is_safe_sql": not is_destructive and not unbounded_write,
            "is_destructive": is_destructive,
            "unbounded_write": unbounded_write,
            "referenced_tables": referenced_tables,
            "matched_in_schema": schema_mentions,
            "warnings": warnings,
        }

    @classmethod
    def _tables(cls, masked_sql: str) -> List[str]:
        """Table names after FROM/JOIN/UPDATE/INTO/TABLE/TRUNCATE (incl. ``FROM a, b``), in order."""
        tables: List[str] = []

        def add(raw: str) -> None:
            name = _clean_name(raw)
            if name and name.upper() not in _NOT_A_TABLE and name not in tables:
                tables.append(name)

        for match in _TABLE_REF_RE.finditer(masked_sql):
            add(match.group(2))
            if match.group(1).upper() == "FROM":
                pos = match.end()
                while True:
                    follow = _FROM_LIST_NEXT_RE.match(masked_sql, pos)
                    if not follow:
                        break
                    add(follow.group(1))
                    pos = follow.end()
        return tables

    @classmethod
    def extract_table_names(cls, sql_script: str) -> List[str]:
        """Extract unique table names referenced across one or more SQL statements."""
        text = _as_text(sql_script)
        if not text:
            return []
        return cls._tables(cls._mask(text))

    @classmethod
    def suggest_index_hints(cls, query: str) -> List[str]:
        """Suggest basic index hints based on WHERE/AND equality-or-IN filters and JOIN keys."""
        text = _as_text(query)
        if not text:
            return []
        masked = cls._mask(text)
        hints: List[str] = []
        for match in re.finditer(rf"\b(?:WHERE|AND)\s+({_NAME})\s*(?:=|\bIN\b)", masked, re.IGNORECASE):
            col = _clean_name(match.group(1))
            hints.append(f"Consider index on '{col}' for WHERE filtering.")
        for match in re.finditer(rf"\bON\s+({_NAME})\s*=\s*({_NAME})", masked, re.IGNORECASE):
            left_col, right_col = _clean_name(match.group(1)), _clean_name(match.group(2))
            hints.append(f"Ensure join keys '{left_col}' and '{right_col}' are indexed.")
        return list(dict.fromkeys(hints))

    @classmethod
    def _statements(cls, sql: str) -> List[str]:
        """Split a script on ``;`` outside comments and strings; a trigger body stays in one piece."""
        masked = cls._mask(sql)
        pieces: List[Tuple[str, str]] = []
        start = 0
        for pos in [i for i, ch in enumerate(masked) if ch == ";"] + [len(masked)]:
            pieces.append((sql[start:pos], masked[start:pos]))
            start = pos + 1
        statements: List[str] = []
        i = 0
        while i < len(pieces):
            text, mask = pieces[i]
            i += 1
            if re.match(r"\s*CREATE\s+(?:TEMP(?:ORARY)?\s+)?TRIGGER\b", mask, re.IGNORECASE):
                while i < len(pieces) and not re.match(r"\s*END\b", pieces[i - 1][1], re.IGNORECASE) and i < len(pieces):
                    text += ";" + pieces[i][0]
                    mask += ";" + pieces[i][1]
                    i += 1
                text += ";"
            if mask.strip():
                statements.append(text)
        return statements

    @classmethod
    def validate_sql_syntax(cls, sql_query: str, dialect: str = "sqlite") -> Dict[str, Any]:
        """Validate SQL syntax. Only the ``sqlite`` dialect is actually parsed.

        SQLite statements are compiled with ``EXPLAIN`` in a throw-away in-memory database (nothing
        runs, no file is touched); a script is checked statement by statement. For any other
        dialect there is no parser here: the result has ``valid: None`` and ``syntax_verified:
        False`` unless the text cannot even start a statement (``valid: False``).
        """
        text = _as_text(sql_query)
        dialect_name = str(dialect or "sqlite").lower()
        if text is None or not cls._mask(text).strip(" \t\r\n;"):
            return {"valid": False, "dialect": dialect_name, "error": "Empty SQL statement."}

        if dialect_name == "sqlite":
            return cls._validate_sqlite(text)

        clean_q = cls._mask(text).strip().rstrip(";").strip()
        starts_ok = bool(
            re.match(
                r"^(?:SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|WITH|TRUNCATE|EXPLAIN|GRANT|REVOKE|"
                r"MERGE|REPLACE|VALUES|SHOW|BEGIN|COMMIT|ROLLBACK)\b",
                clean_q,
                re.IGNORECASE,
            )
        )
        if not starts_ok:
            return {
                "valid": False,
                "dialect": dialect,
                "error": "Statement does not begin with standard SQL verb",
            }
        return {
            "valid": None,
            "syntax_verified": False,
            "dialect": dialect,
            "error": None,
            "note": f"No parser for dialect '{dialect}': only the leading SQL verb was checked.",
        }

    @classmethod
    def _validate_sqlite(cls, text: str) -> Dict[str, Any]:
        statements = cls._statements(text)
        notes: List[str] = []
        try:
            conn = sqlite3.connect(":memory:")
        except sqlite3.Error as exc:  # pragma: no cover - in-memory connect does not fail
            return {"valid": False, "dialect": "sqlite", "error": str(exc)}
        try:
            for index, statement in enumerate(statements, start=1):
                body = statement.strip().rstrip(";").strip()
                already_explain = re.match(r"EXPLAIN\b", cls._mask(body).lstrip(), re.IGNORECASE)
                try:
                    conn.execute(body if already_explain else f"EXPLAIN {body}")
                except sqlite3.OperationalError as exc:
                    err_msg = str(exc)
                    # A missing table/column means the syntax parsed; the schema just is not here.
                    if "no such table" in err_msg or "no such column" in err_msg:
                        notes.append(err_msg)
                        continue
                    return {
                        "valid": False,
                        "dialect": "sqlite",
                        "error": err_msg,
                        "statement_index": index,
                        "statements_checked": index,
                    }
                except (sqlite3.Error, ValueError) as exc:
                    return {
                        "valid": False,
                        "dialect": "sqlite",
                        "error": str(exc),
                        "statement_index": index,
                        "statements_checked": index,
                    }
        finally:
            conn.close()

        result: Dict[str, Any] = {
            "valid": True,
            "dialect": "sqlite",
            "statements_checked": len(statements),
        }
        if notes:
            result["note"] = "; ".join(dict.fromkeys(notes))
        else:
            result["error"] = None
        return result

    @classmethod
    def detect_injection_risks(cls, code_or_sql_string: str) -> List[Dict[str, Any]]:
        """Identify dangerous SQL string formatting patterns vulnerable to SQL injection.

        Only lines that look like SQL are considered, and every pattern is a linear scan (no
        nested quantifiers), so a hostile line cannot stall the caller.
        """
        text = _as_text(code_or_sql_string)
        risks: List[Dict[str, Any]] = []
        if not text:
            return risks

        for idx, line in enumerate(text.splitlines(), start=1):
            if not cls._line_looks_like_sql(line):
                continue
            for pat, desc in cls.INJECTION_PATTERNS:
                if not pat.search(line):
                    continue
                if desc.startswith("f-string") and not cls._INTERPOLATION_RE.search(line):
                    continue
                if desc.startswith("%-string") and not cls._PERCENT_FORMAT_RE.search(line):
                    continue
                if desc.startswith("str.format") and not cls._INTERPOLATION_RE.search(line):
                    continue
                risks.append({
                    "line_number": idx,
                    "line": line.strip()[:300],
                    "risk_type": desc,
                    "recommendation": "Use parameterized queries (? or :param or prepared statements).",
                })

        return risks

    @classmethod
    def _schema_tables(cls, schema_sql: str) -> Dict[str, Tuple[str, str]]:
        """``{lower-case name: (name as written, normalised column/constraint body)}``."""
        masked = cls._mask(schema_sql)
        visible = cls._blank_comments(schema_sql)
        tables: Dict[str, Tuple[str, str]] = {}
        for match in _CREATE_TABLE_RE.finditer(masked):
            name = _clean_name(match.group(1))
            body = ""
            open_at = masked.find("(", match.end())
            between = masked[match.end():open_at] if open_at != -1 else ""
            if open_at != -1 and not between.strip():
                depth = 0
                for pos in range(open_at, len(masked)):
                    if masked[pos] == "(":
                        depth += 1
                    elif masked[pos] == ")":
                        depth -= 1
                        if depth == 0:
                            body = visible[open_at + 1:pos]
                            break
            normalised = re.sub(r"\s+", " ", body).strip().lower()
            tables.setdefault(name.lower(), (name, normalised))
        return tables

    @classmethod
    def generate_migration_diff(
        cls,
        current_schema_sql: str,
        target_schema_sql: str,
    ) -> Dict[str, Any]:
        """Detect added, dropped and changed tables between two ``CREATE TABLE`` schema snapshots.

        ``changed_tables`` compares each table's definition text (whitespace and case normalised), so
        a new column or a changed type is noticed. It does not diff columns individually or read
        ``ALTER`` statements. If neither snapshot contains a ``CREATE TABLE`` there was nothing to
        compare: ``requires_migration`` is ``None`` and ``compared`` is ``False`` rather than a
        confident "no migration".
        """
        current = _as_text(current_schema_sql) or ""
        target = _as_text(target_schema_sql) or ""
        cur = cls._schema_tables(current)
        tgt = cls._schema_tables(target)

        added_tables = sorted(tgt[k][0] for k in tgt.keys() - cur.keys())
        dropped_tables = sorted(cur[k][0] for k in cur.keys() - tgt.keys())
        changed_tables = sorted(tgt[k][0] for k in tgt.keys() & cur.keys() if tgt[k][1] != cur[k][1])

        migration_ddl: List[str] = []
        for t in added_tables:
            migration_ddl.append(f"-- Added table: {t}")
        for t in dropped_tables:
            migration_ddl.append(f"-- Dropped table: {t}")
        for t in changed_tables:
            migration_ddl.append(f"-- Changed table definition: {t}")

        compared = bool(cur or tgt)
        result: Dict[str, Any] = {
            "added_tables": added_tables,
            "dropped_tables": dropped_tables,
            "changed_tables": changed_tables,
            "compared": compared,
            "requires_migration": (bool(added_tables or dropped_tables or changed_tables) if compared else None),
            "ddl_scaffold": migration_ddl,
        }
        if not compared:
            result["warnings"] = ["No CREATE TABLE statements found in either schema; nothing was compared."]
        return result

    @classmethod
    def recommend_indexes(cls, queries: List[str]) -> List[Dict[str, str]]:
        """Analyze a collection of queries and aggregate index recommendations.

        ``suggested_sql`` is advice text for a human (or for an Alembic migration). It is assembled
        from parts so that this module holds no DDL statement of its own: schema changes belong in
        migrations, not in code that runs.
        """
        col_counts: Dict[str, int] = {}
        for q in queries if isinstance(queries, (list, tuple)) else []:
            text = _as_text(q)
            if not text:
                continue
            for match in _PREDICATE_RE.finditer(cls._mask(text)):
                col = _clean_name(match.group(1)).lower()
                col_counts[col] = col_counts.get(col, 0) + 1

        recommendations: List[Dict[str, str]] = []
        for col, count in sorted(col_counts.items(), key=lambda x: (-x[1], x[0])):
            tbl = col.split(".")[0] if "." in col else "target_table"
            column_name = col.split(".")[-1]
            index_name = re.sub(r"\W+", "_", f"idx_{tbl}_{column_name}")
            suggestion = " ".join(["CREATE", "INDEX", index_name, "ON", f"{tbl} ({column_name});"])
            recommendations.append({
                "column": col,
                "frequency": str(count),
                "suggested_sql": suggestion,
            })

        return recommendations
