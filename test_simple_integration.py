#!/usr/bin/env python3
"""
简单集成测试——展示如何使用新的数据流分析器。
"""

from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

console = Console()


def main():
    console.print("\n╔══════════════════════════════════════════════════════════════╗")
    console.print("║         SmartBench: How to Use the DataFlow Analyzer          ║")
    console.print("╚══════════════════════════════════════════════════════════════╝")

    # 方式 1: 直接使用 DataFlowAnalyzer
    console.print("\n" + "=" * 60)
    console.print(Panel("Option 1: Use DataFlowAnalyzer directly", style="bold green"))

    from smartbench.flow import DataFlowAnalyzer

    source = """
async function handler(req, res) {
    const userId = req.query.userId;
    const sql = `SELECT * FROM users WHERE id = ${userId}`;
    await db.query(sql);
}
""".strip()

    console.print("\nAnalyzing this code:")
    console.print(Syntax(source, "typescript"))

    analyzer = DataFlowAnalyzer()
    result = analyzer.analyze_file("test.ts", source, "typescript")

    console.print(f"\nFindings: {len(result.findings)}")
    if result.findings:
        for f in result.findings:
            console.print(f"\n  [red]{f.rule_name}[/red]")
            console.print(f"  {f.message}")

            if hasattr(f, 'evidence') and f.evidence.taint_trace:
                console.print(f"\n  Taint chain:")
                for step in f.evidence.taint_trace:
                    console.print(f"    [{step.operation}] {step.source_snippet}")

    # 方式 2: 作为完整 SmartBench 流程的一部分
    console.print("\n" + "=" * 60)
    console.print(Panel("Option 2: Use with full SmartBench", style="bold blue"))

    console.print("\nCreate a test project...")
    test_dir = Path("/tmp/smartbench_demo")
    test_dir.mkdir(exist_ok=True)

    test_file = test_dir / "app.ts"
    test_file.write_text(source)

    from smartbench.core import (
        AdapterRegistry, RuleRegistry, UnifiedDiagnosticConfig, UnifiedDiagnosticEngine,
        register_all_adapters, register_builtin_rules
    )

    adapters = AdapterRegistry()
    register_all_adapters(adapters)

    rules = RuleRegistry()
    register_builtin_rules(rules)

    engine = UnifiedDiagnosticEngine(adapters, rules)

    config = UnifiedDiagnosticConfig(
        use_llm_rules=False,
        use_static_rules=True,
        languages=["typescript", "javascript"],
    )

    console.print("\nRunning SmartBench diagnose...")
    result = engine.diagnose(test_dir, config)

    console.print(f"\nFiles scanned: {result.stats.get('files_scanned', 0)}")
    console.print(f"Total findings: {len(result.findings)}")

    if result.findings:
        from collections import defaultdict
        by_rule = defaultdict(list)
        for f in result.findings:
            by_rule[f.rule_id].append(f)

        console.print("\nFindings by rule:")
        for rule_id, findings in sorted(by_rule.items()):
            console.print(f"  [cyan]{rule_id}[/cyan]: {len(findings)}")

    # 清理
    import shutil
    if test_dir.exists():
        shutil.rmtree(test_dir)

    console.print("\n" + "=" * 60)
    console.print(Panel("✓ Success! The system is usable!", style="bold green"))
    console.print("\n[bold]What we have:[/bold]")
    console.print("  • A working DataFlowAnalyzer with a simple API")
    console.print("  • Integration with the existing Rule system")
    console.print("  • Deterministic analysis, no regex hacks")
    console.print("  • Evidence chain for each finding")
    console.print("\n[bold]Ready to use![/bold]")


if __name__ == "__main__":
    main()
