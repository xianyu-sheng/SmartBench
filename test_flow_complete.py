#!/usr/bin/env python3
"""
完整测试数据流模块 - 验证它能检测真实问题。
"""

from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.tree import Tree

console = Console()

# 导入我们的模块
from smartbench.flow import DataFlowAnalyzer, AnalysisResult


def test_real_sql_injection_case():
    """测试 Continue 仓库中的真实 SQL 注入案例。"""
    console.print(Panel("Test 1: Real SQL Injection from Continue repo", style="bold blue"))

    # CodeSnippetsIndex.ts 中的真实漏洞案例
    source1 = """
async function deleteSnippets(snippets) {
    const snippetIds = snippets.map((row) => row.id).join(',');
    return await db.run(`DELETE FROM code_snippets WHERE id IN (${snippetIds})`);
}
""".strip()

    # LanceDbIndex.ts 中的真实漏洞案例
    source2 = """
async function getFromCache(uuids) {
    return await sqliteDb.all(
        `SELECT * FROM lance_db_cache WHERE uuid IN (${uuids.map((r) => `'${r.uuid}'`).join(',')})`
    );
}
""".strip()

    analyzer = DataFlowAnalyzer()

    for i, (name, source) in enumerate([
        ("CodeSnippetsIndex.ts", source1),
        ("LanceDbIndex.ts", source2),
    ], 1):
        console.print(f"\n[bold]{i}. {name}[/bold]")
        console.print(Syntax(source, "typescript"))

        result = analyzer.analyze_file(
            file_path=f"test_{name}",
            source=source,
            language="typescript",
        )

        if result.findings:
            console.print(f"\n[green]✓ Found {len(result.findings)} finding(s):[/green]")
            for f in result.findings:
                console.print(f"  [red]• {f.rule_name}[/red]")
                console.print(f"    {f.message}")
                console.print(f"    Confidence: {f.confidence}")

                # 显示证据链
                if f.evidence.taint_trace:
                    tree = Tree("[bold]Evidence Chain[/bold]")
                    for step in f.evidence.taint_trace:
                        tree.add(f"[cyan]{step.operation}[/cyan]: {step.source_snippet}")
                    console.print(tree)

                if f.fix_suggestion:
                    console.print(f"\n  [yellow]Suggestion:[/yellow]")
                    console.print(findings_panel(f.fix_suggestion))
        else:
            console.print(f"\n[yellow]No findings (analysis success: {result.success})[/yellow]")
            if result.error_message:
                console.print(f"  Error: {result.error_message}")


def test_path_traversal_negative_case():
    """测试路径遍历的负面案例 - 不应误报 import 语句。"""
    console.print("\n" + "="*60)
    console.print(Panel("Test 2: Path Traversal - Negative Case (should NOT warn)", style="bold green"))

    source = """
// Import statements with '../' should NOT trigger path traversal warning
import { utils } from '../utils';
import { config } from '../../config';

const safePath = '../static/data.json';

export function loadData() {
    return fs.readFileSync(safePath);
}
""".strip()

    console.print(Syntax(source, "typescript"))

    analyzer = DataFlowAnalyzer()
    result = analyzer.analyze_file(
        file_path="test_imports.ts",
        source=source,
        language="typescript",
    )

    if result.findings:
        console.print(f"\n[red]✗ Unexpected findings: {len(result.findings)}[/red]")
        for f in result.findings:
            console.print(f"  - {f.rule_name}")
    else:
        console.print(f"\n[green]✓ Correct: No false positives[/green]")


def test_safe_parameterized_query():
    """测试安全的参数化查询 - 不应报警。"""
    console.print("\n" + "="*60)
    console.print(Panel("Test 3: Safe Parameterized Query (should NOT warn)", style="bold green"))

    source = """
async function getSafe(id) {
    // Using parameterized query - should NOT warn
    return await db.all('SELECT * FROM table WHERE id = ?', [id]);
}

async function getSafe2(ids) {
    const placeholders = ids.map(() => '?').join(',');
    return await db.all(`SELECT * FROM table WHERE id IN (${placeholders})`, ids);
}
""".strip()

    console.print(Syntax(source, "typescript"))

    analyzer = DataFlowAnalyzer()
    result = analyzer.analyze_file(
        file_path="test_safe.ts",
        source=source,
        language="typescript",
    )

    if result.findings:
        console.print(f"\n[red]✗ Unexpected findings: {len(result.findings)}[/red]")
        for f in result.findings:
            console.print(f"  - {f.rule_name}")
    else:
        console.print(f"\n[green]✓ Correct: No false positives on safe queries[/green]")


def test_complete_flow():
    """测试完整的数据流 - 从 req.query 到 sink。"""
    console.print("\n" + "="*60)
    console.print(Panel("Test 4: Complete Taint Flow (req -> sink)", style="bold blue"))

    source = """
async function dangerousEndpoint(req, res) {
    const userId = req.query.userId;

    // Tainted data flows through variables
    const unsafeId = userId;
    const query = `SELECT * FROM users WHERE id = ${unsafeId}`;

    // Should detect this
    await db.run(query);
}
""".strip()

    console.print(Syntax(source, "typescript"))

    analyzer = DataFlowAnalyzer()
    result = analyzer.analyze_file(
        file_path="test_complete.ts",
        source=source,
        language="typescript",
    )

    if result.findings:
        console.print(f"\n[green]✓ Found {len(result.findings)} finding(s):[/green]")
        for f in result.findings:
            console.print(f"  [red]• {f.rule_name}[/red]")
    else:
        console.print(f"\n[yellow]No findings[/yellow]")


def findings_panel(text: str) -> Panel:
    """Create a panel for findings."""
    return Panel(Syntax(text, "typescript", theme="monokai", word_wrap=True),
                 border_style="dim", padding=(1, 2))


def main():
    """运行所有测试。"""
    console.print("\n[bold magenta]╔══════════════════════════════════════════════════════════════╗[/bold magenta]")
    console.print("[bold magenta]║           SmartBench Data Flow Module Tests                  ║[/bold magenta]")
    console.print("[bold magenta]╚══════════════════════════════════════════════════════════════╝[/bold magenta]")

    try:
        test_real_sql_injection_case()
        test_path_traversal_negative_case()
        test_safe_parameterized_query()
        test_complete_flow()

        console.print("\n" + "="*60)
        console.print("[bold green]All tests completed![/bold green]")

    except Exception as e:
        console.print(f"\n[red]Error:[/red] {e}")
        import traceback
        console.print(traceback.format_exc())


if __name__ == "__main__":
    main()
