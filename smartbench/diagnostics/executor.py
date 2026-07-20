"""
Diagnostic tool executor — runs tools and injects results into debate context.

Maps diagnosis strategies to tool categories, executes applicable tools,
and formats their output for LLM consumption.
"""

import logging
from typing import Dict, List, Optional

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from smartbench.detector.fingerprint import Language
from smartbench.diagnostics.registry import (
    DiagnosticRegistry, DiagnosticTool, DiagnosisResult,
    ProblemCategory,
)
from smartbench.diagnostics.tools import ALL_TOOLS

logger = logging.getLogger(__name__)

# Strategy → ProblemCategory mapping
STRATEGY_TO_CATEGORY: Dict[str, ProblemCategory] = {
    "performance_analysis": ProblemCategory.PERFORMANCE,
    "correctness_audit": ProblemCategory.CODE_QUALITY,
    "architecture_review": ProblemCategory.CODE_QUALITY,
    "security_scan": ProblemCategory.SECURITY,
    "hotspot_analysis": ProblemCategory.PERFORMANCE,
}


def run_tools_for_strategy(
    console: Console,
    project_path: str,
    language: Language,
    strategy: str,
) -> str:
    """Execute diagnostic tools for a given strategy and return formatted context.

    Args:
        console: Rich Console for progress display.
        project_path: Path to the project directory.
        language: Detected project language.
        strategy: Strategy name (e.g., "performance_analysis").

    Returns:
        Formatted markdown string of tool results for debate context injection.
    """
    category = STRATEGY_TO_CATEGORY.get(strategy, ProblemCategory.UNKNOWN)
    if category == ProblemCategory.UNKNOWN:
        return ""

    registry = DiagnosticRegistry()
    for tool in ALL_TOOLS:
        registry.register(tool)

    applicable = registry.find_tools(language, category)
    if not applicable:
        return ""

    results: List[DiagnosisResult] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        for tool in applicable:
            task = progress.add_task(
                f"  Running {tool.name}...", total=None
            )
            try:
                result = tool.diagnose(project_path, category)
                results.append(result)
            except Exception as e:
                logger.warning("Tool %s failed: %s", tool.name, e)
                results.append(DiagnosisResult(
                    tool_name=tool.name,
                    problem_category=category,
                    success=False,
                    error=str(e),
                ))
            progress.remove_task(task)

    return _format_tool_results(results, strategy)


def _format_tool_results(
    results: List[DiagnosisResult], strategy: str
) -> str:
    """Format tool execution results as markdown for LLM context injection.

    Args:
        results: List of DiagnosisResult from executed tools.
        strategy: The strategy name for context.

    Returns:
        Markdown formatted string.
    """
    parts = [
        f"\n## 自动诊断工具执行结果（{strategy}）\n",
        f"以下为 {len(results)} 个诊断工具的实际运行输出。"
        "请基于这些真实数据进行分析，不要编造未在输出中出现的问题。\n",
    ]

    for r in results:
        parts.append(f"\n### {r.tool_name}")
        if not r.success:
            parts.append(f"- **状态**: 执行失败 — {r.error}\n")
            continue

        if r.symptoms:
            parts.append(f"- **严重级别**: {r.severity.value}")
            parts.append(f"- **置信度**: {r.confidence:.0%}")
            parts.append("- **发现的问题**:")
            for s in r.symptoms:
                parts.append(f"  - {s}")

        if r.root_causes:
            parts.append("- **根因分析**:")
            for rc in r.root_causes:
                parts.append(f"  - {rc}")

        if r.suggestions:
            parts.append("- **建议**:")
            for sug in r.suggestions:
                title = sug.get("title", "")
                desc = sug.get("description", "")
                cmd = sug.get("command", "")
                parts.append(f"  - **{title}**: {desc}")
                if cmd:
                    parts.append(f"    ```bash\n    {cmd}\n    ```")

        if r.evidence and r.evidence.strip():
            # Truncate long tool output
            evidence = r.evidence[:2000]
            if len(r.evidence) > 2000:
                evidence += "\n... (输出已截断)"
            parts.append(f"- **原始输出**:\n  ```\n  {evidence}\n  ```")

        if r.commands_used:
            parts.append(
                f"- **执行的命令**: {'; '.join(r.commands_used)}"
            )

    parts.append(
        "\n**注意**: 以上数据来自真实工具执行，非 LLM 生成。"
        "请在分析中优先基于这些实际数据做出判断。\n"
    )

    return "\n".join(parts)
