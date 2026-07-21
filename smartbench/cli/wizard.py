"""
Interactive wizard — step-by-step SmartBench setup.

Guides the user through project selection, API key configuration,
project detection, and diagnosis execution.
"""

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from smartbench.cli.display import display_fingerprint, display_project_understanding
from smartbench.cli.phases import (
    resolve_project_path,
    run_diagnosis_with_graph,
    run_fallback_analysis,
    run_phase1_detection,
    run_phase4_graph,
)
from smartbench.llm.client import call_llm, parse_json_safe
from smartbench.llm.provider import configure_api_keys
from smartbench.prompts.factory import PromptFactory


def run_interactive_wizard(
    console: Console, enable_sandbox: bool = False
) -> Optional[object]:
    """Full interactive setup wizard — 4-step guided workflow.

    Returns:
        DebateResult if diagnosis completed, None otherwise.
    """
    console.print()
    console.print(Panel.fit(
        "[bold cyan]SmartBench[/bold cyan] — Universal Code Diagnosis\n"
        "[dim]AI-powered analysis for any codebase, any language[/dim]",
        border_style="cyan",
    ))

    # ── Step 1: Project source ────────────────────────────────────
    console.print("\n[bold]Step 1/4[/bold] — Where is your code?")
    console.print("  Enter a local path or a git repository URL.")
    console.print(
        "  [dim]Examples: /home/user/myproject  |  "
        "https://github.com/user/repo[/dim]"
    )
    project_input = Prompt.ask("  Project path/URL").strip()

    project_path = resolve_project_path(console, project_input)
    if not project_path:
        console.print(f"[red]Cannot access: {project_input}[/red]")
        raise typer.Exit(1)
    console.print(f"  [green]OK[/green] Project: {project_path}")

    # ── Step 2: API keys ──────────────────────────────────────────
    console.print("\n[bold]Step 2/4[/bold] — Configure LLM API keys")
    console.print(
        "  SmartBench needs at least one LLM API key to analyze your code."
    )
    api_config = configure_api_keys(console)
    if not api_config:
        console.print(
            "[red]No API keys configured. "
            "SmartBench requires an LLM to function.[/red]"
        )
        raise typer.Exit(1)

    # ── Step 3: Project detection ─────────────────────────────────
    console.print("\n[bold]Step 3/4[/bold] — Analyzing your project...")
    fingerprint = run_phase1_detection(console, project_path)
    display_fingerprint(console, fingerprint)

    # Phase 2: LLM reads README
    readme_content = ""
    if fingerprint.has_readme:
        try:
            readme_content = (
                (Path(project_path) / fingerprint.readme_path)
                .read_text(encoding="utf-8", errors="ignore")
            )[:4000]
        except Exception:
            pass

    if api_config and readme_content:
        console.print(
            "\n  [dim]Asking LLM to understand your project...[/dim]"
        )
        factory = PromptFactory(fingerprint)
        prompt = factory.build_project_understanding_prompt(readme_content)
        response = call_llm(api_config, prompt)
        if response:
            understanding = parse_json_safe(response)
            if understanding:
                display_project_understanding(console, understanding)

    # ── Step 4: Clarify concern ───────────────────────────────────
    console.print(
        "\n[bold]Step 4/4[/bold] — What would you like to diagnose?"
    )
    console.print(
        "  [dim]performance, crashes, memory leaks, code quality, "
        "security, or 'analyze everything'[/dim]"
    )
    user_concern = Prompt.ask(
        "  Concern", default="analyze the project for issues"
    ).strip()

    # ── Build code graph ──────────────────────────────────────────
    console.print("\n[bold]Building code graph...[/bold]")
    graph, hybrid_retriever = run_phase4_graph(
        console, project_path, fingerprint
    )

    if graph and len(graph.nodes) > 0:
        console.print(f"  [green]OK[/green] {graph.summary()}")
        result = run_diagnosis_with_graph(
            console, project_path, fingerprint, graph, api_config,
            user_concern, hybrid_retriever=hybrid_retriever,
            enable_sandbox=enable_sandbox,
        )
    else:
        console.print(
            "  [yellow]Could not build code graph "
            "(no source files found?)[/yellow]"
        )
        result = run_fallback_analysis(
            console, project_path, fingerprint, api_config, user_concern,
        )

    console.print("\n[bold green]Done![/bold green]")
    console.print("  Thanks for using SmartBench!\n")
    return result
