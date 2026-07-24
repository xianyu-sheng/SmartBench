#!/usr/bin/env python3
"""深度分析扫描结果，找出真正有价值的漏洞。"""

import json
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from collections import defaultdict

console = Console()
project_root = Path("/home/xianyu-sheng/continue")


def get_file_content(filepath):
    """获取文件内容。"""
    try:
        full_path = project_root / filepath
        if full_path.exists():
            return full_path.read_text()
    except Exception as e:
        pass
    return None


def get_context_lines(content, line_start, context=5):
    """获取上下文代码。"""
    lines = content.split("\n")
    start = max(0, line_start - 1 - context)
    end = min(len(lines), line_start + context)
    return "\n".join(lines[start:end])


def analyze_command_injection(finding):
    """分析命令注入漏洞。"""
    filepath = finding["location"]["file_path"]
    line_num = finding["location"]["line_start"]
    content = get_file_content(filepath)
    if not content:
        return False, "File not found"

    context = get_context_lines(content, line_num, context=8)

    # 检查是否有用户可控输入
    red_flags = [
        "child_process", "exec", "spawn", "execSync", "spawnSync",
        "`", "${", "process.env", "req.", "request.",
        "userInput", "input", "params", "query", "body"
    ]

    has_red_flag = any(flag in context for flag in red_flags)

    # 查看代码
    return has_red_flag, context


def analyze_sql_injection(finding):
    """分析SQL注入漏洞。"""
    filepath = finding["location"]["file_path"]
    line_num = finding["location"]["line_start"]
    content = get_file_content(filepath)
    if not content:
        return False, "File not found"

    context = get_context_lines(content, line_num, context=8)

    # 检查是否有模板字符串拼接
    dangerous_patterns = [
        "`.*\\$\\{.*\\}.*`",  # 模板字符串变量
        "db.run.*\\$\\{", "db.all.*\\$\\{", "db.exec.*\\$\\{",
        "db.prepare.*\\$\\{", "db.get.*\\$\\{",
    ]

    has_danger = any(pattern in context for pattern in dangerous_patterns)

    return has_danger, context


def analyze_hardcoded_secret(finding):
    """分析硬编码密钥。"""
    filepath = finding["location"]["file_path"]
    line_num = finding["location"]["line_start"]
    content = get_file_content(filepath)
    if not content:
        return False, "File not found"

    context = get_context_lines(content, line_num, context=4)

    # 检查是否看起来像真的密钥
    suspicious_keywords = [
        "api_key", "apikey", "secret_key", "secretkey",
        "private_key", "privatekey", "password", "passwd",
        "authorization", "auth_token", "authtoken",
        "token", "access_token", "accesstoken",
        "-----BEGIN", "sk_", "pk_", "xoxp-",
        "AKIA", "AIza", "ghp_", "glpat-",
    ]

    is_suspicious = any(kw in context.lower() for kw in suspicious_keywords)

    return is_suspicious, context


