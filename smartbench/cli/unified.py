"""
Unified Diagnostic CLI commands.

This module provides the CLI interface for the multi-language
unified diagnostic framework.
"""

from pathlib import Path
from typing import List, Optional, Tuple

from rich.console import Console
from rich.table import Table
from rich.tree import Tree

from smartbench import __version__
from smartbench.core import (
    AdapterRegistry,
    AnalysisSession,
    RuleRegistry,
    UnifiedDiagnosticConfig,
    UnifiedDiagnosticEngine,
    UnifiedDiagnosticResult,
    register_all_adapters,
    register_builtin_rules,
)
from smartbench.core.sarif import save_sarif_log
from smartbench.terminal import safe_terminal_text


def setup_engine() -> UnifiedDiagnosticEngine:
    """Set up and configure the unified diagnostic engine."""
    adapters = AdapterRegistry()
    register_all_adapters(adapters)

    rules = RuleRegistry()
    register_builtin_rules(rules)

    return UnifiedDiagnosticEngine(adapters, rules)


def print_findings_summary(
    console: Console,
    result: UnifiedDiagnosticResult,
) -> None:
    """Print a summary of findings."""
    # Warn about files that failed to parse (issue #1 — must not be silent)
    parse_errors = [e for e in result.errors if e.startswith("[") and "_frontend]" in e]
    if parse_errors:
        console.print(
            f"  ⚠️  [yellow]{len(parse_errors)} file(s) failed to parse and were skipped:[/yellow]"
        )
        for msg in parse_errors[:5]:
            console.print(f"      {msg}")
        if len(parse_errors) > 5:
            console.print(f"      … and {len(parse_errors) - 5} more (see report errors)")

    if not result.findings:
        if parse_errors:
            console.print(
                "  [yellow]No findings in files that were parsed"
                " (see parse errors above).[/yellow]"
            )
        else:
            console.print("  ✅ [green]No issues found![/green]")
        return

    # Group by severity
    findings_by_severity: dict = {}
    for f in result.findings:
        sev = f.severity.value
        findings_by_severity.setdefault(sev, []).append(f)

    # Print summary table
    table = Table("Severity", "Count", style="default")
    for severity in ["error", "warning", "info"]:
        count = len(findings_by_severity.get(severity, []))
        if count > 0:
            icon = "🔴" if severity == "error" else "🟡" if severity == "warning" else "🔵"
            color = "red" if severity == "error" else "yellow" if severity == "warning" else "blue"
            table.add_row(f"{icon} {severity.upper()}", f"[{color}]{count}[/{color}]")
    console.print(table)


def print_findings_detail(
    console: Console,
    result: UnifiedDiagnosticResult,
    max_per_rule: int = 5,
) -> None:
    """Print detailed findings."""
    if not result.findings:
        return

    # Group by rule ID
    findings_by_rule: dict = {}
    for f in result.findings:
        findings_by_rule.setdefault(f.rule_id, []).append(f)

    for rule_id in sorted(findings_by_rule.keys()):
        findings = findings_by_rule[rule_id]
        tree = Tree(f"📋 {rule_id} ({len(findings)})")
        for f in findings[:max_per_rule]:
            location_str = f"{f.location.file_path}:{f.location.line_start}"
            sev_icon = "🔴" if f.severity.value == "error" else "🟡" if f.severity.value == "warning" else "🔵"
            leaf = tree.add(f"{sev_icon} {safe_terminal_text(location_str)}")
            leaf.add(f"📝 {safe_terminal_text(f.message)}")
            if f.confidence < 1.0:
                leaf.add(f"🎯 Confidence: {f.confidence:.0%}")
        if len(findings) > max_per_rule:
            tree.add(f"... and {len(findings) - max_per_rule} more")
        console.print(tree)


def print_diagnosis_result(
    console: Console,
    result: UnifiedDiagnosticResult,
    project_path: Path,
) -> None:
    """Print the full diagnosis result."""
    console.print()
    console.print("📊 [bold]Diagnosis Results[/bold]")
    console.print("─" * 40)

    # Print project info
    console.print(f"📁 Project: {safe_terminal_text(project_path)}")
    console.print(f"⏱️  Duration: {result.duration_ms}ms")
    if result.ir:
        console.print(
            f"🧩 SemanticIR: {result.ir.schema_version} · "
            f"languages={','.join(result.ir.languages) or 'unknown'} · "
            f"evidence_packs={len(result.evidence_packs)}"
        )

    if result.errors:
        console.print()
        console.print("⚠️ [yellow]Errors encountered:[/yellow]")
        for err in result.errors[:5]:
            console.print(f"  - {safe_terminal_text(err)}")
        if len(result.errors) > 5:
            console.print(f"  ... and {len(result.errors) - 5} more")

    console.print()
    print_findings_summary(console, result)

    if result.findings:
        console.print()
        console.print("📝 [bold]Details[/bold]")
        console.print()
        print_findings_detail(console, result)


