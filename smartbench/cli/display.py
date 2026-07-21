"""
Display helpers — Rich rendering for debate rounds, project info, and diagnosis results.

All functions accept a rich.Console instance as first argument,
decoupling presentation from business logic.
"""

from typing import Any, Dict, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from smartbench.detector.fingerprint import ProjectFingerprint
from smartbench.engine.debate import DebateResult


def show_debate_round(
    console: Console, role: str, parsed_json: Optional[Dict], raw_text: str
) -> None:
    """Display one debate round output with Rich Panel.

    Args:
        console: Rich Console instance.
        role: "proposer" / "critique" / "judge" / "verifier".
        parsed_json: Parsed JSON dict from LLM, or None if parse failed.
        raw_text: Raw LLM response text.
    """
    role_names = {
        "proposer": ("Proposer（方案提出者）", "cyan"),
        "critique": ("Critique（交叉审查者）", "yellow"),
        "judge": ("Judge（最终仲裁者）", "green"),
    }
    display_name, color = role_names.get(role, (role, "white"))

    if role == "verifier":
        _show_verifier_round(console, parsed_json)
        return

    if not parsed_json:
        console.print(Panel(
            f"[red]解析失败[/red]\n"
            f"[dim]{raw_text[:300] if raw_text else '(无输出)'}[/dim]",
            title=f"[{color}]{display_name}[/{color}]",
            border_style=color,
        ))
        return

    if role == "proposer":
        _show_proposer(console, parsed_json, display_name, color)
    elif role == "critique":
        _show_critique(console, parsed_json, display_name, color)
    elif role == "judge":
        _show_judge(console, parsed_json, display_name, color)


def _show_verifier_round(console: Console, parsed_json: Optional[Dict]) -> None:
    """Display verification results."""
    if not parsed_json:
        return

    vtype = parsed_json.get("type", "")
    proposals = parsed_json.get("proposals", [])
    summary = parsed_json.get("summary", "")

    if vtype != "proposer_check":
        return

    body = ""
    for p in proposals:
        if not isinstance(p, dict):
            continue
        verif = p.get("__verification", {})
        verdict = verif.get("verdict", "unverifiable")
        score = verif.get("verification_score", 0)
        title = p.get("title", "?")

        if verdict == "verified":
            icon = "  [green][✓ 已验证][/green]"
        elif verdict == "partial":
            icon = "  [yellow][⚠ 部分匹配][/yellow]"
        else:
            icon = "  [red][✗ 不存在][/red]"

        body += f"{icon} [bold]{title}[/bold] (得分: {score:.0%})\n"
        for loc in verif.get("hallucinated_locations", []):
            body += f"    [red]✗ 文件不存在: {loc}[/red]\n"
        for loc in verif.get("verified_locations", []):
            body += f"    [dim]✓ {loc}[/dim]\n"
        for loc in verif.get("partial_locations", []):
            body += f"    [yellow]⚠ {loc}[/yellow]\n"

    console.print(Panel(
        body.strip() or summary[:500],
        title="[blue]Verifier（事实核查 - Proposer 输出）[/blue]",
        border_style="blue",
    ))


def _show_proposer(
    console: Console, parsed_json: Dict, display_name: str, color: str
) -> None:
    """Display Proposer output."""
    analysis = parsed_json.get("analysis", {})
    proposals = parsed_json.get("proposals", [])
    body = f"[bold]根因分析：[/bold]{analysis.get('root_cause', 'N/A')}\n"
    body += f"[bold]影响评估：[/bold]{analysis.get('impact_assessment', 'N/A')}\n\n"
    for i, p in enumerate(proposals[:5], 1):
        body += (
            f"[bold]#{i} {p.get('title', '无标题')}[/bold] "
            f"[{p.get('risk_level', '?')}风险]\n"
        )
        body += f"  {p.get('problem', '')[:120]}\n"
        body += f"  [dim]位置: {p.get('location', '?')}[/dim]\n"
    console.print(Panel(
        body.strip(),
        title=f"[{color}]{display_name}[/{color}] （{len(proposals)} 条方案）",
        border_style=color,
    ))


def _show_critique(
    console: Console, parsed_json: Dict, display_name: str, color: str
) -> None:
    """Display Critique output."""
    verdicts = parsed_json.get("verdicts", [])
    assessment = parsed_json.get("overall_assessment", "")
    body = ""
    for v in verdicts:
        icon = {"accept": "[接受]", "modify": "[需修改]", "reject": "[拒绝]"}.get(
            v.get("verdict", ""), "[?]"
        )
        body += f"{icon} [bold]{v.get('proposal_title', '?')}[/bold]\n"
        for concern in v.get("concerns", []):
            body += f"   └ {concern}\n"
        if v.get("suggested_modifications"):
            body += f"   [dim]建议: {v['suggested_modifications']}[/dim]\n"
    if assessment:
        body += f"\n[dim]{assessment}[/dim]"
    console.print(Panel(
        body.strip(),
        title=f"[{color}]{display_name}[/{color}]",
        border_style=color,
    ))


