#!/usr/bin/env python3
"""
简化版 - 用 SmartBench 扫描 continue 项目。
"""

from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

console = Console()


def main():
    project_path = Path("/home/xianyu-sheng/continue").resolve()

    console.print("\n╔══════════════════════════════════════════════════════════════╗")
    console.print("║          SmartBench - Continue Project Scan                   ║")
    console.print("╚══════════════════════════════════════════════════════════════╝")
    console.print(f"\nProject: {project_path}")

    # 先看一下我们已知的那两个有问题的文件
    console.print("\n" + "=" * 60)
    console.print(Panel("Known Files - Manual Review", style="bold cyan"))

    known_files = [
        project_path / "core/indexing/CodeSnippetsIndex.ts",
        project_path / "core/indexing/LanceDbIndex.ts",
    ]

    for fpath in known_files:
        if fpath.exists():
            console.print(f"\n[bold]{fpath.name}[/bold]")
            source = fpath.read_text()
            lines = source.split("\n")

            # 查找有问题的模式
            for i, line in enumerate(lines):
                if ("db.run" in line or "db.all" in line) and ("`" in line and "${" in line):
                    start = max(0, i - 4)
                    end = min(len(lines), i + 5)
                    console.print(f"\n  [red]Potential SQL injection at line {i+1}:[/red]")
                    console.print(Syntax("\n".join(lines[start:end]), "typescript"))

    # 运行完整的 SmartBench
    console.print("\n" + "=" * 60)
    console.print(Panel("Full SmartBench Scan", style="bold blue"))

    from smartbench.core import (
        AdapterRegistry, RuleRegistry, UnifiedDiagnosticConfig,
        UnifiedDiagnosticEngine, register_all_adapters, register_builtin_rules
    )

    console.print("\n[bold]Setting up engine...[/bold]")

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

    console.print("[bold]Running diagnose...[/bold] (this may take a moment)")
    result = engine.diagnose(project_path, config)

    # 显示结果
    console.print("\n" + "=" * 60)
    console.print(Panel("📊 Scan Results", style="bold magenta"))

    console.print(f"\n📁 Files scanned: {result.stats.get('files_scanned', 0)}")
    console.print(f"⏱️  Duration: {result.duration_ms:.1f}ms")
    console.print(f"🔍 Total findings: {len(result.findings)}")

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

    # 显示安全相关的 findings
    console.print("\n" + "=" * 60)
    console.print(Panel("🔍 Security Findings", style="bold yellow"))

    security_findings = [
        f for f in result.findings
        if any(x in f.rule_id.lower() for x in ["injection", "path_traversal", "security"])
    ]

    if security_findings:
        console.print(f"\n[bold red]Security Findings: {len(security_findings)}[/bold red]\n")

        for f in security_findings:
            console.print(f"[bold]{f.rule_name}[/bold]")
            loc = f"{Path(f.location.file_path).name}:{f.location.line_start}"
            console.print(f"  [{f.severity.value}] [cyan]{loc}[/cyan]")
            console.print(f"  {f.message}")
            console.print()

            # 显示代码片段
            try:
                full_path = Path(project_path) / f.location.file_path
                if full_path.exists():
                    source = full_path.read_text()
                    lines = source.split("\n")
                    start = max(0, f.location.line_start - 2)
                    end = min(len(lines), f.location.line_start + 2)
                    console.print(Syntax("\n".join(lines[start:end]), "typescript"))
            except:
                pass
            console.print()

    else:
        console.print("\n[green]No security injection findings found by the rule-based checks[/green]")

    # 保存结果
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


if __name__ == "__main__":
    main()
