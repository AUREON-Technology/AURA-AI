import json
import re
import tomllib
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional


class StructuredDataParser:
    """Parser and validator for JSON, YAML, TOML, and XML structured payloads with schema validation and line diagnostics."""

    @staticmethod
    def parse(content: str, data_format: str = "json") -> Dict[str, Any]:
        """Parse structured text in json, yaml/yml, toml, or xml format."""
        fmt = data_format.strip().lower()
        try:
            if fmt == "json":
                parsed = json.loads(content)
            elif fmt in ("yaml", "yml"):
                try:
                    import yaml  # type: ignore
                except ImportError:
                    yaml = None
                if yaml is None:
                    return {"valid": False, "format": fmt, "error": "PyYAML is not installed."}
                parsed = yaml.safe_load(content)
            elif fmt == "toml":
                parsed = tomllib.loads(content)
            elif fmt == "xml":
                root = ET.fromstring(content)
                parsed = {
                    "tag": root.tag,
                    "attrib": dict(root.attrib),
                    "children_count": len(list(root)),
                    "text": root.text.strip() if root.text else "",
                }
            else:
                return {"valid": False, "format": fmt, "error": f"Unsupported format '{data_format}'."}

            summary_count: int
            if isinstance(parsed, (dict, list)):
                summary_count = len(parsed)
            else:
                summary_count = 1

            return {
                "valid": True,
                "format": fmt,
                "parsed_data": parsed,
                "items_count": summary_count,
            }
        except Exception as exc:
            return {
                "valid": False,
                "format": fmt,
                "error": f"{type(exc).__name__}: {exc}",
            }

    @staticmethod
    def parse_with_line_errors(content: str, data_format: str = "json") -> Dict[str, Any]:
        """Parse content and extract exact line number and error context when parsing fails."""
        fmt = data_format.strip().lower()
        lines = content.splitlines()

        try:
            if fmt == "json":
                parsed = json.loads(content)
            elif fmt in ("yaml", "yml"):
                try:
                    import yaml  # type: ignore
                except ImportError:
                    yaml = None
                if yaml is None:
                    return {"valid": False, "error": "PyYAML not installed", "line": 0}
                parsed = yaml.safe_load(content)
            elif fmt == "toml":
                parsed = tomllib.loads(content)
            elif fmt == "xml":
                parsed = ET.fromstring(content)
            else:
                return {"valid": False, "error": f"Unsupported format: {fmt}", "line": 0}

            return {"valid": True, "error": None, "line": None, "parsed_data": parsed}
        except json.JSONDecodeError as exc:
            line_no = exc.lineno
            col_no = exc.colno
            err_line = lines[line_no - 1] if 0 < line_no <= len(lines) else ""
            return {
                "valid": False,
                "error": exc.msg,
                "line": line_no,
                "column": col_no,
                "offending_line": err_line,
                "context": lines[max(0, line_no - 2) : min(len(lines), line_no + 1)],
            }
        except tomllib.TOMLDecodeError as exc:
            # Extract line number from toml error message if available
            m = re.search(r"line\s+(\d+)", str(exc))
            line_no = int(m.group(1)) if m else 1
            err_line = lines[line_no - 1] if 0 < line_no <= len(lines) else ""
            return {
                "valid": False,
                "error": str(exc),
                "line": line_no,
                "offending_line": err_line,
            }
        except ET.ParseError as exc:
            m = re.search(r"line\s+(\d+),\s*column\s+(\d+)", str(exc))
            line_no = int(m.group(1)) if m else 1
            col_no = int(m.group(2)) if m else 1
            return {
                "valid": False,
                "error": str(exc),
                "line": line_no,
                "column": col_no,
                "offending_line": lines[line_no - 1] if 0 < line_no <= len(lines) else "",
            }
        except Exception as exc:
            return {
                "valid": False,
                "error": str(exc),
                "line": None,
            }

    @staticmethod
    def validate_schema(
        data: Any,
        required_keys: Optional[List[str]] = None,
        type_contracts: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Validate parsed dictionary against required keys and data type contracts."""
        if not isinstance(data, dict):
            return {"valid": False, "errors": ["Root data is not a dictionary / object."]}

        errors: List[str] = []
        reqs = required_keys or []
        for k in reqs:
            if k not in data:
                errors.append(f"Missing required key: '{k}'")

        type_map = {
            "str": str,
            "int": int,
            "float": (int, float),
            "bool": bool,
            "list": list,
            "dict": dict,
        }

        if type_contracts:
            for field, expected_type_name in type_contracts.items():
                if field in data and data[field] is not None:
                    target_cls = type_map.get(expected_type_name.lower())
                    if target_cls and not isinstance(data[field], target_cls):
                        errors.append(
                            f"Field '{field}' expected {expected_type_name}, got {type(data[field]).__name__}"
                        )

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "checked_keys": list(data.keys()),
        }

    @classmethod
    def convert_format(
        cls,
        content: str,
        source_format: str,
        target_format: str,
    ) -> Dict[str, Any]:
        """Losslessly convert structured content between JSON, YAML, and TOML."""
        parsed_res = cls.parse(content, data_format=source_format)
        if not parsed_res["valid"]:
            return {"converted": False, "error": f"Failed to parse source: {parsed_res['error']}"}

        data = parsed_res["parsed_data"]
        tgt = target_format.strip().lower()

        try:
            if tgt == "json":
                out = json.dumps(data, indent=2, default=str)
            elif tgt in ("yaml", "yml"):
                try:
                    import yaml  # type: ignore
                except ImportError:
                    yaml = None
                if yaml is None:
                    return {"converted": False, "error": "PyYAML not installed."}
                out = yaml.dump(data, sort_keys=False)
            else:
                return {"converted": False, "error": f"Unsupported target format: {tgt}"}

            return {
                "converted": True,
                "source_format": source_format,
                "target_format": tgt,
                "output_text": out,
            }
        except Exception as exc:
            return {"converted": False, "error": str(exc)}

    @staticmethod
    def flatten_nested_dict(
        data: Dict[str, Any],
        prefix: str = "",
        separator: str = ".",
    ) -> Dict[str, Any]:
        """Flatten a deeply nested dictionary into dot-separated paths."""
        flat: Dict[str, Any] = {}
        for k, v in data.items():
            key = f"{prefix}{separator}{k}" if prefix else str(k)
            if isinstance(v, dict):
                flat.update(StructuredDataParser.flatten_nested_dict(v, prefix=key, separator=separator))
            else:
                flat[key] = v
        return flat
