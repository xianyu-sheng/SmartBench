#!/usr/bin/env python3
"""
在 Continue 仓库的真实文件上测试分析器。
"""

from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

console = Console()

from smartbench.flow import DataFlowAnalyzer


def test_code_snippets_index():
    """测试 CodeSnippetsIndex.ts。"""
    filepath = Path("/home/xianyu-sheng/continue/core/indexing/CodeSnippetsIndex.ts")
    if not filepath.exists():
        console.print(f"[yellow]File not found: {filepath}[/yellow]")
        return

    source = filepath.read_text()

    console.print(Panel(f"Testing: {filepath.name}", style="bold blue"))

    # 显示有问题的部分
    lines = source.split("\n")
    for i, line in enumerate(lines):
        if "db.run" in line:
            start = max(0, i-3)
            end = min(len(lines), i+4)
            console.print(Syntax("\n".join(lines[start:end]), "typescript"))
            break

    analyzer = DataFlowAnalyzer()
    result = analyzer.analyze_file(str(filepath), source, "typescript")

    console.print(f"\n[bold]Findings: {len(result.findings)}[/bold]")

    if result.findings:
        for f in result.findings:
            console.print(f"\n  [red]{f.rule_name}[/red]")
            console.print(f"  {f.message}")
            console.print(f"  Line: {f.location.start_row}")
            console.print(f"  Evidence:")
            for step in f.evidence.taint_trace:
                console.print(f"    [{step.operation}] {step.source_snippet}")
    else:
        console.print("\n[yellow]No findings found in this file[/yellow]")
        console.print("(Note: This is expected because 'snippets' parameter isn't automatically marked as tainted)")


def test_with_manual_taint():
    """使用手动标记污染的测试。"""
    console.print("\n" + "=" * 60)
    console.print(Panel("Test with manual taint marking", style="bold green"))

    source = """
async function deleteSnippets(snippets, db) {
    const snippetIds = snippets.map((row) => row.id).join(',');
    return await db.run(`DELETE FROM code_snippets WHERE id IN (${snippetIds})`);
}
""".strip()

    console.print(Syntax(source, "typescript"))

    from smartbench.graph.tree_parser import get_parser
    from smartbench.flow.ast_traversal import create_ast_context
    from smartbench.flow.taint_simple import SimpleTaintAnalyzer, TaintTracker
    from smartbench.flow.schema import location_from_node

    source_bytes = source.encode("utf-8")
    parser = get_parser("typescript")
    tree = parser.parse(source_bytes)

    context = create_ast_context("test.ts", source)
    tracker = TaintTracker(context)
    analyzer = SimpleTaintAnalyzer(context, tracker)
    analyzer._debug = False

    # 手动标记 snippets 为污染
    variables = {}
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
        console.print(f"  Taint trace:")
        for step in f['value'].taint_trace:
            console.print(f"    [cyan]{step.operation}[/cyan]: {step.source_snippet}")


def test_lance_db_index():
    """测试 LanceDbIndex.ts。"""
    console.print("\n" + "=" * 60)

    filepath = Path("/home/xianyu-sheng/continue/core/indexing/LanceDbIndex.ts")
    if not filepath.exists():
        console.print(f"[yellow]File not found: {filepath}[/yellow]")
        return

    source = filepath.read_text()

    console.print(Panel(f"Testing: {filepath.name}", style="bold blue"))

    lines = source.split("\n")
    found = False
    for i, line in enumerate(lines):
        if ".all" in line and "SELECT" in line:
            start = max(0, i-5)
            end = min(len(lines), i+6)
            console.print(Syntax("\n".join(lines[start:end]), "typescript"))
            found = True
            break

    if not found:
        console.print("\n[yellow]No .all with SELECT found[/yellow]")

    analyzer = DataFlowAnalyzer()
    result = analyzer.analyze_file(str(filepath), source, "typescript")
    print(f"\nFindings: {len(result.findings)}")


if __name__ == "__main__":
    #test_code_snippets_index()
    test_with_manual_taint()
    #test_lance_db_index()
