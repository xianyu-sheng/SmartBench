"""
SmartBench CLI — interactive code diagnosis wizard.

Usage:
    smartbench              # Interactive mode (full wizard)
    smartbench quick        # Quick mode (minimal questions, auto-detect everything)
    smartbench diagnose     # Just diagnose (skip benchmarking for non-perf issues)
"""

import sys
import os
import json
import time
from pathlib import Path
from typing import Optional, Dict, Any, List

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich import print as rprint

# Ensure package import works
sys.path.insert(0, str(Path(__file__).parent.parent))

from smartbench.detector.scanner import ProjectScanner
from smartbench.detector.fingerprint import ProjectFingerprint, Language, Framework
from smartbench.prompts.factory import PromptFactory
from smartbench.graph.builder import CodeGraphBuilder
from smartbench.graph.retriever import GraphRetriever
from smartbench.diagnostics.registry import DiagnosticRegistry, ProblemCategory
from smartbench.diagnostics.tools import ALL_TOOLS
from smartbench.engine.debate import DebateEngine, DebateResult
from smartbench.llm.provider import (
    PROVIDER_REGISTRY,
    detect_provider,
    load_api_keys_from_env,
    configure_api_keys,
    masked_input,
)
from smartbench.llm.client import call_llm, parse_json_safe as _parse_json_safe
from smartbench.cli.display import (
    show_debate_round,
    display_fingerprint,
    display_project_understanding,
    display_diagnosis_results,
    display_graph_stats,
)
from smartbench.cli.phases import (
    resolve_project_path,
    run_phase1_detection,
    run_phase4_graph,
    run_diagnosis_with_graph,
    run_fallback_analysis,
    run_quick_mode,
    run_diagnose_mode,
)

app = typer.Typer(
    name="smartbench",
    help="SmartBench — AI-powered universal code diagnosis tool",
    add_completion=False,
)
console = Console()


# ═══════════════════════════════════════════════════════════════════════
# Main entry point
# ═══════════════════════════════════════════════════════════════════════

@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    quick: bool = typer.Option(False, "--quick", "-q", help="Quick mode: auto-detect everything"),
    project: Optional[str] = typer.Option(None, "--project", "-p", help="Project path or git URL"),
    concern: Optional[str] = typer.Option(None, "--concern", "-c", help="What problem are you facing?"),
):
    """SmartBench — AI-powered universal code diagnosis tool."""
    if ctx.invoked_subcommand is None:
        if quick:
            run_quick_mode(console, project=project, concern=concern)
        else:
            run_interactive_wizard()


@app.command()
def quick(
    project: Optional[str] = typer.Option(None, "--project", "-p"),
    concern: Optional[str] = typer.Option(None, "--concern", "-c"),
):
    """Quick diagnosis — auto-detect everything, minimal prompts."""
    run_quick_mode(console, project=project, concern=concern)


@app.command()
def diagnose(
    project: str = typer.Option(..., "--project", "-p"),
    symptoms: Optional[str] = typer.Option(None, "--symptoms", "-s"),
    performance: bool = typer.Option(False, "--perf", help="Performance profiling mode"),
):
    """Run diagnosis only (no benchmarking)."""
    run_diagnose_mode(console, project=project, symptoms=symptoms, performance=performance)


@app.command()
def check():
    """Check tool availability for the current system."""
    from smartbench.detector.scanner import ProjectScanner
    current = os.getcwd()
    try:
        fp = ProjectScanner(current).scan()
        registry = DiagnosticRegistry()
        for tool in ALL_TOOLS:
            registry.register(tool)
        health = registry.health_check(fp.primary_language)
        table = Table("Tool", "Available", "Language")
        for name, available in health.items():
            tool = registry.get_tool(name)
            langs = ", ".join(l.value for l in tool.applicable_languages[:3]) if tool else ""
            table.add_row(name, "[green]OK[/green]" if available else "[red]NO[/red]", langs)
        console.print(table)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")


# ═══════════════════════════════════════════════════════════════════════
# Interactive Wizard
# ═══════════════════════════════════════════════════════════════════════

def run_interactive_wizard():
    """Full interactive setup wizard."""
    console.print()
    console.print(Panel.fit(
        "[bold cyan]SmartBench[/bold cyan] — Universal Code Diagnosis\n"
        "[dim]AI-powered analysis for any codebase, any language[/dim]",
        border_style="cyan",
    ))

    # ── Step 1: Project source ────────────────────────────────────────
    console.print("\n[bold]Step 1/4[/bold] — Where is your code?")
    console.print("  Enter a local path or a git repository URL.")
    console.print("  [dim]Examples: /home/user/myproject  |  https://github.com/user/repo[/dim]")

    project_input = Prompt.ask("  Project path/URL").strip()

    project_path = resolve_project_path(console, project_input)
    if not project_path:
        console.print(f"[red]Cannot access: {project_input}[/red]")
        raise typer.Exit(1)

    console.print(f"  [green]OK[/green] Project: {project_path}")

    # ── Step 2: API keys ──────────────────────────────────────────────
    console.print("\n[bold]Step 2/4[/bold] — Configure LLM API keys")
    console.print("  SmartBench needs at least one LLM API key to analyze your code.")

    api_config = configure_api_keys(console)
    if not api_config:
        console.print("[red]No API keys configured. SmartBench requires an LLM to function.[/red]")
        raise typer.Exit(1)

    # ── Step 3: Project detection ─────────────────────────────────────
    console.print("\n[bold]Step 3/4[/bold] — Analyzing your project...")
    fingerprint = run_phase1_detection(console, project_path)
    display_fingerprint(console, fingerprint)

    # Phase 2: LLM reads README
    readme_content = ""
    if fingerprint.has_readme:
        try:
            readme_content = (Path(project_path) / fingerprint.readme_path).read_text(
                encoding="utf-8", errors="ignore"
            )[:4000]
        except Exception:
            pass

    if api_config and readme_content:
        console.print("\n  [dim]Asking LLM to understand your project...[/dim]")
        factory = PromptFactory(fingerprint)
        prompt = factory.build_project_understanding_prompt(readme_content)
        response = call_llm(api_config, prompt)
        if response:
            understanding = _parse_json_safe(response)
            if understanding:
                display_project_understanding(console, understanding)

    # ── Step 4: Clarify concern ───────────────────────────────────────
    console.print("\n[bold]Step 4/4[/bold] — What would you like to diagnose?")
    console.print("  [dim]performance, crashes, memory leaks, code quality, security, or 'analyze everything'[/dim]")
    user_concern = Prompt.ask("  Concern", default="analyze the project for issues").strip()

    # ── Build code graph ──────────────────────────────────────────────
    console.print("\n[bold]Building code graph...[/bold]")
    graph, hybrid_retriever = run_phase4_graph(console, project_path, fingerprint)

    if graph and len(graph.nodes) > 0:
        console.print(f"  [green]OK[/green] {graph.summary()}")
        run_diagnosis_with_graph(console, project_path, fingerprint, graph, api_config,
                                  user_concern, hybrid_retriever=hybrid_retriever)
    else:
        console.print("  [yellow]Could not build code graph (no source files found?)[/yellow]")
        run_fallback_analysis(console, project_path, fingerprint, api_config, user_concern)

    console.print("\n[bold green]Done![/bold green]")
    console.print("  Thanks for using SmartBench!\n")


# ═══════════════════════════════════════════════════════════════════════
# Quick Mode
# ═══════════════════════════════════════════════════════════════════════

