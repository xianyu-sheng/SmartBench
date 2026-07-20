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
            run_quick_mode(project, concern)
        else:
            run_interactive_wizard()


@app.command()
def quick(
    project: Optional[str] = typer.Option(None, "--project", "-p"),
    concern: Optional[str] = typer.Option(None, "--concern", "-c"),
):
    """Quick diagnosis — auto-detect everything, minimal prompts."""
    run_quick_mode(project, concern)


@app.command()
def diagnose(
    project: str = typer.Option(..., "--project", "-p"),
    symptoms: Optional[str] = typer.Option(None, "--symptoms", "-s"),
    performance: bool = typer.Option(False, "--perf", help="Performance profiling mode"),
):
    """Run diagnosis only (no benchmarking)."""
    run_diagnose_mode(project, symptoms, performance)


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

    project_path = resolve_project_path(project_input)
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
    fingerprint = run_phase1_detection(project_path)
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
    graph, hybrid_retriever = run_phase4_graph(project_path, fingerprint)

    if graph and len(graph.nodes) > 0:
        console.print(f"  [green]OK[/green] {graph.summary()}")
        run_diagnosis_with_graph(project_path, fingerprint, graph, api_config,
                                  user_concern, hybrid_retriever=hybrid_retriever)
    else:
        console.print("  [yellow]Could not build code graph (no source files found?)[/yellow]")
        run_fallback_analysis(project_path, fingerprint, api_config, user_concern)

    console.print("\n[bold green]Done![/bold green]")
    console.print("  Thanks for using SmartBench!\n")


# ═══════════════════════════════════════════════════════════════════════
# Quick Mode
# ═══════════════════════════════════════════════════════════════════════

def run_quick_mode(project: Optional[str] = None, concern: Optional[str] = None):
    """Minimal-interaction quick mode."""
    console.print(Panel.fit("[bold cyan]SmartBench Quick Mode[/bold cyan]", border_style="cyan"))

    if not project:
        project = Prompt.ask("Project path/URL").strip()

    project_path = resolve_project_path(project)
    if not project_path:
        console.print(f"[red]Cannot access: {project}[/red]")
        raise typer.Exit(1)

    api_config = load_api_keys_from_env()
    if not api_config:
        console.print("[yellow]No API keys in environment — some features disabled[/yellow]")

    fingerprint = run_phase1_detection(project_path)
    display_fingerprint(console, fingerprint)

    if not concern:
        concern = "analyze the project for potential issues"

    graph, hybrid_retriever = run_phase4_graph(project_path, fingerprint)
    if graph:
        run_diagnosis_with_graph(project_path, fingerprint, graph, api_config,
                                  concern, hybrid_retriever=hybrid_retriever)
    else:
        run_fallback_analysis(project_path, fingerprint, api_config, concern)

    console.print("\n[bold green]Done![/bold green]\n")


def run_diagnose_mode(project: str, symptoms: Optional[str], performance: bool):
    """Diagnosis-only mode."""
    project_path = resolve_project_path(project)
    if not project_path:
        console.print(f"[red]Cannot access: {project}[/red]")
        raise typer.Exit(1)

    api_config = load_api_keys_from_env()
    fingerprint = run_phase1_detection(project_path)
    display_fingerprint(console, fingerprint)

    category = ProblemCategory.PERFORMANCE if performance else ProblemCategory.UNKNOWN
    registry = DiagnosticRegistry()
    for tool in ALL_TOOLS:
        registry.register(tool)

    results = registry.diagnose(fingerprint.primary_language, category, str(project_path))

    console.print("\n[bold]Diagnostic Results:[/bold]")
    for r in results:
        if r.success and r.symptoms:
            console.print(f"  [green]OK[/green] {r.tool_name}: {len(r.symptoms)} findings")
            for s in r.symptoms:
                console.print(f"    - {s}")
            for sug in r.suggestions:
                console.print(f"    [cyan]tip[/cyan] {sug.get('title', '')}")
                if sug.get("command"):
                    console.print(f"      [dim]{sug['command']}[/dim]")
        elif not r.success:
            console.print(f"  [dim]--[/dim] {r.tool_name}: {r.error or 'not available'}")

    # Health check
    console.print("\n[bold]Tool Availability:[/bold]")
    health = registry.health_check(fingerprint.primary_language)
    table = Table("Tool", "Available")
    for name, available in health.items():
        table.add_row(name, "[green]yes[/green]" if available else "[red]no[/red]")
    console.print(table)


