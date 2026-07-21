"""
SmartBench CLI — interactive code diagnosis wizard.

Usage:
    smartbench              # Interactive mode (full wizard)
    smartbench quick        # Quick mode (minimal questions)
    smartbench diagnose     # Diagnosis only
    smartbench check        # Tool availability check
"""

import os
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from smartbench.cli.phases import (
    resolve_project_path,
    run_diagnose_mode,
    run_phase1_detection,
    run_phase4_graph,
    run_quick_mode,
)
from smartbench.cli.wizard import run_interactive_wizard
from smartbench.detector.scanner import ProjectScanner
from smartbench.diagnostics.registry import DiagnosticRegistry
from smartbench.diagnostics.tools import ALL_TOOLS

app = typer.Typer(
    name="smartbench",
    help="SmartBench — AI-powered universal code diagnosis tool",
    add_completion=False,
)
console = Console()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    quick: bool = typer.Option(
        False, "--quick", "-q", help="Quick mode: auto-detect everything"
    ),
    project: Optional[str] = typer.Option(
        None, "--project", "-p", help="Project path or git URL"
    ),
    concern: Optional[str] = typer.Option(
        None, "--concern", "-c", help="What problem are you facing?"
    ),
    output: Optional[str] = typer.Option(
        None, "--output", "-o", help="Save report to file (JSON format)"
    ),
    sandbox: bool = typer.Option(
        False,
        "--sandbox",
        help="Apply proposed patches and run tests in a temp copy (trusted repos only)",
    ),
):
    """SmartBench — AI-powered universal code diagnosis tool."""
    if ctx.invoked_subcommand is None:
        if quick:
            result = run_quick_mode(
                console,
                project=project,
                concern=concern,
                enable_sandbox=sandbox,
            )
        else:
            result = run_interactive_wizard(console, enable_sandbox=sandbox)
        _maybe_save_output(result, output)


@app.command()
def quick(
    project: Optional[str] = typer.Option(None, "--project", "-p"),
    concern: Optional[str] = typer.Option(None, "--concern", "-c"),
    output: Optional[str] = typer.Option(
        None, "--output", "-o", help="Save report to file (JSON)"
    ),
    sandbox: bool = typer.Option(
        False,
        "--sandbox",
        help="Apply proposed patches and run tests in a temp copy (trusted repos only)",
    ),
):
    """Quick diagnosis — auto-detect everything, minimal prompts."""
    result = run_quick_mode(
        console,
        project=project,
        concern=concern,
        enable_sandbox=sandbox,
    )
    _maybe_save_output(result, output)


@app.command()
def diagnose(
    project: str = typer.Option(..., "--project", "-p"),
    symptoms: Optional[str] = typer.Option(None, "--symptoms", "-s"),
    performance: bool = typer.Option(
        False, "--perf", help="Performance profiling mode"
    ),
    system_probes: bool = typer.Option(
        False,
        "--system-probes",
        help="Include host process, VM, and kernel probes (may expose host data)",
    ),
    output: Optional[str] = typer.Option(
        None, "--output", "-o", help="Save report to file (JSON)"
    ),
):
    """Run diagnosis only (no benchmarking)."""
    result = run_diagnose_mode(
        console,
        project=project,
        symptoms=symptoms,
        performance=performance,
        system_probes=system_probes,
    )
    _maybe_save_output(result, output)


@app.command("eval-rag")
def evaluate_rag(
    project: str = typer.Option(..., "--project", "-p"),
    queries: str = typer.Option(..., "--queries", "-q"),
    graph_only: bool = typer.Option(
        False, "--graph-only", help="Evaluate structural retrieval without vectors"
    ),
    output: Optional[str] = typer.Option(
        None, "--output", "-o", help="Save evaluation report to JSON"
    ),
):
    """Measure retrieval Hit@k, Precision@k, MRR, and latency."""
    from smartbench.graph.retriever import GraphRetriever
    from smartbench.rag.evaluator import RAGEvaluator

    project_path = resolve_project_path(console, project)
    if not project_path:
        console.print(f"[red]Cannot access: {project}[/red]")
        raise typer.Exit(1)
    try:
        fingerprint = run_phase1_detection(console, project_path)
        graph, hybrid = run_phase4_graph(
            console,
            project_path,
            fingerprint,
            build_rag=not graph_only,
        )
        if graph is None:
            raise RuntimeError("Could not build a code graph")
        retriever = hybrid or GraphRetriever(graph, project_path)
        evaluator = RAGEvaluator(retriever, project_path)
        evaluator.load_queries(queries)
        report = evaluator.evaluate()
    except (OSError, ValueError, RuntimeError) as exc:
        console.print(f"[red]Evaluation failed: {exc}[/red]")
        raise typer.Exit(1) from exc

    console.print(report.summary())
    _maybe_save_output(report, output)


@app.command()
def check():
    """Check tool availability for the current system."""
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
            langs = (
                ", ".join(lang.value for lang in tool.applicable_languages[:3])
                if tool else ""
            )
            table.add_row(
                name,
                "[green]OK[/green]" if available else "[red]NO[/red]",
                langs,
            )
        console.print(table)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")


def _maybe_save_output(result, output_path: Optional[str]) -> None:
    """Save diagnosis result to a JSON file if --output is specified."""
    if not output_path or result is None:
        return

    import json as _json
    from dataclasses import asdict, is_dataclass

    try:
        data = asdict(result) if is_dataclass(result) else result
        with open(output_path, "w", encoding="utf-8") as f:
            _json.dump(data, f, ensure_ascii=False, indent=2)
        console.print(f"[green]Report saved to: {output_path}[/green]")
    except Exception as e:
        console.print(f"[red]Failed to save output: {e}[/red]")


if __name__ == "__main__":
    app()
