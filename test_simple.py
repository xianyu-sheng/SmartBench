#!/usr/bin/env python3
"""
测试简化的分析器。
"""

from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

console = Console()

from smartbench.graph.tree_parser import get_parser
from smartbench.flow.ast_traversal import create_ast_context
from smartbench.flow.taint_simple import SimpleTaintAnalyzer, TaintTracker
from smartbench.flow.findings import create_sql_injection_finding


def test_simple():
    """测试简单案例。"""
    source = """
async function dangerousEndpoint(req, res) {
    const userId = req.query.userId;
    const query = `SELECT * FROM users WHERE id = ${userId}`;
    await db.run(query);
}
""".strip()

    console.print(Panel("Test: Simple Taint Flow", style="bold blue"))
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
    analyzer.analyze_tree(tree.root_node)

    findings = analyzer._find_sinks(tree.root_node)

    console.print(f"\n[bold]Findings: {len(findings)}[/bold]")
    for f in findings:
        console.print(f"  [red]{f['type']}[/red]")
        console.print(f"  {f['snippet']}")
        for step in f["value"].taint_trace:
            console.print(f"    [cyan]{step.operation}[/cyan]: {step.source_snippet}")

    # 打印作用域变量
    console.print("\n[bold]Scope variables:[/bold]")
    for scope in tracker.scope_manager.get_all_scopes():
        console.print(f"  Scope: {scope.scope_type} {scope.name or ''}")
        for name, value in scope.variables.items():
            console.print(f"    {name}: {value.taint_state}")


def test_real():
    """测试真实案例。"""
    source = """
async function deleteSnippets(snippets, db) {
    const snippetIds = snippets.map((row) => row.id).join(',');
    return await db.run(`DELETE FROM code_snippets WHERE id IN (${snippetIds})`);
}
""".strip()

    console.print("\n" + "=" * 60)
    console.print(Panel("Test: Realistic SQL Injection", style="bold blue"))
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

    # 手动把 snippets 标记为污染
    analyzer.analyze_tree(tree.root_node)

    # 让我们手动污染 snippets 参数来模拟真实情况
    # 找到函数作用域并把 snippets 标记为污染
    for scope in tracker.scope_manager.get_all_scopes():
        if scope.name == "deleteSnippets":
            if "snippets" in scope.variables:
                from smartbench.flow.schema import location_from_node
                from smartbench.flow.taint_simple import TaintTracker

                loc = scope.variables["snippets"].location
                scope.variables["snippets"] = tracker.create_tainted_value(
                    loc, "parameter: snippets", "snippets"
                )

    findings = analyzer._find_sinks(tree.root_node)

    console.print(f"\n[bold]Findings: {len(findings)}[/bold]")
    for f in findings:
        console.print(f"  [red]{f['type']}[/red]")
        console.print(f"  {f['snippet']}")


def main():
    test_simple()
    test_real()


if __name__ == "__main__":
    main()
