#!/usr/bin/env python3
"""
测试变量收集顺序。
"""

from rich.console import Console

console = Console()

from smartbench.graph.tree_parser import get_parser
from smartbench.flow.ast_traversal import (
    create_ast_context,
    get_node_text,
    get_child_by_field_name,
)


def test_order():
    """测试变量声明顺序。"""
    source = """
async function dangerousEndpoint(req, res) {
    const userId = req.query.userId;
    const query = `SELECT * FROM users WHERE id = ${userId}`;
    await db.run(query);
}
""".strip()

    source_bytes = source.encode("utf-8")
    parser = get_parser("typescript")
    tree = parser.parse(source_bytes)

    # 找到所有 variable_declarator 并看顺序
    decls = []

    def find_decls(node):
        if node.type == "variable_declarator":
            name = get_node_text(get_child_by_field_name(node, "name"), source_bytes)
            print(f"Found declarator: {name}")
            decls.append(node)
        for child in node.children:
            find_decls(child)

    find_decls(tree.root_node)

    print(f"\nDecl order:")
    for d in decls:
        name = get_node_text(get_child_by_field_name(d, "name"), source_bytes)
        value_node = get_child_by_field_name(d, "value")
        print(f"  {name} = {value_node.type if value_node else 'None'}")


if __name__ == "__main__":
    test_order()
