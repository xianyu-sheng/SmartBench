#!/usr/bin/env python3
"""
测试新的简化分析器。
"""

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

console = Console()

from smartbench.graph.tree_parser import get_parser
from smartbench.flow.ast_traversal import create_ast_context
from smartbench.flow.taint_simple import SimpleTaintAnalyzer, TaintTracker


def test_simple():
    """测试简单案例。"""
    source = """
async function dangerousEndpoint(req, res) {
    const userId = req.query.userId;
    const query = `SELECT * FROM users WHERE id = ${userId}`;
    await db.run(query);
}
""".strip()

    console.print(Panel("Test 1: req.query -> SQL", style="bold blue"))
    console.print(Syntax(source, "typescript"))
    console.print()

    source_bytes = source.encode("utf-8")
    parser = get_parser("typescript")
    if not parser:
        console.print("[red]No parser![/red]")
        return

    tree = parser.parse(source_bytes)

    context = create_ast_context("test.ts", source)
    tracker = TaintTracker(context)
    analyzer = SimpleTaintAnalyzer(context, tracker)
    analyzer._debug = True

    console.print("\n[bold]Analyzing...[/bold]")
    findings = analyzer.analyze_and_find_findings(tree.root_node)

    console.print(f"\n[bold]Findings: {len(findings)}[/bold]")
    for f in findings:
        console.print(f"  [red]{f['type']}[/red]")
        console.print(f"  {f['snippet']}")
        for step in f["value"].taint_trace:
            console.print(f"    [cyan]{step.operation}[/cyan]: {step.source_snippet}")


def test_snippets():
    """测试 CodeSnippetsIndex 案例。"""
    source = """
async function deleteSnippets(snippets, db) {
    const snippetIds = snippets.map((row) => row.id).join(',');
    return await db.run(`DELETE FROM code_snippets WHERE id IN (${snippetIds})`);
}
""".strip()

    console.print("\n" + "=" * 60)
    console.print(Panel("Test 2: snippets -> SQL (manual mark)", style="bold blue"))
    console.print(Syntax(source, "typescript"))
    console.print()

    source_bytes = source.encode("utf-8")
    parser = get_parser("typescript")
    tree = parser.parse(source_bytes)

    context = create_ast_context("test.ts", source)
    tracker = TaintTracker(context)
    analyzer = SimpleTaintAnalyzer(context, tracker)
    analyzer._debug = True

    # 手动把 snippets 添加到变量表
    from smartbench.flow.taint_simple import SimpleTaintAnalyzer

    # 我们需要手动修改分析过程
    # 让我们直接调用内部方法
    variables = {}

    # Mark snippets as tainted
    from smartbench.flow.schema import location_from_node
    loc = location_from_node("test.ts", tree.root_node)
    variables["snippets"] = tracker.create_tainted_value(
        loc, "parameter: snippets", "snippets"
    )

    # 收集变量
    analyzer._collect_variables(tree.root_node, variables, [{}])

    print(f"\nVariables found: {list(variables.keys())}")
    for k, v in variables.items():
        print(f"  {k}: {v.taint_state}")

    # 查找问题
    findings = analyzer._find_sink_calls(tree.root_node, variables)

    console.print(f"\n[bold]Findings: {len(findings)}[/bold]")
    for f in findings:
        console.print(f"  [red]{f['type']}[/red]")
        console.print(f"  {f['snippet']}")


def test_with_flow_module():
    """测试通过 flow 模块的公共接口。"""
    console.print("\n" + "=" * 60)
    console.print(Panel("Test 3: Using DataFlowAnalyzer", style="bold blue"))

    from smartbench.flow import DataFlowAnalyzer

    source = """
async function dangerousEndpoint(req, res) {
    const userId = req.query.userId;
    const query = `SELECT * FROM users WHERE id = ${userId}`;
    await db.run(query);
}
""".strip()

    analyzer = DataFlowAnalyzer()
    result = analyzer.analyze_file("test.ts", source, "typescript")

    console.print(f"\nSuccess: {result.success}")
    console.print(f"Findings: {len(result.findings)}")

    for f in result.findings:
        console.print(f"  [red]{f.rule_name}[/red]")
        console.print(f"  {f.message}")


if __name__ == "__main__":
    test_simple()
    #test_snippets()
    test_with_flow_module()