# ═══════════════════════════════════════════════════════════════════════
# Phase Implementations
# ═══════════════════════════════════════════════════════════════════════

def run_phase1_detection(project_path: str) -> ProjectFingerprint:
    """Phase 1: Deterministic project scanning (zero LLM)."""
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        task = progress.add_task("Scanning project files...", total=None)
        scanner = ProjectScanner(project_path)
        fp = scanner.scan()
        progress.remove_task(task)
    return fp


def run_phase4_graph(project_path: str, fingerprint: ProjectFingerprint,
                     build_rag: bool = True):
    """Phase 4: Build code graph — parses primary + secondary languages.

    Returns:
        (graph, hybrid_retriever) tuple. hybrid_retriever is None if RAG unavailable.
    """
    try:
        builder = CodeGraphBuilder(max_files=500)
        all_langs = [fingerprint.primary_language] + fingerprint.secondary_languages

        if len(all_langs) == 1:
            lang_label = fingerprint.primary_language.value
        else:
            lang_label = " + ".join(l.value for l in all_langs)

        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
            task = progress.add_task(f"构建代码图 ({lang_label})...", total=None)

            main_graph = builder.build(project_path, fingerprint.primary_language)

            # Also parse secondary languages and merge
            for sec_lang in fingerprint.secondary_languages:
                sec_graph = builder.build(project_path, sec_lang)
                if sec_graph and len(sec_graph.nodes) > 0:
                    main_graph = main_graph.merge(sec_graph)

            progress.remove_task(task)

        # Show language breakdown
        lang_counts = {}
        for node in main_graph.nodes.values():
            lang = node.language or "unknown"
            lang_counts[lang] = lang_counts.get(lang, 0) + 1
        if len(lang_counts) > 1:
            breakdown = ", ".join(f"{l}:{c}" for l, c in sorted(lang_counts.items()))
            console.print(f"    [dim]语言分布: {breakdown}[/dim]")

        # ── Build RAG vector index (NEW) ──────────────────────────────
        hybrid_retriever = None
        if build_rag:
            try:
                from smartbench.rag.indexer import IndexPipeline
                from smartbench.rag.retriever import HybridRetriever
                from smartbench.rag.embedder import CodeEmbedder
                from smartbench.rag.store import VectorStore

                indexer = IndexPipeline(project_path, fingerprint)

                with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
                    rag_task = progress.add_task("[yellow]构建 RAG 向量索引...[/yellow]", total=None)
                    store, rag_embedder = indexer.index_if_needed(main_graph)
                    progress.remove_task(rag_task)

                chunk_count = store.count()
                console.print(f"    [green]OK[/green] RAG 向量索引: {chunk_count} 个代码块")

                hybrid_retriever = HybridRetriever(
                    main_graph, project_path, store, rag_embedder
                )
            except ImportError as e:
                console.print(f"    [yellow]RAG 依赖未安装: {e}[/yellow]")
                console.print(
                    "    [dim]安装可选依赖: pip install smartbench[rag] 或"
                    " pip install chromadb sentence-transformers[/dim]"
                )
            except Exception as e:
                console.print(f"    [yellow]RAG 索引跳过: {e}[/yellow]")

        return main_graph, hybrid_retriever
    except Exception as e:
        console.print(f"  [yellow]代码图构建问题: {e}[/yellow]")
        return None, None


