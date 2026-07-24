#!/usr/bin/env python3
"""
调试模板字符串处理。
"""

from rich.console import Console
from rich.syntax import Syntax

console = Console()

from smartbench.graph.tree_parser import get_parser
from smartbench.flow.ast_traversal import (
    create_ast_context,
    get_node_text,
    get_child_by_field_name,
    get_named_children,
)
from smartbench.flow.schema import location_from_node


def test_template():
    """测试模板字符串解析。"""
    source = """
const query = `SELECT * FROM users WHERE id = ${userId}`;
""".strip()

    console.print(Syntax(source, "typescript"))
    console.print()

    source_bytes = source.encode("utf-8")
    parser = get_parser("typescript")
    tree = parser.parse(source_bytes)

    def print_node(node, depth=0):
        indent = "  " * depth
        text = get_node_text(node, source_bytes)
        if len(text) > 30:
            text = text[:27] + "..."
        print(f"{indent}[{node.type}] {repr(text)}")

        # Print children
        for child in node.children:
            print_node(child, depth + 1)

    print("AST:")
    print_node(tree.root_node)
    print()

    # Find template_string
    template_node = None

    def find_template(node):
        nonlocal template_node
        if node.type == "template_string":
            template_node = node
        for child in node.children:
            find_template(child)

    find_template(tree.root_node)

    if template_node:
        print("Found template_string node:")
        print(f"  Children count: {len(template_node.children)}")
        for i, child in enumerate(template_node.children):
            text = get_node_text(child, source_bytes)
            print(f"  Child {i}: {child.type} = {repr(text)}")
            print(f"    is_named: {child.is_named}")

            if child.type == "template_substitution":
                expr = get_child_by_field_name(child, "expression")
                if expr:
                    expr_text = get_node_text(expr, source_bytes)
                    print(f"    expression: {expr.type} = {repr(expr_text)}")


if __name__ == "__main__":
    test_template()