def list_rules(
    console: Console,
    include_descriptions: bool = False,
) -> None:
    """List all available diagnostic rules."""
    rules = RuleRegistry()
    register_builtin_rules(rules)

    table = Table("Rule ID", "Severity", "Description", title="Available Diagnostic Rules")
    for rule_id in sorted(rules.list_rule_ids()):
        rule = rules.get_rule(rule_id)
        if rule:
            table.add_row(
                rule_id,
                rule.severity.value,
                rule.description or "-",
            )
    console.print(table)
    console.print(f"Total: {len(rules.list_rule_ids())} rules")


def list_languages(
    console: Console,
) -> None:
    """List all supported languages."""
    adapters = AdapterRegistry()
    register_all_adapters(adapters)

    table = Table("Language", "Extensions", title="Supported Languages")
    for lang in sorted(adapters.list_languages()):
        adapter = adapters.get_adapter_for_language(lang)
        if adapter:
            table.add_row(
                lang,
                ", ".join(adapter.file_extensions),
            )
    console.print(table)
    console.print(f"Total: {len(adapters.list_languages())} languages")


def run_unified_diagnosis(
    console: Console,
    project: str,
    output: Optional[str] = None,
    output_sarif: Optional[str] = None,
    rules: Optional[List[str]] = None,
    languages: Optional[List[str]] = None,
    use_llm: bool = False,
    min_confidence: float = 0.7,
    build_evidence_packs: bool = True,
    max_evidence_packs: int = 50,
    state_rule_paths: Optional[List[str]] = None,
) -> Tuple[UnifiedDiagnosticResult, Optional[Path]]:
    """
    Run unified diagnosis.

    Args:
        console: Rich console for output
        project: Project path
        output: Optional JSON output path
        output_sarif: Optional SARIF output path
        rules: Optional rule ID filter
        languages: Optional language filter
        use_llm: Enable LLM-enhanced rules
        min_confidence: Minimum inclusive confidence to include in reports
        build_evidence_packs: Attach deterministic graph evidence to findings
        max_evidence_packs: Maximum number of finding evidence packs
        state_rule_paths: Versioned YAML state-rule files to evaluate

    Returns:
        (result, sarif_path) tuple
    """
    # Resolve project path
    project_path = Path(project).expanduser().absolute()
    if not project_path.exists():
        console.print(f"[red]Error: Path does not exist: {safe_terminal_text(project)}[/red]")
        raise SystemExit(1)

    # Setup engine
    engine = setup_engine()

    # Build config
    config = UnifiedDiagnosticConfig(
        use_llm_rules=use_llm,
        use_static_rules=True,
        rule_ids=rules,
        languages=languages,
        min_confidence=min_confidence,
        build_evidence_packs=build_evidence_packs,
        max_evidence_packs=max_evidence_packs,
        state_rule_paths=[Path(path).expanduser() for path in state_rule_paths or []],
    )

    # Run the same shared session consumed by interactive Agent review.
    console.print(f"🔍 Analyzing: {safe_terminal_text(project_path)}")
    session = AnalysisSession.analyze(
        project_path,
        config,
        engine=engine,
    )
    result = session.result

    # Print results
    print_diagnosis_result(console, result, project_path)

    # Save output if requested
    sarif_path = None
    if output_sarif:
        sarif_path = save_sarif_log(
            result.findings,
            project_path,
            Path(output_sarif),
            tool_name="SmartBench",
            tool_version=__version__,
            rule_registry=engine.rules,
        )
        console.print()
        console.print(f"💾 SARIF report saved to: [cyan]{safe_terminal_text(sarif_path)}[/cyan]")

    if output:
        import json

        output_path = Path(output).expanduser().absolute()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            payload = result.to_dict()
            payload["project_path"] = str(project_path)
            json.dump(payload, f, ensure_ascii=False, indent=2)
        console.print(f"💾 JSON report saved to: [cyan]{safe_terminal_text(output_path)}[/cyan]")

    return result, sarif_path