def _show_judge(
    console: Console, parsed_json: Dict, display_name: str, color: str
) -> None:
    """Display Judge output."""
    decision = parsed_json.get("decision", "?")
    reasoning = parsed_json.get("reasoning", "")
    final = parsed_json.get("final_suggestions", [])
    risk = parsed_json.get("risk_summary", "")
    body = f"[bold]决策：[/bold]{decision}\n"
    body += f"[bold]理由：[/bold]{reasoning}\n"
    body += f"[bold]最终建议：[/bold]{len(final)} 条\n\n"
    for i, s in enumerate(final[:5], 1):
        prio = s.get("priority", 3)
        body += (
            f"[bold]#{i} {s.get('title', '?')}[/bold] "
            f"[优先级:{prio}] [共识:{s.get('consensus', '?')}]\n"
        )
    if risk:
        body += f"\n[bold red][!] 顶层风险：[/bold red]{risk}"
    console.print(Panel(
        body.strip(),
        title=f"[{color}]{display_name}[/{color}] （最终报告）",
        border_style=color,
    ))


# ═══════════════════════════════════════════════════════════════════════
# Project Info Display
# ═══════════════════════════════════════════════════════════════════════


def display_fingerprint(console: Console, fp: ProjectFingerprint) -> None:
    """Display project fingerprint in a Rich table."""
    table = Table(
        title="Project Fingerprint (Phase 1 — zero LLM)", show_header=False
    )
    table.add_column("Property", style="cyan")
    table.add_column("Value")

    table.add_row(
        "Primary Language",
        f"[bold]{fp.primary_language.value}[/bold] "
        f"(confidence: {fp.language_confidence:.0%})",
    )
    if fp.secondary_languages:
        table.add_row(
            "Secondary", ", ".join(lang.value for lang in fp.secondary_languages)
        )
    table.add_row(
        "Framework",
        f"{fp.framework.value} (confidence: {fp.framework_confidence:.0%})",
    )
    table.add_row("Project Type", fp.project_type.value)
    table.add_row("Build System", fp.build_system or "unknown")
    table.add_row(
        "Source Files", f"{fp.source_files} (~{fp.lines_of_code_estimate:,} LOC)"
    )
    table.add_row("Entry Points", ", ".join(fp.entry_points[:5]) or "none")
    table.add_row("Dependencies", f"{fp.dependency_count} packages")
    table.add_row(
        "Git",
        f"{'yes ' + fp.git_remote_url[:50] if fp.is_git_repo else 'no'}",
    )
    if fp.hot_files:
        table.add_row("Hot Files", ", ".join(fp.hot_files[:5]))
    table.add_row(
        "README", f"{'yes: ' + fp.readme_path if fp.has_readme else 'no'}"
    )

    console.print(table)


def display_project_understanding(console: Console, understanding: Dict) -> None:
    """Display LLM's understanding of the project."""
    console.print("\n[bold cyan]LLM Analysis:[/bold cyan]")
    console.print(
        f"  [bold]Summary:[/bold] {understanding.get('project_summary', 'N/A')}"
    )
    console.print(
        f"  [bold]Domain:[/bold] {understanding.get('primary_domain', 'N/A')}"
    )
    concerns = understanding.get("key_concerns", [])
    if concerns:
        console.print(f"  [bold]Key Concerns:[/bold] {', '.join(concerns)}")
    console.print(
        f"  [bold]Suggested Focus:[/bold] "
        f"{understanding.get('suggested_diagnostic_focus', 'N/A')}"
    )


def display_diagnosis_results(
    console: Console,
    result: DebateResult,
    fp: ProjectFingerprint,
    graph: Any = None,
) -> None:
    """Display the final diagnosis report with findings."""
    console.print(
        f"\n[bold]Diagnostic Report[/bold] "
        f"({result.duration_ms}ms, {result.iterations} debate rounds)"
    )

    if not result.final_suggestions:
        console.print("  [yellow]No issues identified[/yellow]")
        if graph:
            display_graph_stats(console, graph, fp)
        return

    console.print(
        f"\n[bold green]{len(result.final_suggestions)} findings:[/bold green]\n"
    )

    prio_colors: Dict[int, str] = {
        5: "red", 4: "yellow", 3: "cyan", 2: "blue", 1: "dim",
    }

    for i, sug in enumerate(result.final_suggestions, 1):
        title = sug.get("title", f"Finding {i}")
        desc = sug.get("description", "")
        impl = sug.get("implementation", "")
        priority = sug.get("priority", 3)
        risk = sug.get("risk_level", "medium")
        location = sug.get("location", "")
        consensus = sug.get("consensus", "unknown")

        color = prio_colors.get(priority, "white")
        loc_line = f"[bold]Location:[/bold] {location}" if location else ""

        console.print(Panel(
            f"[bold]{title}[/bold]\n\n{desc}\n\n"
            f"[bold]Fix:[/bold] {impl}\n{loc_line}".strip(),
            title=(
                f"#{i} [{color}]Priority {priority}[/{color}] | "
                f"Risk: {risk} | Consensus: {consensus}"
            ),
            border_style=color,
        ))

    if graph:
        display_graph_stats(console, graph, fp)


def display_graph_stats(
    console: Console, graph: Any, fp: ProjectFingerprint
) -> None:
    """Show code graph statistics."""
    console.print(f"\n  [dim]Code graph: {graph.summary()}[/dim]")
