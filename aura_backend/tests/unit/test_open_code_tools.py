"""Behaviour of the stand-alone code tools in ``aura_core/tools/open``.

Each case below is one a review of these tools found wrong: correct code reported as broken, a
real defect reported as fine, or valid Mermaid silently rewritten. The test-generator cases run
the generated suite for real (pytest in a temporary directory), because what matters there is
whether ``verified`` agrees with the code, not what the generated text looks like.
"""

from __future__ import annotations

import sys
import textwrap

import pytest

from aura_core.tools.open.automated_test_generator_and_verifier import (
    AutomatedTestGeneratorAndVerifier as Generator,
)
from aura_core.tools.open.mermaid_diagram_generator import MermaidDiagramGenerator as Mermaid
from aura_core.tools.open.symbol_and_import_verifier import SymbolAndImportVerifier as Verifier


def _undefined(source: str) -> list[str]:
    return [u["name"] for u in Verifier.verify_undefined_names(textwrap.dedent(source))["undefined_names"]]


# --------------------------------------------------------------------------- undefined names


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            """
            def outer():
                n = 1
                def inner():
                    return n
                return inner
            """,
            id="closure-reads-outer-local",
        ),
        pytest.param(
            """
            def deco(fn):
                def wrapper(*args, **kwargs):
                    return fn(*args, **kwargs)
                return wrapper
            """,
            id="decorator-factory",
        ),
        pytest.param(
            """
            def counter():
                n = 0
                def inc():
                    nonlocal n
                    n += 1
                    return n
                return inc
            """,
            id="nonlocal",
        ),
        pytest.param(
            """
            def outer():
                a = 1
                def middle():
                    def inner():
                        return a
                    return inner
                return middle
            """,
            id="two-levels-deep",
        ),
        pytest.param(
            """
            def make(limit):
                class Bounded:
                    size = limit
                return Bounded
            """,
            id="class-body-sees-enclosing-function",
        ),
        pytest.param(
            """
            def sort_by(items, k):
                return sorted(items, key=lambda i: i * k)
            """,
            id="lambda-reads-function-local",
        ),
        pytest.param(
            """
            class K(dict):
                def m(self):
                    return __class__
            """,
            id="implicit-__class__-in-method",
        ),
        pytest.param("f = lambda x: x + 1", id="lambda-parameter"),
        pytest.param("print(__file__)", id="module-implicit-name"),
        pytest.param(
            """
            point = [1, 2]
            match point:
                case [a, b]:
                    print(a, b)
            """,
            id="match-capture",
        ),
        pytest.param("from os.path import *\nprint(join('a', 'b'))", id="star-import"),
    ],
)
def test_correct_code_has_no_undefined_names(source: str) -> None:
    assert _undefined(source) == []


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            """
            def f():
                y = 1
            def g():
                return y
            """,
            ["y"],
            id="local-of-a-sibling-function",
        ),
        pytest.param(
            """
            class K:
                LIMIT = 3
                def m(self):
                    return LIMIT
            """,
            ["LIMIT"],
            id="class-attribute-unqualified-in-method",
        ),
        pytest.param(
            """
            class K:
                X = 1
                get = lambda: X
            """,
            ["X"],
            id="class-attribute-in-class-level-lambda",
        ),
        pytest.param(
            """
            def outer():
                def inner():
                    return missing
                return inner
            """,
            ["missing"],
            id="truly-undefined-inside-a-closure",
        ),
    ],
)
def test_names_python_cannot_resolve_are_reported(source: str, expected: list[str]) -> None:
    assert _undefined(source) == expected


@pytest.mark.skipif(sys.version_info < (3, 12), reason="PEP 695 syntax needs Python 3.12+")
def test_pep_695_type_parameters_are_in_scope() -> None:
    source = """
    def first[T](xs: list[T]) -> T:
        y: T = xs[0]
        return y

    class Box[T]:
        def get(self) -> T:
            return self.value

    type Pair[K] = tuple[K, K]
    """
    assert _undefined(source) == []


def test_verify_source_leaves_the_undefined_name_check_opt_in() -> None:
    assert Verifier.verify_source("print(undefined_name)")["valid"] is True
    checked = Verifier.verify_source("print(undefined_name)", check_undefined_names=True)
    assert checked["valid"] is False


# --------------------------------------------------------------------------- mermaid

_ER_LEFT = ("|o", "||", "}o", "}|")
_ER_RIGHT = ("o|", "||", "o{", "|{")


@pytest.mark.parametrize(
    "cardinality",
    [left + line + right for left in _ER_LEFT for line in ("--", "..") for right in _ER_RIGHT],
)
def test_every_valid_er_cardinality_is_kept(cardinality: str) -> None:
    diagram = Mermaid.generate_er_diagram(
        {"A": [{"name": "id", "type": "int", "key": "PK"}]},
        [{"left": "A", "right": "B", "cardinality": cardinality, "label": "has"}],
    )
    assert diagram.splitlines()[-1] == f'    A {cardinality} B : "has"'
    assert Mermaid.validate_mermaid_syntax(diagram)["valid"] is True


@pytest.mark.parametrize("cardinality", ["||--o{\n    click A call evil()", "|o--", "<<-->>", "||==||"])
def test_invalid_er_cardinality_falls_back_to_the_default(cardinality: str) -> None:
    diagram = Mermaid.generate_er_diagram({}, [{"left": "A", "right": "B", "cardinality": cardinality}])
    assert diagram.splitlines() == ["erDiagram", '    A ||--o{ B : "relates"']


