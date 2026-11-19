import json
import tomllib
import xml.etree.ElementTree as ET
from typing import Any, Dict

try:
    import yaml  # type: ignore
except ImportError:
    yaml = None


class StructuredDataParser:
    """Parser and validator for JSON, YAML, TOML, and XML structured payloads."""

    @staticmethod
    def parse(content: str, data_format: str = "json") -> Dict[str, Any]:
        """Parse structured text in json, yaml/yml, toml, or xml format."""
        fmt = data_format.strip().lower()
        try:
            if fmt == "json":
                parsed = json.loads(content)
            elif fmt in ("yaml", "yml"):
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
                }
            else:
                return {"valid": False, "format": fmt, "error": f"Unsupported format '{data_format}'."}

            summary_count: Any
            if isinstance(parsed, dict):
                summary_count = len(parsed)
            elif isinstance(parsed, list):
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