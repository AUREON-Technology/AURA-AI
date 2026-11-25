"""ADR-056: the tokenizer behind ``analyze_source_structure`` for non-Python languages.

Each case is a construct that used to put a brace, quote or keyword where the declaration scanner could see it
(the regex pair it replaced blanked ``"..."``, ``'...'`` and `` `...` `` and nothing else). A passing case finds the real
declaration with the right extent; the "before" cases that the old regexes got wrong are marked.
"""

from __future__ import annotations

import pytest

from aura_core.tools.open_pure_logic import _blank, cyclomatic, scan_source


def symbols(source: str, lang: str) -> dict[str, dict]:
    return {s["name"]: s for s in scan_source(source, lang)["symbols"]}


class TestBlanking:
    def test_offsets_and_newlines_are_kept(self) -> None:
        src = 'let a = "x\\"y"; // c\n/* b\n */ let z;\n'
        out = _blank(src, "javascript")
        assert len(out) == len(src) and out.count("\n") == src.count("\n")
        assert "let a =" in out and "let z;" in out and "x" not in out.split("let a")[1].split(";")[0].strip('= "')

    @pytest.mark.parametrize("lang,src,must_not_contain", [
        ("javascript", 'const u = "http://example.com/{";', "{"),
        ("javascript", "const t = `a ${ {b: 1}.b } {`;", "{"),
        ("javascript", "const t = `x ${ `nested ${ 1 } }` } end`;", "}"),
        ("javascript", "const r = /[\"'{}]+/g;", "{"),
        ("typescript", "x = a / b; y = c / d;", None),
        ("go", "s := `raw } { string`", "{"),
        ("go", "r := '{'", "{"),
        ("rust", 'let s = r#"} " {"#;', "{"),
        ("rust", "let c = '{'; fn real<'a>(x: &'a str) {}", None),
        ("rust", "/* outer /* inner */ still comment { */ fn real() {}", "still"),
        ("kotlin", "/* a /* b */ c { */ fun real() {}", "c {"),
        ("java", 'String s = """\n  { not code }\n  """;', "{"),
        ("java", "char c = '}';", "}"),
        ("csharp", 'var p = @"C:\\dir\\";', "dir"),
        ("csharp", 'var s = $"{x} {{y}}";', "{"),
    ])
    def test_what_is_not_code_is_blanked(self, lang, src, must_not_contain) -> None:
        out = _blank(src, lang)
        if must_not_contain:
            assert must_not_contain not in out, (lang, src, out)

    def test_division_is_not_a_regex_and_does_not_swallow_the_line(self) -> None:
        out = _blank("const half = total / 2; const f = function () { return 1; };", "javascript")
        assert "function () { return 1; }" in out

    def test_a_regex_after_return_or_an_equals_sign_is_blanked_but_not_after_an_identifier(self) -> None:
        assert "{" not in _blank("return /a{2}/.test(x);", "javascript")
        assert "a / b" in _blank("const q = a / b / c;", "javascript")

    def test_an_unterminated_string_ends_at_its_line_not_the_file(self) -> None:
        out = _blank('let a = "oops\nfunction f() { return 1; }\n', "javascript")
        assert "function f() { return 1; }" in out

    def test_a_pathological_input_finishes_quickly(self) -> None:
        import time

        start = time.perf_counter()
        for src in ("`" * 5000, "${" * 5000, '"' * 5000, "/*" * 5000, "r#\"" * 2000, "'" * 5000, "/" * 5000):
            for lang in ("javascript", "go", "rust", "csharp", "java"):
                _blank(src, lang)
        assert time.perf_counter() - start < 5