@pytest.mark.parametrize("arrow", ["->", "-->", "->>", "-->>", "-x", "--x", "-)", "--)", "<<->>", "<<-->>"])
def test_every_valid_sequence_arrow_is_kept(arrow: str) -> None:
    diagram = Mermaid.generate_sequence_diagram(["a", "b"], [{"from": "a", "to": "b", "text": "hi", "arrow": arrow}])
    assert diagram.splitlines()[-1] == f"    a{arrow}b: hi"


def test_sequence_arrow_cannot_inject_lines() -> None:
    diagram = Mermaid.generate_sequence_diagram(
        ["a", "b"], [{"from": "a", "to": "b", "text": "hi", "arrow": "->>b: x\nclick a"}]
    )
    assert diagram.splitlines()[-1] == "    a->>b: hi"
    assert len(diagram.splitlines()) == 4


def test_distinct_python_names_stay_distinct_nodes() -> None:
    assert Mermaid.sanitize_identifier("_Foo") == "_Foo"
    assert Mermaid.sanitize_identifier("Foo") == "Foo"
    assert Mermaid.sanitize_identifier("__init__") == "__init__"
    assert Mermaid.sanitize_identifier("...") == ""
    assert Mermaid.sanitize_identifier("") == ""

    diagram = Mermaid.generate_class_diagram_from_source("class _Foo:\n    pass\nclass Foo(_Foo):\n    pass\n")
    assert "_Foo <|-- Foo : inherits" in diagram["mermaid"]


# --------------------------------------------------------------------------- test generator

_SAMPLE = textwrap.dedent(
    """
    import abc
    from dataclasses import dataclass
    from typing import overload


    def add(a, b):
        return a + b


    def total(*nums):
        return sum(nums)


    def pick(items, key, default=None, *, strict):
        return items.get(key, default)


    async def fetch(url, retries=3):
        return url


    @overload
    def norm(x: int) -> int: ...
    @overload
    def norm(x: str) -> str: ...
    def norm(x, *, upper=False):
        return x


    class Store:
        def __init__(self, path):
            self.path = path

        def save(self, data):
            raise RuntimeError("a real defect the structural suite cannot see")

        @classmethod
        def make(cls, path):
            return cls(path)

        @staticmethod
        def version():
            return 1

        @property
        def name(self):
            return self.path

        @name.setter
        def name(self, value):
            self.path = value


    @dataclass
    class Point:
        x: int
        y: int


    class Base(abc.ABC):
        def __init__(self):
            pass

        @abc.abstractmethod
        def run(self):
            ...
    """
)


def _verify(source: str, **kwargs) -> dict:
    result = Generator.generate_and_verify(source, timeout_seconds=60, **kwargs)
    assert result["stage"] == "verification", result
    return result


def test_structural_suite_passes_correct_code_without_skips() -> None:
    result = _verify(_SAMPLE)
    execution = result["execution"]
    assert result["verified"] is True, execution["stdout"]
    assert result["verification_level"] == "structural"
    assert execution["counts"]["failed"] == 0
    assert execution["counts"]["skipped"] == 0
    assert execution["counts"]["passed"] > 10
    assert result["generation"]["checks"]["call_smoke"] == 0


def test_generated_suite_contains_no_tautologies_or_blanket_skips() -> None:
    code = Generator.generate_pytest_code(_SAMPLE, include_call_smoke=True)["test_code"]
    assert "is not None or" not in code
    assert "pytest.skip" not in code
    assert "except Exception" not in code
    # The receiver is part of the runtime signature, so it is asserted, not guessed around.
    assert "['cls', 'path']" in code
    # Only the implementation of an @overload'ed function is checked.
    assert "['x', 'upper']" in code
    assert "['x']" not in code


def test_call_smoke_reports_a_no_argument_function_that_raises() -> None:
    source = "def healthy():\n    return 1\n\ndef broken():\n    raise RuntimeError('boom')\n"
    structural = _verify(source)
    assert structural["verified"] is True  # structure alone cannot see this defect

    smoke = _verify(source, include_call_smoke=True)
    assert smoke["verified"] is False
    assert smoke["verification_level"] == "structural+call_smoke"
    assert smoke["execution"]["failed_tests"] == ["test_generated.py::test_broken_runs_without_arguments"]


def test_call_smoke_never_instantiates_classes_that_need_arguments() -> None:
    result = _verify(_SAMPLE, include_call_smoke=True)
    code = result["test_code"]
    assert "test_Point_instantiates_without_arguments" not in code  # dataclass fields are required
    assert "test_Store_instantiates_without_arguments" not in code  # __init__ needs `path`
    assert "test_Base_instantiates_without_arguments" not in code  # abstract
    assert result["verified"] is True, result["execution"]["stdout"]


def test_a_module_that_fails_to_import_is_not_verified() -> None:
    result = _verify("import a_dependency_that_is_not_installed\n\ndef f():\n    return 1\n")
    assert result["verified"] is False
    assert result["execution"]["counts"]["errors"] >= 1


def test_nested_module_path_is_importable() -> None:
    result = _verify("def a():\n    return 1\n", module_import_path="pkg.sub.mod")
    assert result["verified"] is True, result["execution"]["stdout"]


@pytest.mark.parametrize("module_path", ["os; import shutil", "pkg..mod", "class.mod", "1abc", ""])
def test_module_import_path_must_be_a_dotted_identifier(module_path: str) -> None:
    result = Generator.generate_pytest_code("def f():\n    return 1\n", module_import_path=module_path or "")
    if module_path == "":
        assert result["generated"] is True  # empty means the default module name
    else:
        assert result["generated"] is False
        assert "dotted Python identifier" in result["error"]
