"""Integration tests for the optional tree-sitter language adapters."""

import os

import pytest

pytest.importorskip("tree_sitter")

from smartbench.graph.tree_parser import extract_symbols, get_parser


@pytest.mark.parametrize(
    ("language", "source"),
    [
        ("python", b"def alpha():\n    return 1\n"),
        ("go", b"package main\nfunc alpha() {}\n"),
        ("javascript", b"function alpha() { return 1; }\n"),
        ("typescript", b"function alpha(): number { return 1; }\n"),
        ("rust", b"fn alpha() {}\n"),
    ],
)
def test_language_adapter_extracts_function(language, source):
    parser = get_parser(language)
    if parser is None:
        if os.environ.get("SMARTBENCH_REQUIRE_TREE_SITTER") == "1":
            pytest.fail(f"missing tree-sitter adapter for {language}")
        pytest.skip(f"optional tree-sitter adapter not installed: {language}")

    symbols = extract_symbols(parser, source, f"sample.{language}")

    assert any(function["name"] == "alpha" for function in symbols["functions"])


def test_python_adapter_extracts_class():
    parser = get_parser("python")
    if parser is None:
        if os.environ.get("SMARTBENCH_REQUIRE_TREE_SITTER") == "1":
            pytest.fail("missing tree-sitter adapter for python")
        pytest.skip("optional tree-sitter adapter not installed: python")

    symbols = extract_symbols(parser, b"class Worker:\n    pass\n", "worker.py")

    assert any(code_class["name"] == "Worker" for code_class in symbols["classes"])


@pytest.mark.parametrize(
    ("language", "source"),
    [
        ("javascript", b"const alpha = async (value) => value + 1;\n"),
        ("typescript", b"const alpha = (value: number): number => value + 1;\n"),
    ],
)
def test_javascript_adapter_extracts_arrow_function(language, source):
    parser = get_parser(language)
    if parser is None:
        if os.environ.get("SMARTBENCH_REQUIRE_TREE_SITTER") == "1":
            pytest.fail(f"missing tree-sitter adapter for {language}")
        pytest.skip(f"optional tree-sitter adapter not installed: {language}")

    symbols = extract_symbols(parser, source, f"sample.{language}")

    assert any(function["name"] == "alpha" for function in symbols["functions"])