class TestDeclarationsAreFoundDespiteTheTraps:
    def test_javascript_template_literal_with_braces_before_a_function(self) -> None:
        src = "const t = `${ {a: 1}.a } }`;\nfunction real(x) {\n  return x;\n}\nfunction after() {}\n"
        found = symbols(src, "javascript")
        assert found["real"]["line"] == 2 and found["real"]["end_line"] == 4
        assert found["after"]["line"] == 5

    def test_a_brace_inside_a_regex_does_not_extend_the_function(self) -> None:
        src = "function a() {\n  return /\\{/.test(s);\n}\nfunction b() {}\n"
        found = symbols(src, "javascript")
        assert found["a"]["end_line"] == 3 and found["b"]["line"] == 4

    def test_rust_raw_string_nested_comment_and_lifetime(self) -> None:
        src = ('/* a /* b */ { */\nfn first<\'a>(x: &\'a str) -> &\'a str {\n    let s = r#"} fn fake() {"#;\n    x\n}\n'
               "fn second() {}\n")
        found = symbols(src, "rust")
        assert set(found) == {"first", "second"} and found["first"]["end_line"] == 5

    def test_go_raw_string_with_a_func_inside(self) -> None:
        src = "package p\nfunc Real() string {\n\treturn `func fake() {`\n}\nfunc After() {}\n"
        found = symbols(src, "go")
        assert set(found) == {"Real", "After"} and found["Real"]["end_line"] == 4

    def test_java_text_block_and_char_literal(self) -> None:
        src = 'class A {\n  String q = """\n    void fake() { }\n    """;\n  char c = \'}\';\n  public void real() {\n  }\n}\n'
        found = symbols(src, "java")
        assert "fake" not in found and found["real"]["parent"] == "A" and found["A"]["end_line"] == 8

    def test_csharp_verbatim_string_ending_in_a_backslash(self) -> None:
        src = 'class C {\n  string p = @"C:\\dir\\";\n  public void Real() {\n  }\n}\n'
        found = symbols(src, "csharp")
        assert found["Real"]["parent"] == "C" and found["C"]["end_line"] == 5

    def test_kotlin_nested_block_comment(self) -> None:
        src = "/* a /* fun fake() { */ still */\nfun real() {\n}\n"
        assert set(symbols(src, "kotlin")) == {"real"}


class TestComplexity:
    def test_a_straight_line_function_is_one(self) -> None:
        assert symbols("function f(a) {\n  return a + 1;\n}\n", "javascript")["f"]["complexity"] == 1

    def test_branches_loops_boolean_operators_and_ternaries_each_add_one(self) -> None:
        src = ("function f(a, b) {\n  if (a && b) {\n    for (const x of a) { while (x) { x--; } }\n  } else if (b || a) {\n"
               "    return a ? 1 : 2;\n  }\n  try { g(); } catch (e) { h(); }\n}\n")
        assert symbols(src, "javascript")["f"]["complexity"] == 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1  # if && for while if || ?: catch

    def test_a_decision_word_inside_a_string_or_comment_counts_for_nothing(self) -> None:
        src = 'function f() {\n  // if while for\n  const s = "if (a && b) while";\n  return s;\n}\n'
        assert symbols(src, "javascript")["f"]["complexity"] == 1

    def test_optional_chaining_and_nullish_coalescing(self) -> None:
        assert symbols("function f(a) {\n  return a?.b ?? 1;\n}\n", "javascript")["f"]["complexity"] == 2

    def test_other_languages_and_the_summary(self) -> None:
        go = "package p\nfunc Pick(a int) int {\n\tswitch {\n\tcase a > 1:\n\t\treturn 1\n\tcase a > 2:\n\t\treturn 2\n\t}\n\tif a > 3 {\n\t\treturn 3\n\t}\n\treturn 0\n}\nfunc Flat() {}\n"
        result = scan_source(go, "go")
        by = {s["name"]: s["complexity"] for s in result["symbols"]}
        assert by == {"Pick": 4, "Flat": 1}
        assert result["complexity"]["most_complex"][0] == {"name": "Pick", "line": 2, "complexity": 4}
        assert "estimate" in result["complexity"]["method"]

    def test_cyclomatic_of_empty_text_is_one(self) -> None:
        assert cyclomatic("") == 1
