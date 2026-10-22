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
from smartbench.benchmarks import (
    BenchmarkConfigError,
    BenchmarkRunner,
    collect_runs,
    compute_metrics,
    load_benchmark_manifest,
)
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
from smartbench.trajectory import TrajectoryStore, replay_document

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

trajectory_app = typer.Typer(
    name="trajectory",
    help="Audit-trajectory utilities: list, show, export, replay",
)
app.add_typer(trajectory_app, name="trajectory")

consent_app = typer.Typer(
    name="consent",
    help="Stage third-party trajectories and send consent requests by email",
)
app.add_typer(consent_app, name="consent")


@consent_app.command("list")
def consent_list(
    queue: Optional[str] = typer.Option(
        None, "--queue", help="Consent queue (default: ~/.smartbench/consent/outreach.json)"
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON"),
):
    """List staged/requested consent requests."""
    from smartbench.consent.queue import default_queue_path, load_queue

    document = load_queue(queue or None)
    requests = document.get("requests", [])
    if json_output:
        console.print_json(data=requests)
        return
    table = Table("Repository", "Status", "Trajectories", "Requested", "Note")
    for entry in requests:
        table.add_row(
            safe_terminal_text(str(entry.get("repository", ""))),
            safe_terminal_text(str(entry.get("status", ""))),
            str(len(entry.get("trajectory_ids") or [])),
            safe_terminal_text(str(entry.get("requested_at") or "-")),
            safe_terminal_text(str(entry.get("note", ""))),
        )
    console.print(table)
    console.print(f"[dim]Queue: {safe_terminal_text(str(default_queue_path() if not queue else queue))}[/dim]")


@consent_app.command("stage")
def consent_stage(
    repository: str = typer.Argument("", help="owner/repo; omit with --project"),
    project: Optional[str] = typer.Option(
        None, "--project", help="Local git checkout used to discover owner/repo"
    ),
    trajectory_id: str = typer.Option("", "--trajectory-id"),
    queue: Optional[str] = typer.Option(None, "--queue"),
):
    """Add a repository to the local consent queue (no email is sent)."""
    from smartbench.consent.queue import discover_github_repository, stage

    name = repository.strip()
    if not name and project:
        name = discover_github_repository(project) or ""
    if not name:
        console.print("[red]Provide owner/repo or a git checkout with a GitHub origin.[/red]")
        raise typer.Exit(1)
    try:
        entry = stage(name, trajectory_id, project_name=project or "", queue_path=queue)
    except ValueError as exc:
        console.print(f"[red]{safe_terminal_text(str(exc))}[/red]")
        raise typer.Exit(1) from exc
    console.print(
        f"[green]Staged: {safe_terminal_text(entry['repository'])} "
        f"({entry['status']}, {len(entry['trajectory_ids'])} trajectory id(s))[/green]"
    )


@consent_app.command("resolve")
def consent_resolve(
    repository: str = typer.Argument(..., help="owner/repo"),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON"),
):
    """Resolve a public maintainer contact (profile or SECURITY/CONTRIBUTING)."""
    from smartbench.consent.contacts import resolve_contact

    contact = resolve_contact(repository)
    if json_output:
        console.print_json(data=contact)
        return
    if contact is None:
        console.print(
            "[yellow]No public contact found; request will be marked contact_missing.[/yellow]"
        )
        return
    console.print(
        f"[green]{safe_terminal_text(repository)}:[/green] "
        f"{safe_terminal_text(contact['email'])} "
        f"[dim]({safe_terminal_text(contact['source'])})[/dim]"
    )


@consent_app.command("send")
def consent_send(
    send: bool = typer.Option(
        False, "--send", help="Actually send; without it this is a dry run"
    ),
    limit: int = typer.Option(5, "--limit", min=1, max=10),
    repository: Optional[str] = typer.Option(None, "--repository", help="Only this owner/repo"),
    queue: Optional[str] = typer.Option(None, "--queue"),
):
    """Resolve contacts and send staged consent requests (dry run by default)."""
    from smartbench.consent.email import send_pending

    try:
        results = send_pending(
            queue_path=queue,
            dry_run=not send,
            limit=limit,
            repository=repository,
        )
    except RuntimeError as exc:
        console.print(f"[red]{safe_terminal_text(str(exc))}[/red]")
        raise typer.Exit(1) from exc
    table = Table("Repository", "Status", "Contact source", "Message-ID")
    for result in results:
        table.add_row(
            safe_terminal_text(str(result.get("repository", ""))),
            safe_terminal_text(str(result.get("status", ""))),
            safe_terminal_text(str(result.get("contact_source") or "-")),
            safe_terminal_text(str(result.get("message_id") or "-")),
        )
    console.print(table)
    if not send:
        console.print("[yellow]Dry run: no email sent. Re-run with --send.[/yellow]")
    elif not results:
        console.print("[dim]No staged requests to send.[/dim]")


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


@benchmark_app.command("metrics")
def benchmark_metrics(
    manifest: str = typer.Option(..., "--manifest", "-m", help="Positive benchmark manifest"),
    negative: Optional[str] = typer.Option(
        None, "--negative", help="Negative-corpus manifest (counts toward FPR)"
    ),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Write the metrics JSON"),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON"),
):
    """Compute precision, recall, false-positive rate and latency from the corpora.

    Metrics are case-level and scoped to each case's declared target rules;
    findings outside those rules are ignored. Counts are deterministic; the
    latency block is a measurement and may vary between runs.
    """
    try:
        positive_cases = load_benchmark_manifest(Path(manifest).expanduser().resolve())
        positive_runs = collect_runs(
            positive_cases, BenchmarkRunner().run(positive_cases)
        )
        negative_runs = []
        if negative:
            negative_cases = load_benchmark_manifest(
                Path(negative).expanduser().resolve()
            )
            negative_runs = collect_runs(
                negative_cases, BenchmarkRunner().run(negative_cases)
            )
        metrics = compute_metrics(positive_runs, negative_runs)
    except (BenchmarkConfigError, OSError, ValueError) as exc:
        console.print(f"[red]Metrics failed: {safe_terminal_text(exc)}[/red]")
        raise typer.Exit(1) from exc

    text = json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True)
    if output:
        destination = Path(output).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
    if json_output:
        console.print(text, soft_wrap=True)
        return

    overall = metrics["overall"]
    table = Table(
        "Scope", "TP", "FP", "FN", "TN", "Precision", "Recall", "F1", "FPR"
    )
    table.add_row("overall", *(_metric_cells(overall)))
    for rule, entry in metrics["by_rule"].items():
        table.add_row(safe_terminal_text(rule), *(_metric_cells(entry)))
    console.print(table)
    latency = overall["latency_ms"]
    console.print(
        f"\n[dim]latency p50 {latency['p50']} ms · p95 {latency['p95']} ms · "
        f"max {latency['max']} ms · abstention: {metrics['abstention_rate']}[/dim]"
    )
    if output:
        console.print(
            f"💾 Metrics report saved to: [cyan]{safe_terminal_text(str(Path(output).expanduser().resolve()))}[/cyan]"
        )


