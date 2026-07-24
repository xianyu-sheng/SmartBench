#!/usr/bin/env python3
"""快速审计脚本。"""

import json
from pathlib import Path

project_root = Path("/home/xianyu-sheng/continue")
results_path = Path("/home/xianyu-sheng/SmartBench/continue_scan_results.json")

with open(results_path, "r") as f:
    data = json.load(f)

findings = data.get("findings", [])

# 排除路径遍历和TODO
interesting = [
    f for f in findings
    if not any(x in f["rule_id"] for x in ["path_traversal", "todo", "unused_import"])
]

print("=" * 80)
print(f"发现 {len(interesting)} 个可能有价值的漏洞:")
print("=" * 80)

for i, f in enumerate(interesting, 1):
    print(f"\n{i}. {f['rule_id']}: {f['message']}")
    print(f"   File: {f['location']['file_path']}:{f['location']['line_start']}")

    # 读取代码
    filepath = project_root / f["location"]["file_path"]
    if filepath.exists():
        lines = filepath.read_text().split("\n")
        line_num = f["location"]["line_start"]
        start = max(0, line_num - 5)
        end = min(len(lines), line_num + 4)
        print("\n   代码:")
        for j in range(start, end):
            prefix = "→ " if j == line_num - 1 else "  "
            print(f"   {prefix}{j+1}: {lines[j]}")

print("\n" + "=" * 80)
print("\nSQL注入漏洞（数据流分析）:")
sql_flow = [f for f in findings if f["rule_id"] == "sql_injection_flow"]
for f in sql_flow:
    print(f"\n  • {f['location']['file_path']}:{f['location']['line_start']}")
