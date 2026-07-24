#!/usr/bin/env python3
"""
调试测试 - 逐步检查数据流分析。
"""

from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

console = Console()

from smartbench.flow import DataFlowAnalyzer, AnalysisResult
from smartbench.flow.ast_traversal import create_ast_context, AstWalker
from smartbench.flow.taint import TaintTracker, TypeScriptTaintVisitor
from smartbench.flow.analyzer import TypeScriptSinkDetector
from smartbench.graph.tree_parser import get_parser


def test_simple():
    """测试简单案例。"""
    source = """
async function dangerousEndpoint(req, res) {
    const userId = req.query.userId;
    const query = `SELECT * FROM users WHERE id = ${userId}`;
    await db.run(query);
}
""".strip()

    console.print(Panel("Debug Test: Simple Taint Flow", style="bold blue"))
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
    taint_visitor = TypeScriptTaintVisitor(context, tracker)
    taint_visitor._debug = True

    sink_detector = TypeScriptSinkDetector(context, tracker, taint_visitor)
    sink_detector._debug = True

    walker = AstWalker(context)
    walker.add_visitor(taint_visitor)
    walker.add_visitor(sink_detector)

    console.print("\n[bold]Walking AST...[/bold]")
    walker.walk(tree.root_node)

    console.print(f"\n[bold]Findings: {len(sink_detector.findings)}[/bold]")
    for f in sink_detector.findings:
        console.print(f"  [red]{f.rule_name}[/red]")
        console.print(f"  {f.message}")

    # 打印作用域变量
    console.print("\n[bold]Scope variables:[/bold]")
    for scope in tracker.scope_manager.get_all_scopes():
        console.print(f"  Scope: {scope.scope_type} {scope.name or ''}")
        for name, value in scope.variables.items():
            console.print(f"    {name}: {value.taint_state}")


def main():
    test_simple()


if __name__ == "__main__":
    main()
