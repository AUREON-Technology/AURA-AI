import ast
import re
from typing import Any, Dict, List, Optional


#: Words Mermaid treats as syntax when they appear as a bare id.
_RESERVED_WORDS = frozenset({
    "end", "graph", "flowchart", "subgraph", "class", "classdef", "style", "linkstyle", "click",
    "default", "participant", "actor", "note", "loop", "alt", "opt", "par", "rect", "direction",
})


class MermaidDiagramGenerator:
    """Mermaid diagram generator for code architecture, classes, flows, sequences, and schemas."""

    #: Every sequence-diagram arrow Mermaid defines (solid/dotted, open/filled, cross, async,
    #: bidirectional), matched whole so nothing else can ride along into the diagram.
    SEQUENCE_ARROW_PATTERN = r"<<-->>|<<->>|-->>|->>|-->|->|--x|-x|--\)|-\)"
    #: An ER relationship: left cardinality, solid (``--``) or dotted (``..``) line, right
    #: cardinality -- e.g. ``||--o{``, ``}o..o{``, ``||--||``.
    ER_CARDINALITY_PATTERN = r"(?:\|o|\|\||\}o|\}\|)(?:--|\.\.)(?:o\||\|\||o\{|\|\{)"

    @staticmethod
    def sanitize_identifier(name: Optional[str]) -> str:
        """Normalize an identifier so it is safe inside Mermaid diagrams; returns '' if empty.

        Underscores are kept as written, so distinct Python names (``_Foo`` and ``Foo``,
        ``__init__`` and ``init``) stay distinct nodes. A name with no letter or digit at all
        (``"..."``) has nothing to identify it and becomes ''.
        """
        if not name or not str(name).strip():
            return ""
        # ``\w`` is Unicode-aware: Arabic, CJK and accented names stay readable and distinct.
        # ASCII-only matching turned every such name into "___" (and, with the empty-name rule
        # above, dropped the node entirely).
        cleaned = re.sub(r"[^\w]", "_", str(name).strip())
        if not re.search(r"[^\W_]", cleaned):
            return ""
        if cleaned[0].isdigit():
            cleaned = f"N_{cleaned}"
        if cleaned.lower() in _RESERVED_WORDS:
            cleaned = f"n_{cleaned}"  # an id of ``end`` terminates a flowchart subgraph
        return cleaned

    class _Ids:
        """Raw name -> unique id within ONE diagram, so ``a-b`` and ``a_b`` stay two nodes."""

        def __init__(self) -> None:
            self._by_raw: Dict[str, str] = {}
            self._taken: set = set()

        def get(self, raw: Any) -> str:
            base = MermaidDiagramGenerator.sanitize_identifier(raw)
            if not base:
                return ""
            key = str(raw).strip()
            if key in self._by_raw:
                return self._by_raw[key]
            candidate, n = base, 2
            while candidate in self._taken:
                candidate = f"{base}_{n}"
                n += 1
            self._taken.add(candidate)
            self._by_raw[key] = candidate
            return candidate

    @staticmethod
    def escape_label(text: str) -> str:
        """Escape quotes and line breaks for safe Mermaid labels."""
        return (
            str(text).replace('"', "'").replace("{", "(").replace("}", ")")
            .replace("\r", " ").replace("\n", " ").strip()[:200]
        )

    @staticmethod
    def _sanitize_token(value: Optional[str], allowed_pattern: str, fallback: str) -> str:
        """Sanitize structural tokens like arrows or cardinalities to prevent line injection."""
        if not value:
            return fallback
        cleaned = str(value).replace("\r", "").replace("\n", "").strip()
        if re.fullmatch(allowed_pattern, cleaned):
            return cleaned
        return fallback

    @classmethod
    def extract_structure_from_source(cls, source_code: str) -> Dict[str, Any]:
        """Extract classes, methods, inheritance, and module imports from Python source via AST."""
        try:
            tree = ast.parse(source_code)
        except SyntaxError as exc:
            return {
                "valid_syntax": False,
                "error": f"SyntaxError: {exc}",
                "classes": [],
                "relations": [],
                "imports": [],
            }
        except (ValueError, RecursionError, MemoryError) as exc:
            return {
                "valid_syntax": False,
                "error": f"Source code could not be parsed: {type(exc).__name__}",
                "classes": [],
                "relations": [],
                "imports": [],
            }

        classes: List[Dict[str, Any]] = []
        relations: List[Dict[str, str]] = []
        imports: List[str] = []

        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                methods: List[str] = []
                attributes: List[str] = []
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        prefix = "+" if not item.name.startswith("_") else "-"
                        args = [a.arg for a in item.args.args if a.arg != "self"]
                        methods.append(f"{prefix}{item.name}({', '.join(args)})")
                    elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                        ann = ast.unparse(item.annotation) if item.annotation else "Any"
                        attributes.append(f"+{item.target.id}: {ann}")

                classes.append({
                    "name": node.name,
                    "attributes": attributes,
                    "methods": methods,
                })

                for base in node.bases:
                    base_name = ast.unparse(base)
                    relations.append({
                        "source": base_name,
                        "target": node.name,
                        "type": "<|--",
                        "label": "inherits",
                    })

            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module)

        return {
            "valid_syntax": True,
            "classes": classes,
            "relations": relations,
            "imports": list(dict.fromkeys(imports)),
        }

    @classmethod
    def generate_class_diagram(
        cls,
        class_names: Optional[List[str]] = None,
        relations: Optional[List[Dict[str, str]]] = None,
        class_details: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Generate a Mermaid classDiagram from class names, optional details, and relations."""
        lines: List[str] = ["classDiagram"]
        seen_classes = set()
        ids = cls._Ids()

        for cname in class_names or []:
            safe_name = ids.get(cname)
            if safe_name and safe_name not in seen_classes:
                lines.append(f"    class {safe_name}")
                seen_classes.add(safe_name)

        for detail in class_details or []:
            if not isinstance(detail, dict):
                continue
            raw_name = detail.get("name", "")
            safe_name = ids.get(raw_name)
            if not safe_name:
                continue
            seen_classes.add(safe_name)
            members: List[str] = []
            for attr in detail.get("attributes") or []:
                members.append(f"        {cls.escape_label(str(attr))}")
            for method in detail.get("methods") or []:
                members.append(f"        {cls.escape_label(str(method))}")
            if members:
                lines.append(f"    class {safe_name} {{")
                lines.extend(members)
                lines.append("    }")
            else:
                lines.append(f"    class {safe_name}")

        for rel in relations or []:
            if not isinstance(rel, dict):
                continue
            src = ids.get(rel.get("source", ""))
            tgt = ids.get(rel.get("target", ""))
            if not src or not tgt:
                continue
            rel_type = cls._sanitize_token(rel.get("type"), r"[<>|\-o.*()]+", "-->")
            label = rel.get("label")
            if label:
                lines.append(f"    {src} {rel_type} {tgt} : {cls.escape_label(str(label))}")
            else:
                lines.append(f"    {src} {rel_type} {tgt}")

        return "\n".join(lines)

    @classmethod
    def generate_class_diagram_from_source(cls, source_code: str) -> Dict[str, Any]:
        """Parse Python source code and generate a Mermaid classDiagram."""
        structure = cls.extract_structure_from_source(source_code)
        if not structure["valid_syntax"]:
            return {
                "generated": False,
                "error": structure["error"],
                "mermaid": "",
            }

        diagram = cls.generate_class_diagram(
            relations=structure["relations"],
            class_details=structure["classes"],
        )
        return {
            "generated": True,
            "classes_count": len(structure["classes"]),
            "relations_count": len(structure["relations"]),
            "mermaid": diagram,
        }

    @classmethod
    def generate_flowchart(
        cls,
        nodes: List[Dict[str, str]],
        edges: List[Dict[str, str]],
        direction: str = "TD",
    ) -> str:
        """Generate a Mermaid flowchart (nodes: id, label, shape; edges: from, to, label)."""
        valid_dirs = {"TD", "TB", "BT", "LR", "RL"}
        dir_clean = direction.upper() if direction.upper() in valid_dirs else "TD"
        lines: List[str] = [f"flowchart {dir_clean}"]
        ids = cls._Ids()

        for node in nodes or []:
            if not isinstance(node, dict):
                continue
            raw_id = node.get("id") or node.get("label") or ""
            node_id = ids.get(raw_id)
            if not node_id:
                continue
            label = cls.escape_label(node.get("label") or raw_id)  # the caller's text, not the id
            shape = node.get("shape", "rect")
            if shape == "round":
                lines.append(f'    {node_id}("{label}")')
            elif shape == "diamond":
                lines.append(f'    {node_id}{{"{label}"}}')
            elif shape == "stadium":
                lines.append(f'    {node_id}(["{label}"])')
            else:
                lines.append(f'    {node_id}["{label}"]')

        for edge in edges or []:
            if not isinstance(edge, dict):
                continue
            src = ids.get(edge.get("from", ""))
            tgt = ids.get(edge.get("to", ""))
            if not src or not tgt:
                continue
            label = edge.get("label")
            if label:
                lines.append(f'    {src} -->|"{cls.escape_label(str(label))}"| {tgt}')
            else:
                lines.append(f"    {src} --> {tgt}")

        return "\n".join(lines)

    @classmethod
    def generate_sequence_diagram(
        cls,
        participants: List[str],
        messages: List[Dict[str, str]],
    ) -> str:
        """Generate a Mermaid sequenceDiagram from participants and messages (from, to, text, arrow)."""
        lines: List[str] = ["sequenceDiagram"]
        ids = cls._Ids()
        for p in participants or []:
            safe_p = ids.get(p)
            if safe_p:
                lines.append(f"    participant {safe_p}")

        for msg in messages or []:
            if not isinstance(msg, dict):
                continue
            sender = ids.get(msg.get("from", ""))
            receiver = ids.get(msg.get("to", ""))
            if not sender or not receiver:
                continue
            text = cls.escape_label(msg.get("text", ""))
            arrow = cls._sanitize_token(msg.get("arrow"), cls.SEQUENCE_ARROW_PATTERN, "->>")
            lines.append(f"    {sender}{arrow}{receiver}: {text}")

        return "\n".join(lines)

    @classmethod
    def generate_er_diagram(
        cls,
        entities: Dict[str, List[Dict[str, str]]],
        relationships: Optional[List[Dict[str, str]]] = None,
    ) -> str:
        """Generate a Mermaid erDiagram from entities and relationships."""
        lines: List[str] = ["erDiagram"]
        ids = cls._Ids()

        for entity_name, fields in (entities or {}).items():
            safe_entity = ids.get(entity_name)
            if not safe_entity:
                continue
            lines.append(f"    {safe_entity} {{")
            for field in fields if isinstance(fields, list) else []:
                if not isinstance(field, dict):
                    continue
                f_type = cls.sanitize_identifier(field.get("type", "string")) or "string"
                f_name = cls.sanitize_identifier(field.get("name", "id")) or "id"
                key = cls._sanitize_token(field.get("key"), r"(?:PK|FK|UK)", "")
                if key:
                    lines.append(f"        {f_type} {f_name} {key}")
                else:
                    lines.append(f"        {f_type} {f_name}")
            lines.append("    }")

        for rel in relationships or []:
            if not isinstance(rel, dict):
                continue
            e1 = ids.get(rel.get("left", ""))
            e2 = ids.get(rel.get("right", ""))
            if not e1 or not e2:
                continue
            card = cls._sanitize_token(rel.get("cardinality"), cls.ER_CARDINALITY_PATTERN, "||--o{")
            label = cls.escape_label(rel.get("label", "relates"))
            lines.append(f'    {e1} {card} {e2} : "{label}"')

        return "\n".join(lines)

    @staticmethod
    def validate_mermaid_syntax(diagram_code: str) -> Dict[str, Any]:
        """Structural syntax validation for generated Mermaid diagrams."""
        stripped = diagram_code.strip()
        if not stripped:
            return {"valid": False, "error": "Empty Mermaid diagram."}

        lines = stripped.splitlines()
        first_line = lines[0].strip()
        valid_headers = (
            "classDiagram",
            "sequenceDiagram",
            "erDiagram",
            "flowchart ",
            "graph ",
            "stateDiagram",
        )
        if not first_line.startswith(valid_headers):
            return {
                "valid": False,
                "error": f"Unsupported or missing Mermaid diagram header: '{first_line}'",
            }

        block_depth = 0
        for line in lines[1:]:
            s = line.strip()
            if s.endswith("{"):
                block_depth += 1
            elif s == "}":
                block_depth -= 1
                if block_depth < 0:
                    return {
                        "valid": False,
                        "error": f"Unbalanced closing brace in diagram (depth={block_depth}).",
                    }

        if block_depth != 0:
            return {
                "valid": False,
                "error": f"Unbalanced curly braces in diagram (depth={block_depth}).",
            }

        return {"valid": True, "header": first_line, "line_count": len(lines)}