def _metric_cells(entry: dict[str, object]) -> tuple[str, ...]:
    """Format one metrics row for the human table."""
    def number(key: str) -> str:
        value = entry.get(key)
        return "-" if value is None else f"{value:.3f}"

    return (
        str(entry.get("tp", 0)),
        str(entry.get("fp", 0)),
        str(entry.get("fn", 0)),
        str(entry.get("tn", 0)),
        number("precision"),
        number("recall"),
        number("f1"),
        number("false_positive_rate"),
    )


@trajectory_app.command("list")
def trajectory_list(
    directory: Optional[str] = typer.Option(None, "--dir", help="Trajectory directory"),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON"),
):
    """List recorded audit trajectories."""
    entries = TrajectoryStore(directory or None).list()
    if json_output:
        console.print(
            json.dumps(entries, ensure_ascii=False, indent=2, sort_keys=True),
            soft_wrap=True,
        )
        return
    if not entries:
        console.print("[yellow]No trajectories recorded yet.[/yellow]")
        return
    table = Table("ID", "Recorded", "Project", "Findings", "Schema")
    for entry in entries:
        table.add_row(
            safe_terminal_text(str(entry.get("trajectory_id", ""))),
            safe_terminal_text(str(entry.get("recorded_at", ""))),
            safe_terminal_text(str(entry.get("project", ""))),
            str(entry.get("findings", 0)),
            safe_terminal_text(str(entry.get("schema_version", ""))),
        )
    console.print(table)


@trajectory_app.command("show")
def trajectory_show(
    trajectory_id: str = typer.Argument(..., help="Trajectory id"),
    directory: Optional[str] = typer.Option(None, "--dir", help="Trajectory directory"),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON"),
):
    """Show one recorded trajectory."""
    document = TrajectoryStore(directory or None).load(trajectory_id)
    if document is None:
        console.print(
            f"[red]Trajectory not found: {safe_terminal_text(trajectory_id)}[/red]"
        )
        raise typer.Exit(1)
    if json_output:
        console.print(
            json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True),
            soft_wrap=True,
        )
        return
    project = document.get("project") or {}
    usage = document.get("usage") or {}
    table = Table("Field", "Value")
    table.add_row("id", safe_terminal_text(str(document.get("trajectory_id", ""))))
    table.add_row("recorded_at", safe_terminal_text(str(document.get("recorded_at", ""))))
    table.add_row("schema", safe_terminal_text(str(document.get("schema_version", ""))))
    table.add_row("project", safe_terminal_text(str(project.get("name", ""))))
    table.add_row(
        "languages",
        safe_terminal_text(", ".join(project.get("languages") or [])),
    )
    table.add_row("findings", str(len(document.get("findings") or [])))
    table.add_row(
        "review_status",
        safe_terminal_text(str(document.get("review_status", ""))),
    )
    table.add_row(
        "signature",
        safe_terminal_text(str(document.get("finding_signature", ""))),
    )
    if usage:
        table.add_row(
            "usage",
            safe_terminal_text(json.dumps(usage, ensure_ascii=False)[:200]),
        )
    console.print(table)


