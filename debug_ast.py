#!/usr/bin/env python3
"""
调试 AST 结构 - 看看 tree-sitter 是如何解析代码的。
"""

from smartbench.graph.tree_parser import get_parser
from smartbench.flow.ast_traversal import get_node_text, get_child_by_field_name, get_named_children
from smartbench.flow.schema import location_from_node


def print_node(node, source_bytes, depth=0):
    """递归打印节点结构。"""
    indent = "  " * depth
    node_type = node.type
    text = get_node_text(node, source_bytes)[:50] if node.start_byte != node.end_byte else ""
    text = repr(text)

    print(f"{indent}[{node_type}] {text} @ {node.start_point}..{node.end_point}")

    # 打印字段
    for field_name in ["function", "arguments", "name", "value", "object", "property", "left", "right"]:
        child = get_child_by_field_name(node, field_name)
        if child:
            child_text = get_node_text(child, source_bytes)[:40]
            print(f"{indent}  .{field_name}: [{child.type}] {repr(child_text)}")

    for child in get_named_children(node):
        print_node(child, source_bytes, depth + 1)


def main():
    source1 = """
async function deleteSnippets(snippets) {
    const snippetIds = snippets.map((row) => row.id).join(',');
    return await db.run(`DELETE FROM code_snippets WHERE id IN (${snippetIds})`);
}
""".strip()

    source2 = """
async function dangerousEndpoint(req, res) {
    const userId = req.query.userId;
    const query = `SELECT * FROM users WHERE id = ${userId}`;
    await db.run(query);
}
""".strip()

    for name, source in [("test1", source1), ("test2", source2)]:
        print("=" * 60)
        print(name)
        print("=" * 60)
        print(source)
        print()

        source_bytes = source.encode("utf-8")
        parser = get_parser("typescript")
        if not parser:
            print("No parser!")
            continue

        tree = parser.parse(source_bytes)
        print_node(tree.root_node, source_bytes)
        print()


if __name__ == "__main__":
    main()
