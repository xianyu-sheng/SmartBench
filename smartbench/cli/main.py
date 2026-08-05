"""
SmartBench CLI — interactive code diagnosis wizard.

Usage:
    smartbench              # Interactive mode (full wizard)
    smartbench quick        # Quick mode (minimal questions)
    smartbench diagnose     # Diagnosis only
    smartbench unified      # Unified multi-language diagnosis
    smartbench check        # Tool availability check
"""

import json
import os
from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console
from rich.table import Table

from smartbench import __version__
from smartbench.benchmarks import BenchmarkConfigError, BenchmarkRunner, load_benchmark_manifest
from smartbench.cli.phases import (
    build_session_retrieval,
    resolve_project_path,
    run_analysis_session,
    run_diagnose_mode,
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

# --fail-on severity gating (issue #2). Ordered least to most severe.
_FAIL_ON_LEVELS = ("none", "info", "warning", "error")
_SEVERITY_RANK = {"info": 1, "warning": 2, "error": 3}


def _count_findings_at_or_above(result, level: str) -> int:
    """Count findings whose severity is at or above ``level``.

    ``level`` of ``"none"`` disables gating and always returns 0, preserving
    the historical exit-code-0 behavior for callers that do not opt in.
    """
    if level == "none":
        return 0
    threshold = _SEVERITY_RANK[level]
    count = 0
    for finding in getattr(result, "findings", []) or []:
        severity = getattr(finding.severity, "value", finding.severity)
        if _SEVERITY_RANK.get(str(severity), 0) >= threshold:
            count += 1
    return count

benchmark_app = typer.Typer(
    name="benchmark",
    help="Reproducible pre-fix/post-fix benchmark execution",
)
app.add_typer(benchmark_app, name="benchmark")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"smartbench {__version__}")
        raise typer.Exit()


@benchmark_app.command("run")
def benchmark_run(
    manifest: str = typer.Option(..., "--manifest", "-m", help="Benchmark YAML manifest"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Save benchmark JSON"),
):
    """Run declared repository snapshots through the unified engine."""
    try:
        manifest_path = Path(manifest).expanduser().resolve()
        cases = load_benchmark_manifest(manifest_path)
        report = BenchmarkRunner().run(cases)
    except (BenchmarkConfigError, OSError, ValueError) as exc:
        console.print(f"[red]Benchmark failed: {safe_terminal_text(exc)}[/red]")
        raise typer.Exit(1) from exc

    table = Table("Case", "Snapshot", "Findings", "Status", title="Benchmark Results")
    for result in report.results:
        table.add_row(
            result.case_id,
            result.label,
            str(result.findings),
            "[green]PASS[/green]" if result.passed else "[red]FAIL[/red]",
        )
    console.print(table)
    if output:
        destination = Path(output).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        console.print(f"💾 Benchmark report saved to: [cyan]{safe_terminal_text(destination)}[/cyan]")
    if not report.passed:
        raise typer.Exit(1)


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
        session = run_analysis_session(console, project_path)
        graph, hybrid = build_session_retrieval(
            console,
            session,
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


@app.command("check-branches")
def check_branches(
    input_file: str = typer.Option(..., "--input", "-i", help="Quick-mode output JSON file"),
    repo: str = typer.Option(..., "--repo", "-r", help="Path to the git repository that was scanned"),
):
    """Check whether quick-mode findings are already fixed on other branches.

    Reads a quick-scan JSON output, then checks each finding's file location
    against local dev/develop/next/release branches.  Findings whose reported
    function already contains extra resource-cleanup patterns on another
    branch are reported as "already-fixed" — they should NOT be submitted
    as a new issue.
    """
    import json

    from rich.table import Table

    from smartbench.frontends.git_branch_checker import GitBranchChecker

    input_path = Path(input_file)
    if not input_path.exists():
        console.print(f"[red]Input file not found: {input_file}[/red]")
        raise typer.Exit(1)

    try:
        data = json.loads(input_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        console.print(f"[red]Invalid JSON: {exc}[/red]")
        raise typer.Exit(1)

    findings = data.get("final_suggestions", [])
    if isinstance(findings, dict):
        findings = findings.get("suggestions", [])
    if not isinstance(findings, list):
        console.print("[red]Invalid final_suggestions shape (expected a list)[/red]")
        raise typer.Exit(1)
    if not findings:
        console.print("[yellow]No suggestions found in input file.[/yellow]")
        return

    repo_path = Path(repo).resolve()
    if not (repo_path / ".git").exists():
        console.print(f"[red]Not a git repository: {repo}[/red]")
        raise typer.Exit(1)

    try:
        checker = GitBranchChecker(repo_path)
    except Exception as exc:
        console.print(f"[red]Failed to init checker: {exc}[/red]")
        raise typer.Exit(1)

    console.print(f"\nChecking {len(findings)} finding(s) against local branches...\n")

    results = checker.check_findings(findings)
    already, clean = GitBranchChecker.format_report(results)

    if already:
        console.print("[bold yellow]Already-fixed on other branch(es) — suppress submission:[/bold yellow]")
        table = Table("File", "Line", "Fixed on")
        for r in already:
            table.add_row(
                r.file_path,
                str(r.line_start),
                ", ".join(r.already_fixed_on),
            )
        console.print(table)

    console.print(
        f"\n[green]Clean (no prior fix found):[/green] {len(clean)} "
        f"| [yellow]Already-fixed:[/yellow] {len(already)}"
    )
    if not already and not clean:
        console.print("[dim]No findings with location data to check.[/dim]")


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
    fail_on: str = typer.Option(
        "none",
        "--fail-on",
        help=(
            "Exit non-zero when findings at or above this severity are reported "
            "[none|info|warning|error]. Default 'none' always exits 0."
        ),
    ),
    deterministic_output: bool = typer.Option(
        False,
        "--deterministic-output",
        help="Normalize runtime-dependent fields in the JSON report",
    ),
):
    """Run unified multi-language diagnosis."""
    fail_on_normalized = fail_on.strip().lower()
    if fail_on_normalized not in _FAIL_ON_LEVELS:
        console.print(
            f"[red]Invalid --fail-on value: {safe_terminal_text(fail_on)}. "
            f"Expected one of: {', '.join(_FAIL_ON_LEVELS)}[/red]"
        )
        raise typer.Exit(2)

    try:
        result, _ = run_unified_diagnosis(
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
            deterministic_output=deterministic_output,
        )
    except SystemExit:
        raise
    except Exception as e:
        console.print(f"[red]Diagnosis failed: {safe_terminal_text(e)}[/red]")
        raise typer.Exit(2) from e

    gating = _count_findings_at_or_above(result, fail_on_normalized)
    if gating:
        console.print(
            f"[yellow]--fail-on {fail_on_normalized}: {gating} finding(s) "
            f"at or above '{fail_on_normalized}'.[/yellow]"
        )
        raise typer.Exit(1)


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
        if hasattr(result, "to_dict") and callable(result.to_dict):
            data = result.to_dict()
        else:
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
