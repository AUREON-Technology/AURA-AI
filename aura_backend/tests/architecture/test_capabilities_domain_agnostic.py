"""Capability primitives carry no domain knowledge (ADR-023, AP5).

The platform is universal only if "an HR assistant" is *composed* from primitives that apply to
any system. A capability file, or the planner that matches them, that names a business domain's
nouns is an industry template in disguise -- exactly what ADR-023 rejects. This denylist is
deliberately broad; a genuine need for one of these words in a primitive is a design discussion,
not a test edit.
"""

from __future__ import annotations

import re
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "aura_core"
CHECKED = sorted((CORE / "adaptation" / "capabilities").glob("*.yaml")) + [CORE / "adaptation" / "planner.py"]

DOMAIN_NOUNS = (
    # people and organisations
    "employee", "staff", "salary", "payroll", "leave", "department", "manager", "hr",
    "patient", "doctor", "student", "teacher", "tenant", "member",
    # commerce
    "customer", "client", "order", "product", "sku", "cart", "checkout", "shop", "store",
    "payment", "invoice", "refund", "shipment", "shipping", "supplier", "vendor", "inventory",
    "price", "discount",
    # other verticals
    "ticket", "incident", "loan", "claim", "policy_holder", "appointment", "booking", "factory",
    "warehouse", "vehicle", "lead", "opportunity",
)
_WORD = re.compile(r"\b(" + "|".join(DOMAIN_NOUNS) + r")s?\b", re.IGNORECASE)


def test_capabilities_are_domain_agnostic() -> None:
    assert len(CHECKED) > 1
    violations = []
    for path in CHECKED:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for match in _WORD.finditer(line):
                violations.append(f"{path.relative_to(CORE)}:{number}: {match.group(0)!r}")
    assert not violations, "capability primitives must not name a domain (ADR-023):\n  " + "\n  ".join(violations)
