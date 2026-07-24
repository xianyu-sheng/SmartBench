#!/usr/bin/env python3
"""
用 SmartBench 扫描 continue 项目。
"""

from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()


def main():
    project_path = Path("/home/xianyu-sheng/continue").resolve()

    console.print("\n╔══════════════════════════════════════════════════════════════╗")
    console.print("║          SmartBench - Continue Project Scan                   ║")
    console.print("╚══════════════════════════════════════════════════════════════╝")
    console.print(f"\nProject: {project_path}")

    # 先直接用我们的数据流分析器找我们已知的那两个文件
    console.print("\n" + "=" * 60)
    console.print(Panel("Phase 1: Known vulnerability check", style="bold cyan"))

    from smartbench.flow import DataFlowAnalyzer
    analyzer = DataFlowAnalyzer()

    known_files = [
        "core/indexing/CodeSnippetsIndex.ts",
        "core/indexing/LanceDbIndex.ts",
    ]

    flow_findings = []

    for rel_path in known_files:
        full_path = project_path / rel_path
        if full_path.exists():
            console.print(f"\n[bold]Scanning: {rel_path}[/bold]")
            source = full_path.read_text()

            # 我们用一个自定义的方式来分析——标记所有函数参数为可能污染
            findings = analyze_with_all_params_tainted(
                str(full_path), source, analyzer
            )

            if findings:
                console.print(f"  [red]Found {len(findings)} issues[/red]")
                flow_findings.extend(findings)
            else:
                console.print(f"  [green]No issues found with strict analysis[/green]")
        else:
            console.print(f"\n[yellow]File not found: {rel_path}[/yellow]")

    # 运行完整的 SmartBench 引擎
    console.print("\n" + "=" * 60)
    console.print(Panel("Phase 2: Full SmartBench scan", style="bold blue"))

    from smartbench.core import (
        AdapterRegistry, RuleRegistry, UnifiedDiagnosticConfig,
        UnifiedDiagnosticEngine, register_all_adapters, register_builtin_rules
    )

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console
    ) as progress:
        task = progress.add_task("Setting up engine...", total=None)

        adapters = AdapterRegistry()
        register_all_adapters(adapters)

        rules = RuleRegistry()
        register_builtin_rules(rules)

        engine = UnifiedDiagnosticEngine(adapters, rules)

        config = UnifiedDiagnosticConfig(
            use_llm_rules=False,
            use_static_rules=True,
            languages=["typescript", "javascript", "python"],
        )

        progress.update(task, description="Running diagnose...")

        result = engine.diagnose(project_path, config)

        progress.stop()

    # 显示完整结果
    console.print("\n" + "=" * 60)
    console.print(Panel("📊 Scan Results", style="bold magenta"))

    if result.errors:
        console.print("\n[yellow]Errors:[/yellow]")
        for err in result.errors[:5]:
            console.print(f"  - {err}")
        if len(result.errors) > 5:
            console.print(f"  ... and {len(result.errors) - 5} more")
        console.print()

    console.print(f"📁 Files scanned: {result.stats.get('files_scanned', 0)}")
    console.print(f"⏱️  Duration: {result.duration_ms:.1f}ms")
    console.print(f"🔍 Total findings: {len(result.findings)}")

    # 按规则分组
    if result.findings:
        from collections import defaultdict
        by_rule = defaultdict(list)
        for f in result.findings:
            by_rule[f.rule_id].append(f)

        console.print("\n[bold]Findings by rule:[/bold]")

        table = Table(show_header=True)
        table.add_column("Rule ID", style="cyan")
        table.add_column("Count", justify="right")
        table.add_column("Severity")

        for rule_id, findings in sorted(by_rule.items(), key=lambda x: -len(x[1])):
            severity = findings[0].severity.value
            table.add_row(rule_id, str(len(findings)), severity)

        console.print(table)

    # 详细显示数据流相关的 findings 和安全相关的 findings
    console.print("\n" + "=" * 60)
    console.print(Panel("🔍 Detailed Findings", style="bold yellow"))

    security_findings = [
        f for f in result.findings if "injection" in f.rule_id.lower() or "path_traversal" in f.rule_id.lower()
    ]

    if security_findings:
        console.print(f"\n[bold red]Security Findings: {len(security_findings)}[/bold red]\n")

        for f in security_findings[:10]:
            console.print(f"[bold]{f.rule_name}[/bold]")
            loc = f"{Path(f.location.file_path).name}:{f.location.line_start}"
            console.print(f"  [{f.severity.value}] [cyan]{loc}[/cyan]")
            console.print(f"  {f.message}")
            console.print()

        if len(security_findings) > 10:
            console.print(f"... and {len(security_findings) - 10} more")

    else:
        console.print("\n[green]No security injection findings found[/green]")

    # 保存完整结果
    output_file = Path("/home/xianyu-sheng/SmartBench/continue_scan_results.json")
    import json
    with open(output_file, "w") as f:
        json.dump({
            "findings": [f.to_dict() for f in result.findings],
            "stats": result.stats,
            "duration_ms": result.duration_ms,
            "errors": result.errors,
        }, f, ensure_ascii=False, indent=2)

    console.print(f"\n💾 Full results saved to: {output_file}")

    console.print("\n" + "=" * 60)
    console.print(Panel("✅ Scan complete! Ready for review.", style="bold green"))


