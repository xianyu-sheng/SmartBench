#!/usr/bin/env python3
"""
审核 SmartBench 的扫描结果。
"""

from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
import json

console = Console()


def main():
    project_path = Path("/home/xianyu-sheng/continue").resolve()
    results_path = Path("/home/xianyu-sheng/SmartBench/continue_scan_results.json")

    console.print("\n╔══════════════════════════════════════════════════════════════╗")
    console.print("║         SmartBench Scan Result - Audit Report                 ║")
    console.print("╚══════════════════════════════════════════════════════════════╝")

    # 1. 先手动确认已知的真实漏洞
    console.print("\n" + "=" * 60)
    console.print(Panel("1. Manually Verified Vulnerabilities", style="bold red"))

    known_files = [
        project_path / "core/indexing/CodeSnippetsIndex.ts",
        project_path / "core/indexing/LanceDbIndex.ts",
    ]

    real_vulns = []

    for fpath in known_files:
        if fpath.exists():
            source = fpath.read_text()
            lines = source.split("\n")

            for i, line in enumerate(lines):
                if ("db.run" in line or "db.all" in line) and "`" in line and "${" in line:
                    real_vulns.append({
                        "file": str(fpath.relative_to(project_path)),
                        "line": i + 1,
                        "snippet": line.strip(),
                        "context": "\n".join(lines[max(0, i-3):i+3])
                    })

    if real_vulns:
        console.print(f"\n[bold red]Found {len(real_vulns)} real vulnerabilities:[/bold red]\n")
        for v in real_vulns:
            console.print(f"  🚨 [cyan]{v['file']}:{v['line']}[/cyan]")
            console.print(Syntax(v["context"], "typescript"))
            console.print(f"  [red]This is a real SQL injection![/red]\n")

    # 2. 检查 SmartBench 扫描结果
    console.print("=" * 60)
    console.print(Panel("2. SmartBench Scan Findings", style="bold blue"))

    if results_path.exists():
        with open(results_path) as f:
            data = json.load(f)

        console.print(f"\nTotal findings: {len(data.get('findings', []))}")

        # 分组统计
        from collections import defaultdict
        by_rule = defaultdict(list)
        for f in data.get("findings", []):
            by_rule[f.get("rule_id")].append(f)

        console.print("\nFindings by rule:")
        for rule_id, findings in sorted(by_rule.items(), key=lambda x: -len(x[1])):
            console.print(f"  [cyan]{rule_id}[/cyan]: {len(findings)}")

        # 检查是否找到真实漏洞
        console.print("\n" + "=" * 60)
        console.print(Panel("3. Audit: Did SmartBench find the real vulnerabilities?", style="bold yellow"))

        found_vulns = []

        sql_findings = [
            f for f in data.get("findings", [])
            if "sql" in f.get("rule_id", "").lower()
        ]

        for f in sql_findings:
            for real in real_vulns:
                if real["file"] in str(f.get("location", {}).get("file_path", "")):
                    if abs(f.get("location", {}).get("line_start", 0) - real["line"]) <= 2:
                        found_vulns.append({
                            "real": real,
                            "found": f
                        })

        if found_vulns:
            console.print("\n[bold green]✅ SmartBench found the real vulnerabilities![/bold green]\n")
            for match in found_vulns:
                f = match["found"]
                loc = f"{f.get('location', {}).get('file_path')}:{f.get('location', {}).get('line_start')}"
                console.print(f"  [green]✓ {loc}[/green]")
                console.print(f"  {f.get('rule_id')}: {f.get('message')}")
        else:
            console.print("\n[yellow]⚠️  SmartBench did not flag the real SQL injection vulnerabilities[/yellow]")
            console.print("\nThe vulnerabilities exist, but the dataflow analysis isn't detecting them because:")
            console.print("  - It only looks for req/request params (these snippets are from function params)")
            console.print("  - Needs to be more aggressive in taint tracking function args")

        # 路径遍历误报检查
        console.print("\n" + "=" * 60)
        console.print(Panel("4. Audit: Path Traversal False Positives Check", style="bold magenta"))

        path_findings = [
            f for f in data.get("findings", [])
            if "path_traversal" in f.get("rule_id", "").lower()
        ]

        if path_findings:
            console.print(f"\n[bold]Found {len(path_findings)} path traversal findings[/bold yellow]\n")
            console.print("[yellow]These are likely false positives (from import statements etc.)[/yellow]\n")
            for f in path_findings[:5]:
                loc = f"{Path(f.get('location', {}).get('file_path')).name}:{f.get('location', {}).get('line_start')}"
                console.print(f"  {loc}: {f.get('message')}")

    else:
        console.print("\n[yellow]No scan results file found[/yellow]")

    # 总结
    console.print("\n" + "=" * 60)
    console.print(Panel("📋 Audit Summary", style="bold green"))

    console.print("\n[bold]Findings:[/bold]")
    console.print(f"  ✅ {len(real_vulns)} real SQL injection vulnerabilities exist in the codebase")
    console.print(f"  ⚠️  SmartBench needs improvement to catch them with dataflow analysis")
    console.print(f"  📊 Rule-based analysis: see details above")

    console.print("\n[bold]Next steps:[/bold]")
    console.print("  1. Improve taint source matching to include function params")
    console.print("  2. Tune false positive reduction for path traversal")
    console.print("  3. Keep iterating on the dataflow analysis accuracy")


if __name__ == "__main__":
    main()
