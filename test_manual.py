#!/usr/bin/env python3
"""
手动测试数据流分析。
"""

from rich.console import Console
from rich.syntax import Syntax

console = Console()

from smartbench.graph.tree_parser import get_parser
from smartbench.flow.ast_traversal import (
    create_ast_context,
    get_node_text,
    get_child_by_field_name,
)
from smartbench.flow.schema import location_from_node, TaintState
from smartbench.flow.taint_simple import SimpleTaintAnalyzer, TaintTracker


def manual_test():
    """手动测试。"""
    source = """
async function dangerousEndpoint(req, res) {
    const userId = req.query.userId;
    const query = `SELECT * FROM users WHERE id = ${userId}`;
    await db.run(query);
}
""".strip()

    console.print(Syntax(source, "typescript"))

    source_bytes = source.encode("utf-8")
    parser = get_parser("typescript")
    tree = parser.parse(source_bytes)

    context = create_ast_context("test.ts", source)
    tracker = TaintTracker(context)

    # 手动建立作用域
    from smartbench.flow.scope import ScopeType

    # Module scope
    tracker.scope_manager.enter_scope(ScopeType.MODULE, None, "module")

    # Function scope
    func_node = None
    def find_func(node):
        nonlocal func_node
        if node.type == "function_declaration":
            func_node = node
        for child in node.children:
            find_func(child)

    find_func(tree.root_node)

    func_loc = location_from_node("test.ts", func_node)
    scope = tracker.scope_manager.enter_scope(ScopeType.FUNCTION, func_loc, "dangerousEndpoint")

    # Mark req as tainted
    req_loc = location_from_node("test.ts", func_node)
    req_value = tracker.create_tainted_value(req_loc, "parameter: req", "req")
    scope.set("req", req_value)

    # Block scope
    block_node = None
    def find_block(node):
        nonlocal block_node
        if node.type == "statement_block":
            block_node = node
        for child in node.children:
            find_block(child)

    find_block(func_node)

    block_loc = location_from_node("test.ts", block_node)
    tracker.scope_manager.enter_scope(ScopeType.BLOCK, block_loc)

    # Find variable declarators
    declarators = []
    def find_decls(node):
        if node.type == "variable_declarator":
            declarators.append(node)
        for child in node.children:
            find_decls(child)

    find_decls(block_node)

    print(f"\nFound {len(declarators)} declarators")

    # 现在手动计算这些变量
    print("\n" + "=" * 60)

    # First declarator: userId = req.query.userId
    decl1 = declarators[0]

    name_node = get_child_by_field_name(decl1, "name")
    value_node = get_child_by_field_name(decl1, "value")

    print(f"\nDeclarator 1:")
    print(f"  name: {get_node_text(name_node, source_bytes)}")
    print(f"  value type: {value_node.type}")

    # Let's create a little evaluator
    def evaluate(node):
        print(f"Evaluating: {node.type}")

        if node.type == "member_expression":
            obj = get_child_by_field_name(node, "object")
            prop = get_child_by_field_name(node, "property")

            obj_text = get_node_text(obj, source_bytes)
            prop_text = get_node_text(prop, source_bytes)
            print(f"  Member: {obj_text}.{prop_text}")

            # Check for req.query
            if obj_text == "req" and prop_text == "query":
                print("  => Tainted!")
                return "tainted"

            # Recurse on object
            obj_result = evaluate(obj)
            if obj_result == "tainted":
                print("  => Propagated taint")
                return "tainted"

        elif node.type == "identifier":
            name = get_node_text(node, source_bytes)
            value = tracker.scope_manager.get(name)
            if value:
                print(f"  Identifier {name}: {value.taint_state}")
                if value.taint_state == TaintState.TAINTED:
                    return "tainted"

        elif node.type == "template_string":
            print(f"  Template children: {len(node.children)}")
            values = []
            for child in node.children:
                if child.type == "template_substitution":
                    print(f"    Found substitution!")
                    expr = get_child_by_field_name(child, "expression")
                    if expr:
                        v = evaluate(expr)
                        if v == "tainted":
                            values.append("tainted")
            if "tainted" in values:
                print(f"  => Tainted template")
                return "tainted"
            return "not_tainted"

        return "unknown"

    # Evaluate first value (req.query.userId)
    print("\nEvaluating value 1...")
    result1 = evaluate(value_node)
    print(f"Result: {result1}")

    # Set userId variable
    if result1 = "tainted"
    loc = location_from_node("test.ts", decl1)
    if result1 == "tainted":
        val = tracker.create_tainted_value(loc, "assignment", "userId")
    else:
        val = tracker.create_value(loc, TaintState.NOT_TAINTED)
    tracker.scope_manager.set("userId", val)

    # Second declarator: query = `...`
    decl2 = declarators[1]
    value_node2 = get_child_by_field_name(decl2, "value")

    print(f"\nEvaluating value 2 (template string)...")
    result2 = evaluate(value_node2)
    print(f"Result: {result2}")

    if result2 == "tainted":
        val2 = tracker.create_tainted_value(location_from_node("test.ts", decl2), "template", "query")
    else:
        val2 = tracker.create_value(location_from_node("test.ts", decl2), TaintState.NOT_TAINTED
    tracker.scope_manager.set("query", "tainted" if result2 == "tainted" else "not_tainted")
    print(f"Set query = {result2}")

    # Find call expression
    print("\n" + "=" * 60)
    call_node = None
    def find_call(node):
        nonlocal call_node
        if node.type == "call_expression":
            func = get_child_by_field_name(node, "function")
            if func:
                ft = get_node_text(func, source_bytes)
                print(f"Found call: {ft}")
                if ".run" in ft or ".all" in ft or ".query" in ft:
                    call_node = node
        for child in node.children:
            find_call(child)

    find_call(tree.root_node)

    if call_node:
        print(f"\nFound call:")
        args_node = get_child_by_field_name(call_node, "arguments")
        if args_node:
            print(f"Args node has {len(args_node.children)} children")
            for child in args_node.children:
                if child.is_named:
                    print(f"  Arg: {child.type}")
                    result = evaluate(child)
                    print(f"  Result: {result}")


if __name__ == "__main__":
    manual_test()
