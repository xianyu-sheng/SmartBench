"""
SmartBench CLI — interactive code diagnosis wizard.

Usage:
    smartbench              # Interactive mode (full wizard)
    smartbench quick        # Quick mode (minimal questions)
    smartbench diagnose     # Diagnosis only
    smartbench unified      # Unified multi-language diagnosis
    smartbench check        # Tool availability check
"""

import os
from typing import List, Optional

import typer
from rich.console import Console
from rich.table import Table

from smartbench import __version__
from smartbench.cli.phases import (
    resolve_project_path,
    run_diagnose_mode,
    run_phase1_detection,
    run_phase4_graph,
    run_quick_mode,
)
from smartbench.cli.unified import (
    list_languages,
    list_rules,
    run_unified_diagnosis,
)
from smartbench.cli.wizard import run_interactive_wizard
from smartbench.detector.scanner import ProjectScanner
from smartbench.diagnostics.registry import DiagnosticRegistry
from smartbench.diagnostics.tools import ALL_TOOLS
from smartbench.terminal import safe_terminal_text

app = typer.Typer(
    name="smartbench",
    help="SmartBench — AI-powered universal code diagnosis tool",
    add_completion=False,
)
console = Console()

# Create unified command sub-app
unified_app = typer.Typer(
    name="unified",
    help="Unified multi-language diagnostic framework",
)
app.add_typer(unified_app, name="unified")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"smartbench {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the installed SmartBench version and exit",
    ),
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
        console.print(f"[red]Cannot access: {safe_terminal_text(project)}[/red]")
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
        console.print(
            f"[red]Evaluation failed: {safe_terminal_text(exc)}[/red]"
        )
        raise typer.Exit(1) from exc

    console.print(safe_terminal_text(report.summary()))
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
        health = registry.health_check(
            fp.primary_language,
            fp.secondary_languages,
        )
        table = Table("Tool", "Available", "Language")
        for name, available in health.items():
            tool = registry.get_tool(name)
            langs = (
                ", ".join(lang.value for lang in tool.applicable_languages[:3])
                if tool else ""
            )
            table.add_row(
                safe_terminal_text(name),
                "[green]OK[/green]" if available else "[red]NO[/red]",
                safe_terminal_text(langs),
            )
        console.print(table)
    except Exception as e:
        console.print(f"[red]Error: {safe_terminal_text(e)}[/red]")
        raise typer.Exit(1) from e


# =============================================================================
# Unified diagnostic commands
# =============================================================================

@unified_app.command("run")
def unified_run(
    project: str = typer.Option(..., "--project", "-p", help="Project path"),
    output: Optional[str] = typer.Option(
        None, "--output", "-o", help="Save JSON report to file"
    ),
    output_sarif: Optional[str] = typer.Option(
        None, "--sarif", help="Save SARIF report to file"
    ),
    rule: Optional[List[str]] = typer.Option(
        None, "--rule", "-r", help="Run specific rule(s) only"
    ),
    language: Optional[List[str]] = typer.Option(
        None, "--language", "-l", help="Scan specific language(s) only"
    ),
    use_llm: bool = typer.Option(
        False, "--llm", help="Enable LLM-enhanced rules"
    ),
    min_confidence: float = typer.Option(
        0.7,
        "--min-confidence",
        min=0.0,
        max=1.0,
        help="Only report findings at or above this confidence",
    ),
    no_evidence: bool = typer.Option(
        False,
        "--no-evidence",
        help="Disable deterministic graph EvidencePack generation",
    ),
    max_evidence_packs: int = typer.Option(
        50,
        "--max-evidence-packs",
        min=0,
        help="Maximum finding EvidencePacks to attach",
    ),
    state_rules: Optional[List[str]] = typer.Option(
        None,
        "--state-rules",
        help="Load a versioned YAML state-machine rule file (repeatable)",
    ),
):
    """Run unified multi-language diagnosis."""
    try:
        run_unified_diagnosis(
            console,
            project=project,
            output=output,
            output_sarif=output_sarif,
            rules=rule,
            languages=language,
            use_llm=use_llm,
            min_confidence=min_confidence,
            build_evidence_packs=not no_evidence,
            max_evidence_packs=max_evidence_packs,
            state_rule_paths=state_rules,
        )
    except SystemExit:
        raise
    except Exception as e:
        console.print(f"[red]Diagnosis failed: {safe_terminal_text(e)}[/red]")
        raise typer.Exit(1) from e


@unified_app.command("rules")
def unified_rules(
    descriptions: bool = typer.Option(
        True, "--descriptions", "-d", help="Show rule descriptions"
    ),
):
    """List available diagnostic rules."""
    list_rules(console, include_descriptions=descriptions)


@unified_app.command("languages")
def unified_languages():
    """List supported languages."""
    list_languages(console)


# =============================================================================


def _maybe_save_output(result, output_path: Optional[str]) -> None:
    """Atomically save JSON output and fail the command on write errors."""
    if not output_path or result is None:
        return

    import json as _json
    import tempfile
    from dataclasses import asdict, is_dataclass
    from pathlib import Path

    temporary_path = None
    try:
        data = asdict(result) if is_dataclass(result) else result
        payload = _json.dumps(data, ensure_ascii=False, indent=2)
        destination = Path(output_path).expanduser()
        parent = destination.parent
        if not parent.is_dir():
            raise FileNotFoundError(f"parent directory does not exist: {parent}")
        descriptor, temporary_path = tempfile.mkstemp(
            dir=parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            text=True,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.write("\n")
        os.replace(temporary_path, destination)
        temporary_path = None
        console.print(
            f"[green]Report saved to: {safe_terminal_text(destination)}[/green]"
        )
    except Exception as e:
        console.print(
            f"[red]Failed to save output: {safe_terminal_text(e)}[/red]"
        )
        raise typer.Exit(1) from e
    finally:
        if temporary_path:
            try:
                Path(temporary_path).unlink(missing_ok=True)
            except OSError:
                pass


if __name__ == "__main__":
    app()