@trajectory_app.command("export")
def trajectory_export(
    output: str = typer.Argument(..., help="Output JSONL path"),
    directory: Optional[str] = typer.Option(None, "--dir", help="Trajectory directory"),
    format: str = typer.Option("jsonl", "--format", help="Export format (jsonl)"),
):
    """Export all recorded trajectories into one dataset file."""
    try:
        count = TrajectoryStore(directory or None).export(output, format=format)
    except (OSError, ValueError) as exc:
        console.print(f"[red]Export failed: {safe_terminal_text(str(exc))}[/red]")
        raise typer.Exit(1) from exc
    console.print(
        f"[green]Exported {count} trajectories to "
        f"{safe_terminal_text(output)}[/green]"
    )


@trajectory_app.command("replay")
def trajectory_replay(
    trajectory_id: str = typer.Argument(..., help="Trajectory id"),
    directory: Optional[str] = typer.Option(None, "--dir", help="Trajectory directory"),
    project: Optional[str] = typer.Option(
        None,
        "--project",
        help="Checkout to re-run the deterministic stage against (LLM stages are skipped)",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON"),
):
    """Replay a trajectory: schema check plus an offline deterministic comparison."""
    document = TrajectoryStore(directory or None).load(trajectory_id)
    if document is None:
        console.print(
            f"[red]Trajectory not found: {safe_terminal_text(trajectory_id)}[/red]"
        )
        raise typer.Exit(1)
    result = replay_document(document, project_path=project)
    if json_output:
        console.print(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
            soft_wrap=True,
        )
    else:
        table = Table("Check", "Value")
        for key in (
            "ok",
            "reproduced",
            "trajectory_id",
            "stored_deterministic_findings",
            "replayed_deterministic_findings",
            "matched",
            "llm",
        ):
            if key in result:
                table.add_row(key, safe_terminal_text(str(result[key])))
        if result.get("only_recorded"):
            table.add_row(
                "only_recorded",
                safe_terminal_text(str(result["only_recorded"])[:300]),
            )
        if result.get("only_replayed"):
            table.add_row(
                "only_replayed",
                safe_terminal_text(str(result["only_replayed"])[:300]),
            )
        console.print(table)
    if not result.get("ok"):
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
                save_trajectory=True,
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
    sandbox_backend: str = typer.Option(
        "local",
        "--sandbox-backend",
        help="Execution backend: local (host) or container (rootless podman/docker)",
    ),
    sandbox_image: Optional[str] = typer.Option(
        None,
        "--sandbox-image",
        help="Container image containing the project toolchain",
    ),
    sandbox_runtime: Optional[str] = typer.Option(
        None,
        "--sandbox-runtime",
        help="Container runtime: podman or docker (auto-detect by default)",
    ),
    max_workers: int = typer.Option(
        1,
        "--max-workers",
        min=1,
        max=8,
        help="Shard the LLM review across N workers (1 = single debate)",
    ),
    feedback: bool = typer.Option(
        False,
        "--feedback/--no-feedback",
        help="Apply feedback priors to suggestion ordering (never suppresses)",
    ),
    feedback_file: Optional[str] = typer.Option(
        None,
        "--feedback-file",
        help="Feedback JSONL file (default: ~/.smartbench/feedback/events.jsonl)",
    ),
    trials: int = typer.Option(
        1,
        "--trials",
        min=1,
        max=10,
        help="Run the LLM review N times and report trial stability",
    ),
    temperature: float = typer.Option(
        0.3,
        "--temperature",
        help="LLM sampling temperature (0.0–2.0)",
    ),
    seed: Optional[int] = typer.Option(
        None,
        "--seed",
        help="LLM sampling seed when the provider supports it",
    ),
    no_trajectory: bool = typer.Option(
        False,
        "--no-trajectory",
        help="Do not auto-save the redacted audit trajectory for this run",
    ),
    trajectory_dir: Optional[str] = typer.Option(
        None,
        "--trajectory-dir",
        help="Trajectory directory (default: ~/.smartbench/trajectories)",
    ),
):
    """Quick diagnosis — auto-detect everything, minimal prompts."""
    result = run_quick_mode(
        console,
        project=project,
        concern=concern,
        enable_sandbox=sandbox,
        sandbox_backend=sandbox_backend,
        sandbox_image=sandbox_image,
        sandbox_runtime=sandbox_runtime,
        max_workers=max_workers,
        apply_feedback=feedback,
        feedback_file=feedback_file,
        trials=trials,
        temperature=temperature,
        seed=seed,
        save_trajectory=not no_trajectory,
        trajectory_dir=trajectory_dir,
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
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON instead of rich tables"),
    output_file: str = typer.Option("", "--output", "-o", help="Write JSON result to this file (requires --json)"),
):
    """Check whether quick-mode findings are already fixed on other branches.

    Reads a quick-scan JSON output, then checks each finding's file location
    against local dev/develop/next/release branches AND commit history.
    Findings whose reported function already contains extra resource-cleanup
    patterns on another branch, or whose cleanup pattern was introduced by a
    prior commit, are reported as "already-fixed" — they should NOT be
    submitted as a new issue.
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

    if not json_output:
        console.print(f"\nChecking {len(findings)} finding(s) against branches and history...\n")

    results = checker.check_findings(findings)
    already, clean = GitBranchChecker.format_report(results)

    # ── JSON mode ──────────────────────────────────────────────────
    if json_output:
        payload = {
            "already_fixed": [r.to_dict() for r in already],
            "clean": [r.to_dict() for r in clean],
            "errors": [r.to_dict() for r in results if r.error],
            "summary": {
                "total": len(results),
                "already_fixed": len(already),
                "clean": len(clean),
                "errors": sum(1 for r in results if r.error),
            },
        }
        text = json.dumps(payload, indent=2, ensure_ascii=False)
        if output_file:
            Path(output_file).write_text(text, encoding="utf-8")
            console.print(f"[green]JSON written to {output_file}[/green]")
        else:
            console.print(text)
        return

    # ── Rich table mode ────────────────────────────────────────────
    if already:
        console.print("[bold yellow]Already-fixed — suppress submission:[/bold yellow]")
        table = Table("File", "Line", "Fixed on", "Evidence")
        for r in already:
            table.add_row(
                r.file_path,
                str(r.line_start),
                ", ".join(r.already_fixed_on),
                r.fix_evidence or "",
            )
        console.print(table)

    console.print(
        f"\n[green]Clean (no prior fix found):[/green] {len(clean)} "
        f"| [yellow]Already-fixed:[/yellow] {len(already)}"
    )
    if not already and not clean:
        console.print("[dim]No findings with location data to check.[/dim]")


intent_app = typer.Typer(name="intent", help="Manage the user-side intent memory file")
app.add_typer(intent_app, name="intent")

feedback_app = typer.Typer(
    name="feedback", help="Record human decisions and rank findings offline"
)
app.add_typer(feedback_app, name="feedback")


@intent_app.command("list")
def intent_list(
    memory_file: str = typer.Option("", "--file", help="Intent memory file (default: ~/.smartbench/intent.yaml)"),
):
    """List the declared intent-memory entries."""
    from smartbench.analysis import MemoryStore

    store = MemoryStore.load(Path(memory_file) if memory_file else None)
    if not store.entries:
        console.print("[yellow]No intent memory entries.[/yellow]")
        return
    table = Table("Label", "Path", "Rule", "Reason", "Source")
    for entry in store.entries:
        table.add_row(
            entry.label.value,
            safe_terminal_text(entry.path or "*"),
            safe_terminal_text(entry.rule_id or "*"),
            safe_terminal_text(entry.reason),
            safe_terminal_text(entry.source),
        )
    console.print(table)


@intent_app.command("validate")
def intent_validate(
    memory_file: str = typer.Option("", "--file", help="Intent memory file (default: ~/.smartbench/intent.yaml)"),
):
    """Validate the intent memory file schema."""
    import yaml as _yaml

    from smartbench.analysis import MemoryStore, validate_document

    target = Path(memory_file) if memory_file else MemoryStore.default_path()
    if not target.is_file():
        console.print(f"[red]File not found: {safe_terminal_text(str(target))}[/red]")
        raise typer.Exit(1)
    try:
        document = _yaml.safe_load(target.read_text(encoding="utf-8"))
    except _yaml.YAMLError as exc:
        console.print(f"[red]Invalid YAML: {safe_terminal_text(str(exc))}[/red]")
        raise typer.Exit(1) from exc
    errors = validate_document(document)
    if errors:
        for error in errors:
            console.print(f"[red]- {safe_terminal_text(error)}[/red]")
        raise typer.Exit(1)
    console.print(f"[green]OK: {safe_terminal_text(str(target))}[/green]")


@intent_app.command("add")
def intent_add(
    label: str = typer.Option(..., "--label", help="adjudication label for matching findings"),
    path: str = typer.Option("", "--path", help="Relative path glob (optional)"),
    rule_id: str = typer.Option("", "--rule-id", help="Rule id to match (optional)"),
    reason: str = typer.Option("", "--reason", help="Why this decision applies"),
    source: str = typer.Option("", "--source", help="URL or reference for the decision"),
    memory_file: str = typer.Option("", "--file", help="Intent memory file (default: ~/.smartbench/intent.yaml)"),
):
    """Append one entry to the intent memory file."""
    from smartbench.analysis import Adjudication, MemoryEntry, MemoryStore

    try:
        parsed_label = Adjudication(label)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in Adjudication)
        console.print(f"[red]--label must be one of: {allowed}[/red]")
        raise typer.Exit(1) from exc
    target = Path(memory_file) if memory_file else None
    store = MemoryStore.load(target)
    before = len(store.entries)
    store = store.with_entry(
        MemoryEntry(
            label=parsed_label,
            reason=reason,
            source=source,
            path=path,
            rule_id=rule_id,
        )
    )
    saved = store.save(target)
    console.print(
        f"[green]Intent memory saved to {safe_terminal_text(str(saved))}[/green] "
        f"({len(store.entries) - before} new entries, {len(store.entries)} total)"
    )


@feedback_app.command("add")
def feedback_add(
    label: str = typer.Option(..., "--label", help="actionable / design_trade_off / intentional / out_of_scope / theoretical"),
    rule_id: str = typer.Option("", "--rule-id", help="Rule id the decision applies to"),
    language: str = typer.Option("", "--language", help="Language (python, go, ...)"),
    file_path: str = typer.Option("", "--file-path", help="Project-relative file path"),
    line_start: int = typer.Option(0, "--line", help="Line number"),
    confidence: Optional[float] = typer.Option(None, "--confidence", min=0.0, max=1.0),
    reason: str = typer.Option("", "--reason", help="Why this decision applies"),
    source: str = typer.Option("", "--source", help="URL or reference for the decision"),
    trajectory_id: str = typer.Option("", "--trajectory-id", help="Link to a recorded trajectory"),
    finding_signature: str = typer.Option("", "--finding-signature"),
    feedback_file: str = typer.Option("", "--file", help="Feedback JSONL file (default: ~/.smartbench/feedback/events.jsonl)"),
):
    """Append one human decision as a feedback event."""
    from smartbench.feedback import FeedbackStore, make_event

    try:
        event = make_event(
            label=label,
            rule_id=rule_id,
            language=language,
            file_path=file_path,
            line_start=line_start,
            confidence=confidence,
            reason=reason,
            source=source,
            trajectory_id=trajectory_id,
            finding_signature=finding_signature,
        )
    except ValueError as exc:
        console.print(f"[red]{safe_terminal_text(str(exc))}[/red]")
        raise typer.Exit(1) from exc
    store = FeedbackStore(Path(feedback_file) if feedback_file else None)
    added = store.append(event)
    if not added:
        console.print(
            "[yellow]Event not written (duplicate or store unavailable).[/yellow]"
        )
        return
    console.print(
        f"[green]Feedback event recorded: {safe_terminal_text(event.event_id)}[/green] "
        f"({len(store.events())} total)"
    )


@feedback_app.command("list")
def feedback_list(
    rule_id: Optional[str] = typer.Option(None, "--rule-id"),
    language: Optional[str] = typer.Option(None, "--language"),
    label: Optional[str] = typer.Option(None, "--label"),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON"),
    feedback_file: str = typer.Option("", "--file", help="Feedback JSONL file"),
):
    """List recorded feedback events."""
    from smartbench.feedback import FeedbackStore

    store = FeedbackStore(Path(feedback_file) if feedback_file else None)
    events = store.filter(rule_id=rule_id, language=language, label=label)
    if json_output:
        payload = {
            "schema_version": "smartbench.feedback.list/v1",
            "events": [event.to_dict() for event in events],
            "counts": store.counts(events),
        }
        console.print(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            soft_wrap=True,
        )
        return
    if not events:
        console.print("[yellow]No feedback events recorded.[/yellow]")
        return
    table = Table("Recorded", "Label", "Rule", "Language", "Location", "Conf", "Reason")
    for event in events:
        location = f"{event.file_path}:{event.line_start}" if event.file_path else "*"
        table.add_row(
            safe_terminal_text(event.recorded_at),
            event.label,
            safe_terminal_text(event.rule_id or "*"),
            safe_terminal_text(event.language or "*"),
            safe_terminal_text(location),
            "-" if event.confidence is None else f"{event.confidence:.2f}",
            safe_terminal_text(event.reason),
        )
    console.print(table)


@feedback_app.command("policy")
def feedback_policy(
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON"),
    feedback_file: str = typer.Option("", "--file", help="Feedback JSONL file"),
):
    """Show offline priors and advisory thresholds (never auto-applied)."""
    from smartbench.feedback import FeedbackStore, build_priors, suggest_thresholds

    store = FeedbackStore(Path(feedback_file) if feedback_file else None)
    events = store.events()
    priors = build_priors(events)
    payload = {
        "schema_version": "smartbench.feedback.policy/v1",
        "events": len(events),
        "counts": store.counts(events),
        "priors": [prior.to_dict() for _, prior in sorted(priors.items())],
        "advisory_thresholds": suggest_thresholds(events, priors=priors),
        "suppressed": 0,
        "note": "advisory only; findings are never suppressed",
    }
    if json_output:
        console.print(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            soft_wrap=True,
        )
        return
    if not priors:
        console.print("[yellow]No feedback events recorded.[/yellow]")
        return
    table = Table("Rule", "Language", "Support", "Actionable", "Ratio")
    for _, prior in sorted(priors.items()):
        table.add_row(
            safe_terminal_text(prior.rule_id),
            safe_terminal_text(prior.language),
            str(prior.support),
            str(prior.actionable),
            f"{prior.actionable_ratio:.3f}",
        )
    console.print(table)
    for suggestion in payload["advisory_thresholds"]:
        console.print(
            "[dim]advisory: "
            f"{safe_terminal_text(str(suggestion['rule_id']))} / "
            f"{safe_terminal_text(str(suggestion['language']))} → "
            f"min_confidence={suggestion['suggested_min_confidence']} "
            "(not applied automatically)[/dim]"
        )


@feedback_app.command("rank")
def feedback_rank(
    input_file: str = typer.Option(..., "--input", "-i", help="Unified or quick report JSON file"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Write ranked JSON"),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON"),
    feedback_file: str = typer.Option("", "--file", help="Feedback JSONL file"),
):
    """Rank report findings with feedback priors; every finding is preserved."""
    from smartbench.feedback import FeedbackStore, rank_findings
    from smartbench.frontends.upstream_checker import extract_findings

    source = Path(input_file)
    if not source.is_file():
        console.print(f"[red]Input file not found: {safe_terminal_text(input_file)}[/red]")
        raise typer.Exit(1)
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        console.print(f"[red]Invalid JSON: {safe_terminal_text(str(exc))}[/red]")
        raise typer.Exit(1) from exc
    findings = extract_findings(document)
    store = FeedbackStore(Path(feedback_file) if feedback_file else None)
    payload = rank_findings(findings, store.events())
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if output:
        destination = Path(output).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
    if json_output:
        console.print(text, soft_wrap=True)
        return
    ranked = payload["findings"]
    if not ranked:
        console.print("[yellow]No findings found in input file.[/yellow]")
        return
    table = Table("Rank", "Rule", "Location", "Actionable ratio", "Support", "Confidence")
    for index, finding in enumerate(ranked, start=1):
        feedback = finding.get("feedback") or {}
        ratio = feedback.get("actionable_ratio")
        confidence = finding.get("confidence")
        table.add_row(
            str(index),
            safe_terminal_text(str(finding.get("rule_id", ""))),
            safe_terminal_text(
                f"{finding.get('file_path', '')}:{finding.get('line_start', 0)}"
            ),
            "-" if ratio is None else f"{ratio:.3f}",
            str(feedback.get("support", 0)),
            "-" if confidence is None else f"{float(confidence):.2f}",
        )
    console.print(table)
    console.print(
        f"[dim]{len(ranked)} findings ranked, 0 suppressed (ranking only).[/dim]"
    )
    if output:
        console.print(
            f"[green]Ranked report saved to {safe_terminal_text(str(Path(output).expanduser()))}[/green]"
        )


@app.command("check-upstream")
def check_upstream(
    input_file: str = typer.Option(..., "--input", "-i", help="Unified or quick report JSON file"),
    repo: str = typer.Option(..., "--repo", "-r", help="Upstream repository in owner/name form"),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON instead of a table"),
    output_file: str = typer.Option("", "--output", "-o", help="Write JSON result to this file (requires --json)"),
    max_findings: int = typer.Option(10, "--max-findings", help="Maximum findings to look up"),
    token_env: str = typer.Option("GITHUB_TOKEN", "--token-env", help="Environment variable holding a GitHub token"),
    record: bool = typer.Option(
        True,
        "--record/--no-record",
        help="Record discussed upstream results in the user intent memory",
    ),
    memory_file: str = typer.Option("", "--memory-file", help="Intent memory file (default: ~/.smartbench/intent.yaml)"),
):
    """Check whether findings were already raised or declined upstream.

    Searches the upstream repository's issues and pull requests for each
    finding. A closed item with explicit intent markers (wontfix, by design,
    trade-off) is reported as ``discussed`` — do not re-file it without new
    evidence.
    """
    from smartbench.frontends.upstream_checker import (
        GitHubUpstreamChecker,
        extract_findings,
        record_discussed,
    )

    input_path = Path(input_file)
    if not input_path.exists():
        console.print(f"[red]Input file not found: {input_file}[/red]")
        raise typer.Exit(1)
    try:
        document = json.loads(input_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        console.print(f"[red]Invalid JSON: {safe_terminal_text(exc)}[/red]")
        raise typer.Exit(1) from exc
    findings = extract_findings(document)
    if not findings:
        console.print("[yellow]No findings found in input file.[/yellow]")
        return

    token = os.environ.get(token_env, "").strip()
    try:
        checker = GitHubUpstreamChecker(repo, token=token, max_findings=max_findings)
    except ValueError as exc:
        console.print(f"[red]{safe_terminal_text(str(exc))}[/red]")
        raise typer.Exit(1) from exc
    try:
        results = checker.check(findings)
    finally:
        checker.close()

    memory_record: dict[str, object] = {}
    if record:
        added, total, saved = record_discussed(
            results,
            Path(memory_file) if memory_file else None,
        )
        memory_record = {"added": added, "total": total, "saved": saved}
        if not json_output:
            if added:
                console.print(
                    f"[green]Intent memory saved to {safe_terminal_text(saved)}[/green] "
                    f"({added} new entries, {total} total)"
                )
            else:
                console.print("[dim]No new upstream discussions to record.[/dim]")

    payload = GitHubUpstreamChecker.format_report(results)
    if memory_record:
        payload["memory"] = memory_record
    if json_output:
        text = json.dumps(payload, indent=2, ensure_ascii=False)
        if output_file:
            Path(output_file).write_text(text, encoding="utf-8")
            console.print(f"[green]JSON written to {output_file}[/green]")
        else:
            console.print(text, soft_wrap=True)
        return

    table = Table("Finding", "Status", "Matches")
    for result in results:
        label = f"{result.rule_id} @ {result.file_path}:{result.line_start}"
        detail = "; ".join(
            f"{match.state}{'*' if match.intent_hint else ''} {match.url}"
            for match in result.matches[:2]
        ) or result.error
        table.add_row(
            safe_terminal_text(label),
            result.status,
            safe_terminal_text(detail),
        )
    console.print(table)
    summary = payload["summary"]
    console.print(
        f"\n[green]none:[/green] {summary.get('none', 0)} "
        f"| [yellow]mentioned:[/yellow] {summary.get('mentioned', 0)} "
        f"| [magenta]discussed:[/magenta] {summary.get('discussed', 0)} "
        f"| [dim]closed:[/dim] {summary.get('closed', 0)}"
    )
    console.print(
        "[dim]* = explicit intent markers (wontfix / by design / trade-off)[/dim]"
    )


@app.command("preflight")
def preflight(
    input_file: str = typer.Option(..., "--input", "-i", help="Unified or quick report JSON file"),
    repo: str = typer.Option("", "--repo", "-r", help="Local git repository for branch/history checks"),
    upstream: str = typer.Option("", "--upstream", help="Upstream owner/name for the online issue check"),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON instead of a table"),
    output_file: str = typer.Option("", "--output", "-o", help="Write JSON result to this file (requires --json)"),
    max_findings: int = typer.Option(10, "--max-findings", help="Maximum findings to look up upstream"),
    token_env: str = typer.Option("GITHUB_TOKEN", "--token-env", help="Environment variable holding a GitHub token"),
    memory_file: str = typer.Option("", "--memory-file", help="Intent memory file (default: ~/.smartbench/intent.yaml)"),
    branches: bool = typer.Option(True, "--branches/--no-branches", help="Run the local branch/history check"),
    record: bool = typer.Option(
        True,
        "--record/--no-record",
        help="Record discussed upstream results in the user intent memory",
    ),
    feedback: bool = typer.Option(
        False,
        "--feedback/--no-feedback",
        help="Annotate and order rows with feedback priors (never suppresses)",
    ),
    feedback_file: str = typer.Option(
        "",
        "--feedback-file",
        help="Feedback JSONL file (default: ~/.smartbench/feedback/events.jsonl)",
    ),
):
    """Unified pre-submission check: branches, declared intent and upstream history.

    Each finding gets one verdict:

    - ``already_fixed`` — the fix exists on another branch or in history;
    - ``declared`` — the user intent memory declares the pattern intentional,
      an accepted trade-off or out of scope;
    - ``discussed`` — a closed upstream issue/PR explains the behaviour;
    - ``mentioned`` — an open upstream issue/PR already exists;
    - ``submit`` — nothing blocks submission.
    """
    from smartbench.analysis import MemoryStore, memory_entry_for
    from smartbench.frontends.upstream_checker import (
        GitHubUpstreamChecker,
        extract_findings,
        record_discussed,
    )

    input_path = Path(input_file)
    if not input_path.exists():
        console.print(f"[red]Input file not found: {input_file}[/red]")
        raise typer.Exit(1)
    try:
        document = json.loads(input_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        console.print(f"[red]Invalid JSON: {safe_terminal_text(exc)}[/red]")
        raise typer.Exit(1) from exc
    findings = extract_findings(document)
    if not findings:
        console.print("[yellow]No findings found in input file.[/yellow]")
        return

    memory = MemoryStore.load(Path(memory_file) if memory_file else None)

    branch_by_key: dict[tuple[str, int], object] = {}
    if branches and repo:
        from smartbench.frontends.git_branch_checker import GitBranchChecker

        repo_path = Path(repo).resolve()
        if not (repo_path / ".git").exists():
            console.print(f"[red]Not a git repository: {repo}[/red]")
            raise typer.Exit(1)
        try:
            branch_checker = GitBranchChecker(repo_path)
        except Exception as exc:
            console.print(
                f"[red]Failed to init branch checker: {safe_terminal_text(str(exc))}[/red]"
            )
            raise typer.Exit(1) from exc
        branch_input = [
            {
                "location": {
                    "file_path": finding["file_path"],
                    "line_start": finding["line_start"],
                }
            }
            for finding in findings
            if finding.get("file_path") and finding.get("line_start")
        ]
        for result in branch_checker.check_findings(branch_input):
            branch_by_key[(result.file_path, result.line_start)] = result

    upstream_results: list = []
    if upstream:
        token = os.environ.get(token_env, "").strip()
        try:
            upstream_checker = GitHubUpstreamChecker(
                upstream, token=token, max_findings=max_findings
            )
        except ValueError as exc:
            console.print(f"[red]{safe_terminal_text(str(exc))}[/red]")
            raise typer.Exit(1) from exc
        try:
            upstream_results = upstream_checker.check(findings)
        finally:
            upstream_checker.close()
        if record:
            added, total, saved = record_discussed(
                upstream_results,
                Path(memory_file) if memory_file else None,
            )
            if added and not json_output:
                console.print(
                    f"[green]Intent memory saved to {safe_terminal_text(saved)}[/green] "
                    f"({added} new entries, {total} total)"
                )

    rows: list[dict[str, object]] = []
    for index, finding in enumerate(findings):
        branch = branch_by_key.get((finding["file_path"], finding["line_start"]))
        entry = memory_entry_for(
            memory,
            str(finding.get("rule_id", "")),
            str(finding.get("file_path", "")),
        )
        upstream_result = upstream_results[index] if index < len(upstream_results) else None
        if branch is not None and getattr(branch, "already_fixed_on", None):
            verdict = "already_fixed"
            detail = "fixed on: " + ", ".join(branch.already_fixed_on)
        elif entry is not None:
            verdict = "declared"
            detail = f"intent memory: {entry.label.value} — {entry.reason}"
        elif upstream_result is not None and upstream_result.status == "discussed":
            verdict = "discussed"
            detail = upstream_result.matches[0].url if upstream_result.matches else ""
        elif upstream_result is not None and upstream_result.status == "mentioned":
            verdict = "mentioned"
            detail = upstream_result.matches[0].url if upstream_result.matches else ""
        else:
            verdict = "submit"
            detail = ""
        rows.append(
            {
                "rule_id": finding["rule_id"],
                "file_path": finding["file_path"],
                "line_start": finding["line_start"],
                "verdict": verdict,
                "detail": detail,
            }
        )

    if feedback:
        from smartbench.feedback import (
            FeedbackStore,
            build_priors,
            finding_language,
            prior_for,
        )

        events = FeedbackStore(Path(feedback_file) if feedback_file else None).events()
        priors = build_priors(events)
        verdict_rank = {
            "already_fixed": 0,
            "declared": 1,
            "discussed": 2,
            "mentioned": 3,
            "submit": 4,
        }
        scored = []
        for index, row in enumerate(rows):
            prior = prior_for(
                priors,
                str(row["rule_id"]),
                finding_language({"file_path": str(row["file_path"])}),
            )
            score = prior.actionable_ratio if prior is not None else 0.5
            row["feedback"] = (
                {
                    "score": score,
                    "actionable_ratio": prior.actionable_ratio,
                    "support": prior.support,
                    "label_counts": dict(prior.label_counts),
                }
                if prior is not None
                else {
                    "score": score,
                    "actionable_ratio": None,
                    "support": 0,
                    "label_counts": {},
                }
            )
            scored.append(
                (verdict_rank.get(str(row["verdict"]), 9), -score, index, row)
            )
        scored.sort(key=lambda item: (item[0], item[1], item[2]))
        rows = [item[3] for item in scored]

    counts: dict[str, int] = {}
    for row in rows:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    payload = {"findings": rows, "summary": {"total": len(rows), **counts}}

    if json_output:
        text = json.dumps(payload, indent=2, ensure_ascii=False)
        if output_file:
            Path(output_file).write_text(text, encoding="utf-8")
            console.print(f"[green]JSON written to {output_file}[/green]")
        else:
            console.print(text, soft_wrap=True)
        return

    headers = ["Finding", "Verdict", "Detail"] + (["Prior"] if feedback else [])
    table = Table(*headers)
    for row in rows:
        cells = [
            safe_terminal_text(f"{row['rule_id']} @ {row['file_path']}:{row['line_start']}"),
            row["verdict"],
            safe_terminal_text(str(row["detail"])[:120]),
        ]
        if feedback:
            annotation = row.get("feedback") or {}
            ratio = annotation.get("actionable_ratio")
            cells.append(
                "-"
                if ratio is None
                else f"{ratio:.2f}/{annotation.get('support', 0)}"
            )
        table.add_row(*cells)
    console.print(table)
    if feedback:
        console.print(
            "[dim]Feedback priors applied: ranking only, 0 findings suppressed.[/dim]"
        )
    console.print(
        f"\n[green]submit:[/green] {counts.get('submit', 0)} "
        f"| [cyan]already_fixed:[/cyan] {counts.get('already_fixed', 0)} "
        f"| [magenta]declared:[/magenta] {counts.get('declared', 0)} "
        f"| [yellow]discussed:[/yellow] {counts.get('discussed', 0)} "
        f"| [blue]mentioned:[/blue] {counts.get('mentioned', 0)}"
    )
    console.print(
        "[dim]Check every 'submit' verdict by hand before filing upstream.[/dim]"
    )


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
    output_html: Optional[str] = typer.Option(
        None,
        "--html",
        help=(
            "Save a single self-contained HTML report whose evidence "
            "references link to in-document file:line anchors"
        ),
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
    no_trajectory: bool = typer.Option(
        False,
        "--no-trajectory",
        help="Do not auto-save the redacted audit trajectory for this run",
    ),
    trajectory_dir: Optional[str] = typer.Option(
        None,
        "--trajectory-dir",
        help="Trajectory directory (default: ~/.smartbench/trajectories)",
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
            output_html=output_html,
            rules=rule,
            languages=language,
            use_llm=use_llm,
            min_confidence=min_confidence,
            build_evidence_packs=not no_evidence,
            max_evidence_packs=max_evidence_packs,
            state_rule_paths=state_rules,
            deterministic_output=deterministic_output,
            record_trajectory=not no_trajectory,
            trajectory_dir=trajectory_dir,
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