def analyze_with_all_params_tainted(file_path: str, source: str, analyzer):
    """用一个更宽松的方式分析——标记所有函数参数为污染。"""
    from smartbench.graph.tree_parser import get_parser
    from smartbench.flow.ast_traversal import create_ast_context
    from smartbench.flow.taint_simple import SimpleTaintAnalyzer, TaintTracker
    from smartbench.flow.findings import create_sql_injection_finding

    source_bytes = source.encode("utf-8", errors="replace")
    parser = get_parser("typescript")
    if not parser:
        return []

    tree = parser.parse(source_bytes)
    context = create_ast_context(file_path, source)
    tracker = TaintTracker(context)
    simple_analyzer = SimpleTaintAnalyzer(context, tracker)
    simple_analyzer._debug = False

    # 自定义分析：找到所有函数，标记所有参数为污染
    findings = []

    def find_functions(node):
        if node.type in ("function_declaration", "arrow_function", "method_definition"):
            analyze_function_with_all_params_tainted(node, context, tracker, findings)
        for child in node.children:
            find_functions(child)

    def analyze_function_with_all_params_tainted(func_node, context, tracker, findings):
        variables = {}

        # 收集所有参数并标记为污染
        params_node = get_child_by_field_name(func_node, "parameters")
        loc = location_from_node(file_path, func_node) if 'location_from_node' in globals() else None
        from smartbench.flow.schema import location_from_node
        loc = location_from_node(file_path, func_node)

        param_names = []
        if params_node:
            for child in params_node.children:
                if child.type == "required_parameter":
                    for sub_child in child.children:
                        if sub_child.type == "identifier":
                            from smartbench.flow.ast_traversal import get_node_text
                            name = get_node_text(sub_child, context.source_bytes)
                            param_names.append(name)
                            variables[name] = tracker.create_tainted_value(
                                loc, f"parameter: {name}", name
                            )

        # 收集变量
        body_node = get_child_by_field_name(func_node, "body")
        if body_node:
            simple_analyzer._collect_variables(body_node, variables, [{}])

        # 找 sinks
        func_findings = simple_analyzer._find_sink_calls(func_node, variables)
        for f in func_findings:
            if f["type"] == "sql_injection":
                findings.append(create_sql_injection_finding(
                    f["location"], f["snippet"], f["value"], context.source
                ))

    find_functions(tree.root_node)

    if findings:
        console.print("\n  Details:")
        for f in findings:
            console.print(f"    [red]{f.rule_name}[/red]")
            console.print(f"    Line {f.location.start_row}: {f.message}")
            # 显示相关代码
            lines = source.split("\n")
            start = max(0, f.location.start_row - 2)
            end = min(len(lines), f.location.end_row + 1)
            snippet = "\n".join(lines[start:end])
            console.print(Syntax(snippet, "typescript"))

    return findings


if __name__ == "__main__":
    main()
