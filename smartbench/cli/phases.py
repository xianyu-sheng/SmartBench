"""
Diagnosis phases — project scanning, code graph building, debate orchestration.

Orchestrates the 5-phase pipeline:
  1. Fingerprint (deterministic project detection)
  2. LLM Understanding (optional README analysis)
  3. Strategy Selection (LLM picks diagnostic focus)
  4. Code Graph + RAG Indexing
  5. Multi-Agent Debate with Verification
"""

import os
import time
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Dict, Tuple

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt
from rich.progress import Progress, SpinnerColumn, TextColumn

from smartbench.detector.scanner import ProjectScanner
from smartbench.detector.fingerprint import ProjectFingerprint
from smartbench.prompts.factory import PromptFactory
from smartbench.graph.builder import CodeGraphBuilder
from smartbench.graph.retriever import GraphRetriever
from smartbench.diagnostics.registry import DiagnosticRegistry, ProblemCategory
from smartbench.diagnostics.tools import ALL_TOOLS
from smartbench.engine.debate import DebateEngine
from smartbench.llm.client import call_llm, parse_json_safe
from smartbench.llm.provider import load_api_keys_from_env
from smartbench.cli.display import (
    show_debate_round,
    display_fingerprint,
    display_graph_stats,
    display_diagnosis_results,
)


def resolve_project_path(console: Console, input_path: str) -> Optional[str]:
    """Resolve a project path or git URL to a local directory.

    Args:
        console: Rich Console instance for progress output.
        input_path: Local path or git URL.

    Returns:
        Absolute path to the project directory, or None if unresolvable.
    """
    input_path = os.path.expanduser(input_path)

    local = Path(input_path)
    if local.exists() and local.is_dir():
        return str(local.resolve())

    # Git URL
    if input_path.startswith(("http://", "https://", "git@", "ssh://")):
        console.print("  [dim]Cloning repository...[/dim]")
        tmpdir = os.path.join(
            tempfile.gettempdir(), f"smartbench_{int(time.time())}"
        )
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
# Phase 1: Project Fingerprinting
# ═══════════════════════════════════════════════════════════════════════

def run_phase1_detection(
    console: Console, project_path: str
) -> ProjectFingerprint:
    """Phase 1: Deterministic project scanning (zero LLM)."""
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Scanning project files...", total=None)
        scanner = ProjectScanner(project_path)
        fp = scanner.scan()
        progress.remove_task(task)
    return fp


# ═══════════════════════════════════════════════════════════════════════
# Phase 4: Code Graph + RAG
# ═══════════════════════════════════════════════════════════════════════

def run_phase4_graph(
    console: Console,
    project_path: str,
    fingerprint: ProjectFingerprint,
    build_rag: bool = True,
) -> Tuple[Optional[object], Optional[object]]:
    """Phase 4: Build code graph — parses primary + secondary languages.

    Returns:
        (graph, hybrid_retriever) tuple. hybrid_retriever is None if
        RAG unavailable.
    """
    try:
        builder = CodeGraphBuilder(max_files=500)
        all_langs = [fingerprint.primary_language] + fingerprint.secondary_languages

        if len(all_langs) == 1:
            lang_label = fingerprint.primary_language.value
        else:
            lang_label = " + ".join(lang.value for lang in all_langs)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task(
                f"构建代码图 ({lang_label})...", total=None
            )
            main_graph = builder.build(project_path, fingerprint.primary_language)

            for sec_lang in fingerprint.secondary_languages:
                sec_graph = builder.build(project_path, sec_lang)
                if sec_graph and len(sec_graph.nodes) > 0:
                    main_graph = main_graph.merge(sec_graph)

            progress.remove_task(task)

        # Language breakdown
        lang_counts: Dict[str, int] = {}
        for node in main_graph.nodes.values():
            lang = node.language or "unknown"
            lang_counts[lang] = lang_counts.get(lang, 0) + 1
        if len(lang_counts) > 1:
            breakdown = ", ".join(
                f"{lang}:{count}" for lang, count in sorted(lang_counts.items())
            )
            console.print(f"    [dim]语言分布: {breakdown}[/dim]")

        # RAG vector index
        hybrid_retriever = None
        if build_rag:
            hybrid_retriever = _build_rag_index(
                console, project_path, fingerprint, main_graph
            )

        return main_graph, hybrid_retriever
    except Exception as e:
        console.print(f"  [yellow]代码图构建问题: {e}[/yellow]")
        return None, None


