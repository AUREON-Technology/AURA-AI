import ast
import re
from typing import Any, Dict, List, Optional


class MermaidDiagramGenerator:
    """Mermaid diagram generator for code architecture, classes, flows, sequences, and schemas."""

    @staticmethod
    def sanitize_identifier(name: Optional[str]) -> str:
        """Normalize an identifier so it is safe inside Mermaid diagrams; returns '' if empty."""
        if not name or not str(name).strip():
            return ""
        cleaned = re.sub(r"[^A-Za-z0-9_]", "_", str(name).strip())
        cleaned = re.sub(r"_+", "_", cleaned).strip("_")
        if not cleaned:
            return ""
        if cleaned[0].isdigit():
            cleaned = f"N_{cleaned}"
        return cleaned

    @staticmethod
    def escape_label(text: str) -> str:
        """Escape quotes and line breaks for safe Mermaid labels."""
        return text.replace('"', "'").replace("\r", " ").replace("\n", " ").strip()

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

        for cname in class_names or []:
            safe_name = cls.sanitize_identifier(cname)
            if safe_name and safe_name not in seen_classes:
                lines.append(f"    class {safe_name}")
                seen_classes.add(safe_name)

        for detail in class_details or []:
            raw_name = detail.get("name", "")
            safe_name = cls.sanitize_identifier(raw_name)
            if not safe_name:
                continue
            seen_classes.add(safe_name)
            members: List[str] = []
            for attr in detail.get("attributes", []):
                members.append(f"        {cls.escape_label(str(attr))}")
            for method in detail.get("methods", []):
                members.append(f"        {cls.escape_label(str(method))}")
            if members:
                lines.append(f"    class {safe_name} {{")
                lines.extend(members)
                lines.append("    }")
            else:
                lines.append(f"    class {safe_name}")

        for rel in relations or []:
            src = cls.sanitize_identifier(rel.get("source", ""))
            tgt = cls.sanitize_identifier(rel.get("target", ""))
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

        for node in nodes:
            node_id = cls.sanitize_identifier(node.get("id", ""))
            if not node_id:
                continue
            label = cls.escape_label(node.get("label", node_id))
            shape = node.get("shape", "rect")
            if shape == "round":
                lines.append(f'    {node_id}("{label}")')
            elif shape == "diamond":
                lines.append(f'    {node_id}{{"{label}"}}')
            elif shape == "stadium":
                lines.append(f'    {node_id}(["{label}"])')
            else:
                lines.append(f'    {node_id}["{label}"]')

        for edge in edges:
            src = cls.sanitize_identifier(edge.get("from", ""))
            tgt = cls.sanitize_identifier(edge.get("to", ""))
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
        for p in participants:
            safe_p = cls.sanitize_identifier(p)
            if safe_p:
                lines.append(f"    participant {safe_p}")

        for msg in messages:
            sender = cls.sanitize_identifier(msg.get("from", ""))
            receiver = cls.sanitize_identifier(msg.get("to", ""))
            if not sender or not receiver:
                continue
            text = cls.escape_label(msg.get("text", ""))
            arrow = cls._sanitize_token(msg.get("arrow"), r"[-x<>o]+", "->>")
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

        for entity_name, fields in entities.items():
            safe_entity = cls.sanitize_identifier(entity_name)
            if not safe_entity:
                continue
            lines.append(f"    {safe_entity} {{")
            for field in fields:
                f_type = cls.sanitize_identifier(field.get("type", "string")) or "string"
                f_name = cls.sanitize_identifier(field.get("name", "id")) or "id"
                key = cls._sanitize_token(field.get("key"), r"(?:PK|FK|UK)", "")
                if key:
                    lines.append(f"        {f_type} {f_name} {key}")
                else:
                    lines.append(f"        {f_type} {f_name}")
            lines.append("    }")

        for rel in relationships or []:
            e1 = cls.sanitize_identifier(rel.get("left", ""))
            e2 = cls.sanitize_identifier(rel.get("right", ""))
            if not e1 or not e2:
                continue
            card = cls._sanitize_token(rel.get("cardinality"), r"[|o}{.\-]{2,5}", "||--o{")
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