def run_diagnosis_with_graph(project_path: str, fingerprint: ProjectFingerprint,
                              graph, api_config: Optional[Dict],
                              concern: str,
                              hybrid_retriever=None,
                              enable_verify: bool = True):
    """Run the full graph-enhanced diagnosis pipeline with RAG + verification."""
    if not api_config:
        console.print("[yellow]No LLM configured — showing graph stats only[/yellow]")
        display_graph_stats(console, graph, fingerprint)
        return

    factory = PromptFactory(fingerprint)

    # Phase 3: Strategy selection
    strategies = [
        {"name": "performance_analysis", "description": "CPU, memory, I/O profiling",
         "tools": ["perf", "pprof", "flamegraph"]},
        {"name": "correctness_audit", "description": "Bug detection, edge cases, error handling",
         "tools": ["static_analysis", "test_coverage"]},
        {"name": "architecture_review", "description": "Design patterns, coupling, cohesion",
         "tools": ["dependency_analysis", "code_graph"]},
        {"name": "security_scan", "description": "Vulnerabilities, injection, secrets exposure",
         "tools": ["static_analysis", "dependency_audit"]},
    ]

    if fingerprint.hot_files:
        strategies.append({
            "name": "hotspot_analysis",
            "description": f"Focus on recently changed files: {', '.join(fingerprint.hot_files[:3])}",
            "tools": ["code_graph", "git_blame"],
        })

    strategy_prompt = factory.build_strategy_prompt(concern, strategies)
    strategy_response = call_llm(api_config, strategy_prompt)
    strategy = _parse_json_safe(strategy_response) if strategy_response else None

    if strategy:
        selected = strategy.get("selected_strategy", "auto")
        reasoning = strategy.get("reasoning", "")
        console.print(f"\n  [cyan]Strategy:[/cyan] {selected}")
        if reasoning:
            console.print(f"  [dim]{reasoning}[/dim]")

    # Hybrid context retrieval (graph + RAG)
    retriever = GraphRetriever(graph, project_path, max_tokens_estimate=4000)
    if hybrid_retriever:
        code_context = hybrid_retriever.retrieve(concern)
    else:
        code_context = retriever.retrieve(concern)

    analysis_context = factory.build_analysis_context(
        code_context=code_context,
        user_symptoms=concern,
    )

    # ── Create Verifier (NEW) ─────────────────────────────────────────
    verifier = None
    if enable_verify:
        try:
            from smartbench.verifier.verifier import Verifier
            verifier = Verifier(
                project_path=project_path,
                graph=graph,
                graph_retriever=retriever,
                hybrid_retriever=hybrid_retriever,
            )
        except ImportError as e:
            console.print(f"  [dim]验证模块未加载: {e}[/dim]")
        except Exception as e:
            console.print(f"  [yellow]验证器初始化跳过: {e}[/yellow]")

    # Phase 5: Multi-agent debate with verification
    console.print("\n[bold]多 Agent 辩论中...[/bold]\n")
    if verifier:
        console.print("  [dim]证据核查已启用[/dim]")

    # Build a single role-aware LLM caller: fn(prompt, role="proposer")
    def llm_fn(prompt: str, role: str = "") -> str:
        return call_llm(api_config, prompt, role=role) or ""

    debate_engine = DebateEngine(llm_fn, prompt_factory=factory, verifier=verifier)
    result = debate_engine.debate(analysis_context, target=concern,
                                  on_progress=_show_debate_round)

    # Show verification stats if available
    if verifier:
        try:
            stats = verifier.get_verification_stats(result.final_suggestions)
            console.print(
                f"  [dim]验证: {stats['verified']} 通过, "
                f"{stats['partial']} 部分, {stats['hallucinated']} 不存在, "
                f"总体得分: {stats['overall_score']:.0%}[/dim]"
            )
        except Exception:
            pass

    display_diagnosis_results(console, result, fingerprint, graph)


def run_fallback_analysis(project_path: str, fingerprint: ProjectFingerprint,
                           api_config: Optional[Dict], concern: str):
    """Fallback: file-based analysis when code graph can't be built."""
    if not api_config:
        console.print("[yellow]No LLM configured — cannot perform analysis[/yellow]")
        return

    factory = PromptFactory(fingerprint)

    code_context = ""
    for entry_file in fingerprint.entry_points[:3]:
        try:
            content = (Path(project_path) / entry_file).read_text(
                encoding="utf-8", errors="ignore"
            )
            code_context += f"\n// {entry_file}\n{content[:2000]}\n"
        except Exception:
            pass

    if fingerprint.has_readme:
        try:
            readme = (Path(project_path) / fingerprint.readme_path).read_text(
                encoding="utf-8", errors="ignore"
            )
            code_context = f"// {fingerprint.readme_path}\n{readme[:2000]}\n" + code_context
        except Exception:
            pass

    analysis_context = factory.build_analysis_context(
        code_context=code_context,
        user_symptoms=concern,
    )

    # Build a single role-aware LLM caller: fn(prompt, role="proposer")
    def llm_fn(prompt: str, role: str = "") -> str:
        return call_llm(api_config, prompt, role=role) or ""

    debate_engine = DebateEngine(llm_fn, prompt_factory=factory)
    result = debate_engine.debate(analysis_context, target=concern,
                                  on_progress=_show_debate_round)

    display_diagnosis_results(console, result, fingerprint, None)


# ═══════════════════════════════════════════════════════════════════════
# Display helpers
# ═══════════════════════════════════════════════════════════════════════