def _build_rag_index(
    console: Console,
    project_path: str,
    fingerprint: ProjectFingerprint,
    graph: object,
) -> Optional[object]:
    """Build RAG vector index. Returns HybridRetriever or None."""
    try:
        from smartbench.rag.indexer import IndexPipeline
        from smartbench.rag.retriever import HybridRetriever

        indexer = IndexPipeline(project_path, fingerprint)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            rag_task = progress.add_task(
                "[yellow]构建 RAG 向量索引...[/yellow]", total=None
            )
            store, rag_embedder = indexer.index_if_needed(graph)
            progress.remove_task(rag_task)

        chunk_count = store.count()
        console.print(
            f"    [green]OK[/green] RAG 向量索引: {chunk_count} 个代码块"
        )
        return HybridRetriever(graph, project_path, store, rag_embedder)
    except ImportError as e:
        console.print(f"    [yellow]RAG 依赖未安装: {e}[/yellow]")
        console.print(
            "    [dim]安装可选依赖: pip install smartbench[rag] 或"
            " pip install chromadb sentence-transformers[/dim]"
        )
    except Exception as e:
        console.print(f"    [yellow]RAG 索引跳过: {e}[/yellow]")
    return None


# ═══════════════════════════════════════════════════════════════════════
# Phase 5: Diagnosis with Graph + Debate
# ═══════════════════════════════════════════════════════════════════════

def run_diagnosis_with_graph(
    console: Console,
    project_path: str,
    fingerprint: ProjectFingerprint,
    graph: object,
    api_config: Optional[Dict],
    concern: str,
    hybrid_retriever: object = None,
    enable_verify: bool = True,
) -> Optional[object]:
    """Run the full graph-enhanced diagnosis pipeline with RAG + verification.

    Returns:
        DebateResult or None.
    """
    if not api_config:
        console.print(
            "[yellow]No LLM configured — showing graph stats only[/yellow]"
        )
        display_graph_stats(console, graph, fingerprint)
        return None

    factory = PromptFactory(fingerprint)

    # Phase 3: Strategy selection
    strategies = [
        {
            "name": "performance_analysis",
            "description": "CPU, memory, I/O profiling",
            "tools": ["perf", "pprof", "flamegraph"],
        },
        {
            "name": "correctness_audit",
            "description": "Bug detection, edge cases, error handling",
            "tools": ["static_analysis", "test_coverage"],
        },
        {
            "name": "architecture_review",
            "description": "Design patterns, coupling, cohesion",
            "tools": ["dependency_analysis", "code_graph"],
        },
        {
            "name": "security_scan",
            "description": "Vulnerabilities, injection, secrets exposure",
            "tools": ["static_analysis", "dependency_audit"],
        },
    ]

    if fingerprint.hot_files:
        strategies.append({
            "name": "hotspot_analysis",
            "description": (
                f"Focus on recently changed files: "
                f"{', '.join(fingerprint.hot_files[:3])}"
            ),
            "tools": ["code_graph", "git_blame"],
        })

    strategy_prompt = factory.build_strategy_prompt(concern, strategies)
    strategy_response = call_llm(api_config, strategy_prompt)
    strategy = parse_json_safe(strategy_response) if strategy_response else None

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

    # ── Execute diagnostic tools ──────────────────────────────────
    tool_context = ""
    if selected and selected != "auto":
        try:
            from smartbench.diagnostics.executor import run_tools_for_strategy
            tool_context = run_tools_for_strategy(
                console, project_path, fingerprint.primary_language, selected
            )
            if tool_context:
                console.print("  [dim]诊断工具已执行[/dim]")
        except Exception as e:
            console.print(f"  [dim]工具执行跳过: {e}[/dim]")

    analysis_context = factory.build_analysis_context(
        code_context=code_context + tool_context,
        user_symptoms=concern,
    )

    # Verifier
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

    # Phase 5: Multi-agent debate
    console.print("\n[bold]多 Agent 辩论中...[/bold]\n")
    if verifier:
        console.print("  [dim]证据核查已启用[/dim]")

    def llm_fn(prompt: str, role: str = "") -> str:
        return call_llm(api_config, prompt, role=role) or ""

    debate_engine = DebateEngine(llm_fn, prompt_factory=factory, verifier=verifier)

    def on_progress(role: str, parsed_json, raw_text: str) -> None:
        show_debate_round(console, role, parsed_json, raw_text)

    result = debate_engine.debate(
        analysis_context, target=concern, on_progress=on_progress,
    )

    # Verification stats
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
    return result