def main():
    results_path = Path("/home/xianyu-sheng/SmartBench/continue_scan_results.json")
    with open(results_path, "r") as f:
        data = json.load(f)

    findings = data.get("findings", [])

    console.print("\n" + "=" * 80)
    console.print(Panel("SmartBench 扫描结果深度分析", style="bold magenta"))
    console.print(f"总计: {len(findings)} 个发现")

    # 按规则分类
    by_rule = defaultdict(list)
    for f in findings:
        by_rule[f["rule_id"]].append(f)

    # 显示统计
    console.print("\n[bold]发现统计:[/bold]")
    for rule_id, items in sorted(by_rule.items(), key=lambda x: -len(x[1])):
        console.print(f"  {rule_id}: {len(items)}")

    # 深入分析关键漏洞
    console.print("\n" + "=" * 80)
    console.print(Panel("关键漏洞详细分析", style="bold red"))

    high_value_findings = []

    # 1. SQL注入分析
    console.print("\n[bold][yellow]🔍 SQL 注入漏洞分析:[/yellow][/bold]")
    sql_findings = [
        f for f in findings
        if "sql" in f.get("rule_id", "").lower()
    ]

    if sql_findings:
        for f in sql_findings:
            filepath = f["location"]["file_path"]
            line_num = f["location"]["line_start"]
            is_danger, context = analyze_sql_injection(f)

            console.print(f"\n  [cyan]{filepath}:{line_num}[/cyan]")
            if context:
                console.print(Syntax(context, "typescript"))

            # 特别标记已知的真实漏洞
            if "CodeSnippetsIndex" in filepath:
                console.print("  [bold][red]⚠️  已知真实漏洞: SQL注入存在![/red][/bold]")
                high_value_findings.append({
                    "type": "SQL Injection",
                    "severity": "CRITICAL",
                    "file": filepath,
                    "line": line_num,
                    "description": "用户输入被直接拼接到 SQL 查询中",
                    "code": context
                })

    # 2. 命令注入分析
    console.print("\n" + "=" * 80)
    console.print(Panel("命令注入漏洞分析", style="bold yellow"))

    cmd_findings = [f for f in findings if "command" in f.get("rule_id", "").lower()]
    if cmd_findings:
        for f in cmd_findings:
            filepath = f["location"]["file_path"]
            line_num = f["location"]["line_start"]
            is_danger, context = analyze_command_injection(f)

            console.print(f"\n  [cyan]{filepath}:{line_num}[/cyan]")
            if context:
                console.print(Syntax(context, "typescript"))

            # 检查是否真的危险
            if is_danger:
                console.print("  [red]⚠️  可能存在命令注入风险[/red]")
                high_value_findings.append({
                    "type": "Command Injection",
                    "severity": "HIGH",
                    "file": filepath,
                    "line": line_num,
                    "description": "需要进一步人工验证",
                    "code": context
                })
            else:
                console.print("  [green]可能是误报[/green]")

    # 3. 硬编码密钥分析
    console.print("\n" + "=" * 80)
    console.print(Panel("硬编码密钥分析", style="bold blue"))

    secret_findings = [f for f in findings if "hardcoded" in f.get("rule_id", "").lower()]
    if secret_findings:
        for f in secret_findings:
            filepath = f["location"]["file_path"]
            line_num = f["location"]["line_start"]
            is_suspicious, context = analyze_hardcoded_secret(f)

            console.print(f"\n  [cyan]{filepath}:{line_num}[/cyan]")
            if context:
                console.print(Syntax(context, "typescript"))

            if is_suspicious:
                console.print("  [red]⚠️  发现可疑密钥![/red]")
                high_value_findings.append({
                    "type": "Hardcoded Secret",
                    "severity": "HIGH",
                    "file": filepath,
                    "line": line_num,
                    "description": "可能泄露敏感凭证",
                    "code": context
                })

    # 4. 数据流量分析结果 - 这些误报率低
    console.print("\n" + "=" * 80)
    console.print(Panel("数据流量分析结果", style="bold green"))

    flow_findings = [f for f in findings if "_flow" in f.get("rule_id", "")]
    if flow_findings:
        console.print(f"\n找到 {len(flow_findings)} 个数据流规则发现")
        for f in flow_findings:
            filepath = f["location"]["file_path"]
            line_num = f["location"]["line_start"]
            content = get_file_content(filepath)
            if content:
                context = get_context_lines(content, line_num, context=8)
                console.print(f"\n  [cyan]{filepath}:{line_num}[/cyan]")
                console.print(f"  Rule: {f['rule_id']}")
                console.print(Syntax(context, "typescript"))

                # 自动添加为高价值发现
                high_value_findings.append({
                    "type": f["rule_id"],
                    "severity": "MEDIUM",
                    "file": filepath,
                    "line": line_num,
                    "description": f["message"],
                    "code": context
                })

    # 总结报告
    console.print("\n" + "=" * 80)
    console.print(Panel("分析结果摘要", style="bold cyan"))

    if high_value_findings:
        table = Table(show_header=True)
        table.add_column("类型", style="yellow")
        table.add_column("严重程度", style="red")
        table.add_column("文件", style="cyan")
        table.add_column("行号")

        for finding in high_value_findings:
            table.add_row(
                finding["type"],
                finding["severity"],
                finding["file"],
                str(finding["line"])
            )

        console.print(table)
    else:
        console.print("\n[green]未发现高价值漏洞[/green]")

    # 保存详细报告
    console.print("\n[bold]保存详细报告...[/bold]")

    report = {
        "total_findings": len(findings),
        "high_value_findings": high_value_findings,
        "summary": {
            "critical": sum(1 for f in high_value_findings if f["severity"] == "CRITICAL"),
            "high": sum(1 for f in high_value_findings if f["severity"] == "HIGH"),
            "medium": sum(1 for f in high_value_findings if f["severity"] == "MEDIUM"),
        }
    }

    with open("/home/xianyu-sheng/SmartBench/detailed_audit.json", "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    console.print("\n[green]报告已保存到: detailed_audit.json[/green]")

    # 输出最终回答
    console.print("\n" + "=" * 80)
    console.print(Panel("最终结论", style="bold green"))

    critical_count = sum(1 for f in high_value_findings if f["severity"] == "CRITICAL")

    if critical_count > 0:
        console.print(f"\n[bold][red]发现 {critical_count} 个严重漏洞！[/red][/bold]")
        console.print("\n除了你提到的 SQL 注入外，还发现了以下值得关注的问题:")
        for f in high_value_findings:
            if f["severity"] in ["CRITICAL", "HIGH"]:
                console.print(f"\n  • {f['type']} at {f['file']}:{f['line']}")
    else:
        console.print("\n除了已知的 SQL 注入外，未发现其他严重漏洞。")


if __name__ == "__main__":
    main()