def display_fingerprint(console, fp: ProjectFingerprint):
    """Display project fingerprint in a table."""
    table = Table(title="Project Fingerprint (Phase 1 — zero LLM)", show_header=False)
    table.add_column("Property", style="cyan")
    table.add_column("Value")

    table.add_row("Primary Language", f"[bold]{fp.primary_language.value}[/bold] "
                  f"(confidence: {fp.language_confidence:.0%})")
    if fp.secondary_languages:
        table.add_row("Secondary", ", ".join(l.value for l in fp.secondary_languages))
    table.add_row("Framework", f"{fp.framework.value} (confidence: {fp.framework_confidence:.0%})")
    table.add_row("Project Type", fp.project_type.value)
    table.add_row("Build System", fp.build_system or "unknown")
    table.add_row("Source Files", f"{fp.source_files} (~{fp.lines_of_code_estimate:,} LOC)")
    table.add_row("Entry Points", ", ".join(fp.entry_points[:5]) or "none")
    table.add_row("Dependencies", f"{fp.dependency_count} packages")
    table.add_row("Git", f"{'yes ' + fp.git_remote_url[:50] if fp.is_git_repo else 'no'}")
    if fp.hot_files:
        table.add_row("Hot Files", ", ".join(fp.hot_files[:5]))
    table.add_row("README", f"{'yes: ' + fp.readme_path if fp.has_readme else 'no'}")

    console.print(table)


def display_project_understanding(console, understanding: Dict):
    """Display LLM's understanding of the project."""
    console.print("\n[bold cyan]LLM Analysis:[/bold cyan]")
    console.print(f"  [bold]Summary:[/bold] {understanding.get('project_summary', 'N/A')}")
    console.print(f"  [bold]Domain:[/bold] {understanding.get('primary_domain', 'N/A')}")
    concerns = understanding.get("key_concerns", [])
    if concerns:
        console.print(f"  [bold]Key Concerns:[/bold] {', '.join(concerns)}")
    console.print(f"  [bold]Suggested Focus:[/bold] {understanding.get('suggested_diagnostic_focus', 'N/A')}")


def display_diagnosis_results(console, result: DebateResult, fp: ProjectFingerprint, graph=None):
    """Display the final diagnosis report."""
    console.print(f"\n[bold]Diagnostic Report[/bold] ({result.duration_ms}ms, {result.iterations} debate rounds)")

    if not result.final_suggestions:
        console.print("  [yellow]No issues identified[/yellow]")
        if graph:
            display_graph_stats(console, graph, fp)
        return

    console.print(f"\n[bold green]{len(result.final_suggestions)} findings:[/bold green]\n")

    prio_colors = {5: "red", 4: "yellow", 3: "cyan", 2: "blue", 1: "dim"}

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
            f"[bold]{title}[/bold]\n\n{desc}\n\n[bold]Fix:[/bold] {impl}\n{loc_line}".strip(),
            title=f"#{i} [{color}]Priority {priority}[/{color}] | Risk: {risk} | Consensus: {consensus}",
            border_style=color,
        ))

    if graph:
        display_graph_stats(console, graph, fp)


def display_graph_stats(console, graph, fp: ProjectFingerprint):
    """Show code graph statistics."""
    console.print(f"\n  [dim]Code graph: {graph.summary()}[/dim]")


# ═══════════════════════════════════════════════════════════════════════
# Utility functions
# ═══════════════════════════════════════════════════════════════════════

def resolve_project_path(input_path: str) -> Optional[str]:
    """Resolve a project path or git URL to a local directory."""
    import tempfile
    import subprocess

    input_path = os.path.expanduser(input_path)

    local = Path(input_path)
    if local.exists() and local.is_dir():
        return str(local.resolve())

    # Git URL
    if input_path.startswith(("http://", "https://", "git@", "ssh://")):
        console.print("  [dim]Cloning repository...[/dim]")
        tmpdir = os.path.join(tempfile.gettempdir(), f"smartbench_{int(time.time())}")
        try:
            result = subprocess.run(
                ["git", "clone", "--depth", "1", input_path, tmpdir],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode == 0:
                return tmpdir
            console.print(f"  [red]Clone failed: {result.stderr[:200]}[/red]")
        except Exception as e:
            console.print(f"  [red]Clone error: {e}[/red]")

    return None


# ═══════════════════════════════════════════════════════════════════════
