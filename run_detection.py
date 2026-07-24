#!/usr/bin/env python3
"""
运行 SmartBench 检测并验证结果。
"""

import json
from pathlib import Path
from rich.console import Console

console = Console()

# 导入并设置引擎
from smartbench.core import (
    AdapterRegistry,
    RuleRegistry,
    UnifiedDiagnosticConfig,
    UnifiedDiagnosticEngine,
    register_all_adapters,
    register_builtin_rules,
)


def setup_engine():
    """设置检测引擎。"""
    adapters = AdapterRegistry()
    register_all_adapters(adapters)

    rules = RuleRegistry()
    register_builtin_rules(rules)

    console.print(f"📋 注册的规则: {rules.list_rule_ids()}")

    return UnifiedDiagnosticEngine(adapters, rules)


def main():
    project_path = Path("/home/xianyu-sheng/continue").resolve()

    console.print(f"🔍 分析项目: {project_path}")
    console.print()

    engine = setup_engine()

    config = UnifiedDiagnosticConfig(
        use_llm_rules=False,
        use_static_rules=True,
        languages=["typescript", "javascript"],
    )

    console.print("⏳ 正在分析...")
    result = engine.diagnose(project_path, config)

    console.print()
    console.print("=" * 60)
    console.print("📊 检测结果")
    console.print("=" * 60)
    console.print()

    if result.errors:
        console.print("⚠️ 错误:")
        for err in result.errors:
            console.print(f"   - {err}")
        console.print()

    console.print(f"📁 扫描文件: {result.stats.get('files_scanned', 0)}")
    console.print(f"⏱️  耗时: {result.duration_ms}ms")
    console.print(f"🔍 发现问题: {len(result.findings)}")
    console.print()

    # 分组显示
    if result.findings:
        by_rule: dict = {}
        for f in result.findings:
            by_rule.setdefault(f.rule_id, []).append(f)

        for rule_id, findings in sorted(by_rule.items()):
            console.print(f"📋 {rule_id}: {len(findings)} 个问题")
            for f in findings[:3]:
                loc = f"{f.location.file_path}:{f.location.line_start}"
                sev = f.severity.value
                console.print(f"   [{sev}] {loc}")
                console.print(f"      {f.message}")
            if len(findings) > 3:
                console.print(f"   ... 和 {len(findings) - 3} 个更多")
            console.print()
    else:
        console.print("✅ 没有发现问题")
        console.print()

    # 保存结果
    output_path = Path("/home/xianyu-sheng/SmartBench/continue_results.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({
            "findings": [f.to_dict() for f in result.findings],
            "stats": result.stats,
            "duration_ms": result.duration_ms,
            "errors": result.errors,
            "project_path": str(project_path),
        }, f, ensure_ascii=False, indent=2)

    console.print(f"💾 结果已保存到: {output_path}")

    # 现在让我们手动查看之前发现的 SQL 注入文件
    console.print()
    console.print("=" * 60)
    console.print("🔍 手动验证: 检查 SQL 注入文件")
    console.print("=" * 60)
    console.print()

    sql_files_to_check = [
        "core/indexing/CodeSnippetsIndex.ts",
        "core/indexing/LanceDbIndex.ts",
    ]

    for fpath in sql_files_to_check:
        full_path = project_path / fpath
        if full_path.exists():
            console.print(f"📁 {fpath}:")
            content = full_path.read_text()
            # 查找 db.run
            lines = content.split("\n")
            for i, line in enumerate(lines):
                if "db.run" in line or "query" in line and "IN" in line:
                    console.print(f"  L{i+1}: {line.strip()}")
            console.print()


if __name__ == "__main__":
    main()
