#!/usr/bin/env python3
"""
测试 tree-sitter 的字段名。
"""

from rich.console import Console

console = Console()

from smartbench.graph.tree_parser import get_parser
from smartbench.flow.ast_traversal import (
    create_ast_context,
    get_node_text,
    get_child_by_field_name,
)


def test_fields():
    """测试字段名。"""
    source = """
const query = `SELECT * FROM users WHERE id = ${userId}`;
""".strip()

    source_bytes = source.encode("utf-8")
    parser = get_parser("typescript")
    tree = parser.parse(source_bytes)

    # Find template_substitution
    sub_node = None

    def find_sub(node):
        nonlocal sub_node
        if node.type == "template_substitution":
            sub_node = node
        for child in node.children:
            find_sub(child)

    find_sub(tree.root_node)

    if sub_node:
        print(f"template_substitution found!")
        print(f"Children:")
        for child in sub_node.children:
            print(f"  {child.type}: {repr(get_node_text(child, source_bytes))}")

        print(f"\nFields via node.child_by_field_name:")
        # 试试常见的字段名
        for field_name in ["expression", "value", "arg", "argument"]:
            child = sub_node.child_by_field_name(field_name)
            print(f"  {field_name}: {child.type if child else 'None'}")

        # 或者 tree-sitter 可能需要用不同的方式
        print(f"\nLooking for identifier in children:")
        for child in sub_node.children:
            if child.type == "identifier":
                print(f"  Found identifier: {get_node_text(child, source_bytes)}")


if __name__ == "__main__":
    test_fields()
