#!/usr/bin/env python3
"""
测试我们的修复是否有效！
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
    console.print("║       SmartBench - 测试修复后的扫描                           ║")
    console.print("╚══════════════════════════════════════════════════════════════╝")
    console.print(f"\n项目: {project_path}")

    # 先手动确认已知漏洞
    console.print("\n" + "=" * 60)
    console.print(Panel("已知漏洞 - 手动验证", style="bold cyan"))

    known_files = [
        project_path / "core/indexing/CodeSnippetsIndex.ts",
        project_path / "core/indexing/LanceDbIndex.ts",
    ]

    real_vulns = []

    for fpath in known_files:
        if fpath.exists():
            source = fpath.read_text()
            lines = source.split("\n")

            # 查找可疑模式
            for i, line in enumerate(lines):
                if ("db.run" in line or "db.all" in line) and "`" in line and "${" in line:
                    start = max(0, i - 4)
                    end = min(len(lines), i + 5)
                    console.print(f"\n  [red]可能的 SQL 注入在第 {i+1} 行:[/red]")
                    console.print(Syntax("\n".join(lines[start:end]), "typescript"))
                    real_vulns.append((fpath, i + 1))

    # 运行完整的 SmartBench
    console.print("\n" + "=" * 60)
    console.print(Panel("完整 SmartBench 扫描（修复后）", style="bold blue"))

    from smartbench.core import (
        AdapterRegistry, RuleRegistry, UnifiedDiagnosticConfig,
        UnifiedDiagnosticEngine, register_all_adapters, register_builtin_rules
    )

    console.print("\n[bold]设置引擎...[/bold]")

    adapters = AdapterRegistry()
    register_all_adapters(adapters)

    rules = RuleRegistry()
    register_builtin_rules(rules)

    # 列出注册的规则
    console.print("\n[bold]已注册规则:[/bold]")
    for rule_id in rules.list_rule_ids():
        console.print(f"  - {rule_id}")

    engine = UnifiedDiagnosticEngine(adapters, rules)

    # 使用默认的最小置信度 0.7
    config = UnifiedDiagnosticConfig(
        use_llm_rules=False,
        use_static_rules=True,
        languages=["typescript", "javascript", "python"],
        min_confidence=0.7,
    )

    console.print("\n[bold]运行诊断...[/bold] (这可能需要一点时间)")
    result = engine.diagnose(project_path, config)

    # 显示结果
    console.print("\n" + "=" * 60)
    console.print(Panel("📊 扫描结果", style="bold magenta"))

    console.print(f"\n📁 扫描文件数: 未知（来自 IR）")
    console.print(f"⏱️  耗时: {result.duration_ms:.1f}ms")
    console.print(f"🔍 总发现数: {len(result.findings)}")

    if result.findings:
        from collections import defaultdict
        by_rule = defaultdict(list)
        for f in result.findings:
            by_rule[f.rule_id].append(f)

        console.print("\n[bold]按规则分组的发现:[/bold]")

        table = Table(show_header=True)
        table.add_column("Rule ID", style="cyan")
        table.add_column("Count", justify="right")
        table.add_column("Severity")

        for rule_id, findings in sorted(by_rule.items(), key=lambda x: -len(x[1])):
            severity = findings[0].severity.value
            table.add_row(rule_id, str(len(findings)), severity)

        console.print(table)

    # 显示安全相关的发现
    console.print("\n" + "=" * 60)
    console.print(Panel("🔍 安全发现", style="bold yellow"))

    security_findings = [
        f for f in result.findings
        if any(x in f.rule_id.lower() for x in ["injection", "path_traversal", "security", "secret", "sql"])
    ]

    if security_findings:
        console.print(f"\n[bold red]安全发现: {len(security_findings)}[/bold red]\n")

        for f in security_findings:
            console.print(f"[bold]{f.rule_name}[/bold] (confidence: {f.confidence:.2f})")
            loc = f"{Path(f.location.file_path).name}:{f.location.line_start}"
            console.print(f"  [{f.severity.value}] [cyan]{loc}[/cyan]")
            console.print(f"  {f.message}")

            # 显示代码片段
            try:
                full_path = Path(project_path) / f.location.file_path
                if full_path.exists():
                    source = full_path.read_text()
                    lines = source.split("\n")
                    start = max(0, f.location.line_start - 2)
                    end = min(len(lines), f.location.line_start + 2)
                    console.print(Syntax("\n".join(lines[start:end]), "typescript"))
            except Exception:
                pass

            # 显示元数据（如果有证据链）
            if f.metadata:
                console.print(f"  Metadata: {f.metadata}")

            console.print()

    else:
        console.print("\n[green]没有发现安全问题（使用过滤后的规则）[/green]")

    # 检查是否找到了已知的真实漏洞！
    console.print("\n" + "=" * 60)
    console.print(Panel("✅ 验证: 是否找到了已知漏洞?", style="bold green"))

    found_any = False
    for f in result.findings:
        fpath = Path(f.location.file_path).name
        line = f.location.line_start

        for (known_path, known_line) in real_vulns:
            known_name = known_path.name
            if fpath == known_name and abs(line - known_line) <= 3:
                console.print(f"\n  [green]✓ 找到了已知漏洞! {fpath}:{line}[/green]")
                console.print(f"    Rule: {f.rule_id}")
                console.print(f"    Confidence: {f.confidence:.2f}")
                found_any = True

    if not found_any:
        console.print("\n  [yellow]⚠️  没有找到已知漏洞 - 可能还需要调整数据流分析[/yellow]")
        console.print("  让我们尝试降低置信度阈值...")

        # 尝试降低置信度阈值再次运行
        console.print("\n  " + "=" * 50)
        console.print("  尝试降低置信度阈值 (min_confidence = 0.0)...")

        config_low = UnifiedDiagnosticConfig(
            use_llm_rules=False,
            use_static_rules=True,
            languages=["typescript", "javascript", "python"],
            min_confidence=0.0,
        )

        result_low = engine.diagnose(project_path, config_low)

        console.print(f"\n  低置信度扫描发现数: {len(result_low.findings)}")

        security_findings_low = [
            f for f in result_low.findings
            if any(x in f.rule_id.lower() for x in ["injection", "path_traversal", "security", "secret", "sql"])
        ]

        console.print(f"  低置信度安全发现数: {len(security_findings_low)}")

        for f in security_findings_low:
            fpath = Path(f.location.file_path).name
            line = f.location.line_start

            for (known_path, known_line) in real_vulns:
                known_name = known_path.name
                if fpath == known_name and abs(line - known_line) <= 3:
                    console.print(f"\n    [green]✓ 在低置信度下找到了已知漏洞! {fpath}:{line}[/green]")
                    console.print(f"    Rule: {f.rule_id}")
                    console.print(f"    Confidence: {f.confidence:.2f}")
                    found_any = True

    # 保存结果
    output_file = Path("/home/xianyu-sheng/SmartBench/continue_scan_results_fixed.json")
    import json
    with open(output_file, "w") as f:
        json.dump({
            "findings": [f.to_dict() for f in result.findings],
            "stats": result.stats,
            "duration_ms": result.duration_ms,
            "errors": result.errors,
        }, f, ensure_ascii=False, indent=2)

    console.print(f"\n💾 完整结果已保存到: {output_file}")

    console.print("\n" + "=" * 60)
    console.print(Panel("✅ 扫描完成! 总结", style="bold green"))

    console.print(f"\n[bold]关键改进:[/bold]")
    console.print("  - 旧的正则安全规则默认禁用 (PathTraversal, CommandInjection)")
    console.print("  - 数据流分析更激进，可以检测函数参数")
    console.print("  - 添加了置信度阈值过滤 (min_confidence = 0.7)")
    console.print("  - 硬编码密钥规则跳过测试文件")

    console.print(f"\n[bold]结果对比 (继续项目):[/bold]")
    console.print("  - 之前: ~469 个发现（很多误报）")
    console.print(f"  - 现在: {len(result.findings)} 个发现")


if __name__ == "__main__":
    main()