def run_fallback_analysis(
    console: Console,
    project_path: str,
    fingerprint: ProjectFingerprint,
    api_config: Optional[Dict],
    concern: str,
) -> Optional[object]:
    """Fallback: file-based analysis when code graph can't be built.

    Returns:
        DebateResult or None.
    """
    if not api_config:
        console.print(
            "[yellow]No LLM configured — cannot perform analysis[/yellow]"
        )
        return

    factory = PromptFactory(fingerprint)

    code_context = ""
    for entry_file in fingerprint.entry_points[:3]:
        try:
            content = (
                (Path(project_path) / entry_file)
                .read_text(encoding="utf-8", errors="ignore")
            )
            code_context += f"\n// {entry_file}\n{content[:2000]}\n"
        except Exception:
            pass

    if fingerprint.has_readme:
        try:
            readme = (
                (Path(project_path) / fingerprint.readme_path)
                .read_text(encoding="utf-8", errors="ignore")
            )
            code_context = (
                f"// {fingerprint.readme_path}\n{readme[:2000]}\n"
                + code_context
            )
        except Exception:
            pass

    analysis_context = factory.build_analysis_context(
        code_context=code_context,
        user_symptoms=concern,
    )

    def llm_fn(prompt: str, role: str = "") -> str:
        return call_llm(api_config, prompt, role=role) or ""

    debate_engine = DebateEngine(llm_fn, prompt_factory=factory)
    result = debate_engine.debate(analysis_context, target=concern)

    display_diagnosis_results(console, result, fingerprint, None)
    return result


# ═══════════════════════════════════════════════════════════════════════
# Entry Modes
# ═══════════════════════════════════════════════════════════════════════

def run_quick_mode(
    console: Console,
    project: Optional[str] = None,
    concern: Optional[str] = None,
) -> Optional[object]:
    """Minimal-interaction quick mode — auto-detect everything.

    Returns:
        DebateResult if diagnosis completed, None otherwise.
    """
    console.print(Panel.fit(
        "[bold cyan]SmartBench Quick Mode[/bold cyan]", border_style="cyan"
    ))

    if not project:
        project = Prompt.ask("Project path/URL").strip()

    project_path = resolve_project_path(console, project)
    if not project_path:
        console.print(f"[red]Cannot access: {project}[/red]")
        return

    api_config = load_api_keys_from_env()
    if not api_config:
        console.print(
            "[yellow]No API keys in environment — some features disabled[/yellow]"
        )

    fingerprint = run_phase1_detection(console, project_path)
    display_fingerprint(console, fingerprint)

    if not concern:
        concern = "analyze the project for potential issues"

    graph, hybrid_retriever = run_phase4_graph(console, project_path, fingerprint)
    if graph:
        result = run_diagnosis_with_graph(
            console, project_path, fingerprint, graph, api_config,
            concern, hybrid_retriever=hybrid_retriever,
        )
    else:
        result = run_fallback_analysis(
            console, project_path, fingerprint, api_config, concern,
        )

    console.print("\n[bold green]Done![/bold green]\n")
    return result


def run_diagnose_mode(
    console: Console,
    project: str,
    symptoms: Optional[str],
    performance: bool,
) -> None:
    """Diagnosis-only mode — runs tools, shows results."""
    project_path = resolve_project_path(console, project)
    if not project_path:
        console.print(f"[red]Cannot access: {project}[/red]")
        return

    load_api_keys_from_env()  # noqa: F841
    fingerprint = run_phase1_detection(console, project_path)
    display_fingerprint(console, fingerprint)

    category = (
        ProblemCategory.PERFORMANCE if performance else ProblemCategory.UNKNOWN
    )
    registry = DiagnosticRegistry()
    for tool in ALL_TOOLS:
        registry.register(tool)

    results = registry.diagnose(
        fingerprint.primary_language, category, str(project_path)
    )

    console.print("\n[bold]Diagnostic Results:[/bold]")
    for r in results:
        if r.success and r.symptoms:
            console.print(
                f"  [green]OK[/green] {r.tool_name}: "
                f"{len(r.symptoms)} findings"
            )
            for s in r.symptoms:
                console.print(f"    - {s}")
            for sug in r.suggestions:
                console.print(f"    [cyan]tip[/cyan] {sug.get('title', '')}")
                if sug.get("command"):
                    console.print(f"      [dim]{sug['command']}[/dim]")
        elif not r.success:
            console.print(
                f"  [dim]--[/dim] {r.tool_name}: "
                f"{r.error or 'not available'}"
            )

    # Health check
    console.print("\n[bold]Tool Availability:[/bold]")
    health = registry.health_check(fingerprint.primary_language)
    table = Table("Tool", "Available")
    for name, available in health.items():
        table.add_row(
            name, "[green]yes[/green]" if available else "[red]no[/red]"
        )
    console.print(table)
