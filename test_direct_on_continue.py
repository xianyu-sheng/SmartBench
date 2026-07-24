#!/usr/bin/env python3
"""
直接在 Continue 仓库真实文件上测试 DataFlowAnalyzer。
"""

from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

console = Console()

from smartbench.flow import DataFlowAnalyzer
from smartbench.graph.tree_parser import get_parser
from smartbench.flow.ast_traversal import create_ast_context
from smartbench.flow.taint_simple import SimpleTaintAnalyzer, TaintTracker
from smartbench.flow.schema import location_from_node


def test_code_snippets_index():
    """测试 CodeSnippetsIndex.ts - 手动标记污染。"""
    filepath = Path("/home/xianyu-sheng/continue/core/indexing/CodeSnippetsIndex.ts")
    if not filepath.exists():
        console.print(f"[yellow]File not found: {filepath}[/yellow]")
        return

    source = filepath.read_text()

    console.print(Panel(f"Analyzing: {filepath.name}", style="bold blue"))

    # 找到 deleteSnippets 函数
    lines = source.split("\n")
    start = None
    end = None
    in_func = False
    brace_count = 0
    for i, line in enumerate(lines):
        if "deleteSnippets" in line:
            start = i
            in_func = True
        if in_func:
            brace_count += line.count("{")
            brace_count -= line.count("}")
            if brace_count == 0 and start is not None:
                end = i + 1
                break

    if start is not None and end is not None:
        func_code = "\n".join(lines[start:end])
        console.print("\n[bold]Found function:[/bold]")
        console.print(Syntax(func_code, "typescript"))

        # 测试这个函数
        source_bytes = func_code.encode("utf-8")
        parser = get_parser("typescript")
        tree = parser.parse(source_bytes)

        context = create_ast_context(str(filepath), func_code)
        tracker = TaintTracker(context)
        analyzer = SimpleTaintAnalyzer(context, tracker)

        # 手动标记 snippets 和 db 为污染/已知
        variables = {}
        loc = location_from_node(str(filepath), tree.root_node)
        variables["snippets"] = tracker.create_tainted_value(
            loc, "parameter: snippets", "snippets"
        )

        analyzer._collect_variables(tree.root_node, variables, [{}])

        print(f"\nVariables found: {list(variables.keys())}")
        for k, v in variables.items():
            print(f"  {k}: {v.taint_state}")

        findings = analyzer._find_sink_calls(tree.root_node, variables)

        if findings:
            console.print(f"\n[bold red]✓ Found {len(findings)} issue(s):[/bold red]")
            for f in findings:
                console.print(f"  {f['type']}: {f['snippet']}")
        else:
            console.print(f"\n[yellow]No findings[/yellow]")


def test_lance_db_index():
    """测试 LanceDbIndex.ts。"""
    console.print("\n" + "=" * 60)

    filepath = Path("/home/xianyu-sheng/continue/core/indexing/LanceDbIndex.ts")
    if not filepath.exists():
        console.print(f"[yellow]File not found: {filepath}[/yellow]")
        return

    source = filepath.read_text()

    console.print(Panel(f"Analyzing: {filepath.name}", style="bold blue"))

    # 找有问题的函数
    lines = source.split("\n")
    candidates = []

    for i, line in enumerate(lines):
        if ".all" in line and "SELECT" in line and "${" in line:
            # 往前找函数定义
            start = i
            while start > 0 and "function" not in lines[start]:
                start -= 1
            end = i
            brace_count = 0
            for j in range(i, len(lines)):
                brace_count += lines[j].count("{")
                brace_count -= lines[j].count("}")
                if brace_count == 0:
                    end = j + 1
                    break
            if start < end:
                candidates.append("\n".join(lines[start:end]))

    if candidates:
        console.print("\n[bold]Found candidate function(s):[/bold]")
        console.print(Syntax(candidates[0], "typescript"))

        # 分析
        func_code = candidates[0]
        source_bytes = func_code.encode("utf-8")
        parser = get_parser("typescript")
        tree = parser.parse(source_bytes)

        context = create_ast_context(str(filepath), func_code)
        tracker = TaintTracker(context)
        analyzer = SimpleTaintAnalyzer(context, tracker)

        # 找出参数名
        params = []
        import tree_sitter
        def find_params(node):
            if node.type == "formal_parameters":
                for child in node.children:
                    if child.type == "required_parameter":
                        for sub_child in child.children:
                            if sub_child.type == "identifier":
                                from smartbench.flow.ast_traversal import get_node_text
                                name = get_node_text(sub_child, source_bytes)
                                params.append(name)
            for child in node.children:
                find_params(child)

        find_params(tree.root_node)

        variables = {}
        loc = location_from_node(str(filepath), tree.root_node)
        for param in params:
            variables[param] = tracker.create_tainted_value(
                loc, f"parameter: {param}", param
            )
        print(f"Marked as tainted: {params}")

        analyzer._collect_variables(tree.root_node, variables, [{}])
        findings = analyzer._find_sink_calls(tree.root_node, variables)

        if findings:
            console.print(f"\n[bold red]✓ Found {len(findings)} issue(s):[/bold red]")
            for f in findings:
                console.print(f"  {f['type']}: {f['snippet']}")
                console.print(f"  Taint trace:")
                for step in f['value'].taint_trace[:5]:  # 只显示前5个
                    console.print(f"    [{step.operation}] {step.source_snippet}")
        else:
            console.print(f"\n[yellow]No findings[/yellow]")
    else:
        console.print("\n[yellow]No candidate functions found with .all and ${[/yellow]")


if __name__ == "__main__":
    test_code_snippets_index()
    test_lance_db_index()
