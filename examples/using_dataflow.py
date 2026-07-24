#!/usr/bin/env python3
"""
SmartBench DataFlow Analysis - 使用示例

展示如何使用新的数据流分析器。
"""

import tempfile
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

console = Console()


def example_1_basic():
    """基础使用：分析单个文件。"""
    console.print(Panel("Example 1: Basic usage", style="bold green"))

    from smartbench.flow import DataFlowAnalyzer

    code = """
async function search(req) {
    const query = req.query.q;
    const sql = `SELECT * FROM users WHERE name LIKE '%${query}%'`;
    await db.all(sql);
}
"""

    console.print("\nCode to analyze:")
    console.print(Syntax(code, "typescript"))

    analyzer = DataFlowAnalyzer()
    result = analyzer.analyze_file("example.ts", code, "typescript")

    console.print(f"\nResult: {'✅ Success' if result.success else '❌ Failed'}")
    console.print(f"Findings: {len(result.findings)}")

    for f in result.findings:
        console.print(f"\n  [red]{f.rule_name}[/red]")
        console.print(f"  Location: {f.location.file_path}:{f.location.start_row}")
        console.print(f"  Message: {f.message}")

        if hasattr(f, "fix_suggestion") and f.fix_suggestion:
            console.print("\n  [yellow]Fix suggestion:[/yellow]")
            console.print(f"  {f.fix_suggestion}")


def example_2_scan_directory():
    """扫描整个目录。"""
    console.print("\n" + "=" * 60)
    console.print(Panel("Example 2: Scan a directory", style="bold blue"))

    from smartbench.core import (
        AdapterRegistry,
        RuleRegistry,
        UnifiedDiagnosticConfig,
        UnifiedDiagnosticEngine,
        register_all_adapters,
        register_builtin_rules,
    )

    # 创建一个隔离且自动清理的测试项目
    with tempfile.TemporaryDirectory(prefix="smartbench-dataflow-") as tmpdir:
        test_dir = Path(tmpdir)

        (test_dir / "vulnerable.ts").write_text("""
async function unsafe(req) {
    const id = req.params.id;
    await db.run(`DELETE FROM t WHERE id = ${id}`);
}
""", encoding="utf-8")

        (test_dir / "safe.ts").write_text("""
async function safe(req) {
    const id = req.params.id;
    await db.run('DELETE FROM t WHERE id = ?', [id]);
}
""", encoding="utf-8")

        console.print(f"\nScanning: {test_dir}")

        # 设置引擎
        adapters = AdapterRegistry()
        register_all_adapters(adapters)

        rules = RuleRegistry()
        register_builtin_rules(rules)

        engine = UnifiedDiagnosticEngine(adapters, rules)

        config = UnifiedDiagnosticConfig(
            use_llm_rules=False,
            use_static_rules=True,
            languages=["typescript"],
        )

        result = engine.diagnose(test_dir, config)

        console.print(f"Total findings: {len(result.findings)}")

        if result.findings:
            from collections import defaultdict

            by_rule = defaultdict(list)
            for f in result.findings:
                by_rule[f.rule_id].append(f)

            console.print("\nBy rule:")
            for rule_id, findings in sorted(by_rule.items()):
                console.print(f"  {rule_id}: {len(findings)}")


def example_3_custom_integration():
    """自定义集成。"""
    console.print("\n" + "=" * 60)
    console.print(Panel("Example 3: Custom integration", style="bold magenta"))

    console.print("""
[bold]The DataFlow analyzer can be integrated in several ways:[/bold]

  1. Direct API: Use `DataFlowAnalyzer.analyze_file()` directly
  2. Rule system: Enabled automatically via `DataFlowSecurityRule`
  3. Custom use: Build your own analysis on top of the primitives

[bold]Architecture Overview:[/bold]

  smartbench/flow/
  ├── __init__.py          # Public API exports
  ├── schema.py            # Core data structures
  ├── ast_traversal.py     # Tree-sitter AST helpers
  ├── scope.py             # Variable scope tracking
  ├── taint_simple.py      # Simple taint analysis (current)
  ├── sinks.py             # Dangerous sink definitions
  ├── sources.py           # Taint source definitions
  ├── findings.py          # Finding + evidence chain
  └── analyzer.py          # Main analysis engine
""")


def main():
    console.print("\n╔══════════════════════════════════════════════════════════════╗")
    console.print("║       SmartBench DataFlow Analysis - Usage Examples           ║")
    console.print("╚══════════════════════════════════════════════════════════════╝")

    example_1_basic()
    example_2_scan_directory()
    example_3_custom_integration()

    console.print("\n" + "=" * 60)
    console.print(Panel("✅ Ready to use!", style="bold green"))
    console.print("\n[bold]Next steps:[/bold]")
    console.print("  • Run on your own projects")
    console.print("  • Add more source/sink patterns")
    console.print("  • Integrate into CI/CD pipelines")


if __name__ == "__main__":
    main()
