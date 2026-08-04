"""
Diagnosis phases — project scanning, code graph building, debate orchestration.

Orchestrates the 5-phase pipeline:
  1. Fingerprint (deterministic project detection)
  2. LLM Understanding (optional README analysis)
  3. Strategy Selection (LLM picks diagnostic focus)
  4. Code Graph + RAG Indexing
  5. Multi-Agent Debate with Verification
"""

import atexit
import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import Dict, Optional, Tuple

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Prompt
from rich.table import Table

from smartbench.cli.display import (
    display_diagnosis_results,
    display_fingerprint,
    display_graph_stats,
    show_debate_round,
)
from smartbench.core import AnalysisSession, UnifiedDiagnosticConfig
from smartbench.detector.fingerprint import ProjectFingerprint
from smartbench.detector.scanner import ProjectScanner
from smartbench.diagnostics.registry import (
    DiagnosticRegistry,
    ProblemCategory,
    infer_problem_category,
)
from smartbench.diagnostics.tools import ALL_TOOLS
from smartbench.engine.debate import DebateEngine, EvidencePolicy
from smartbench.graph.builder import CodeGraphBuilder
from smartbench.graph.evidence import DeterministicGraphRAG
from smartbench.graph.retriever import GraphRetriever
from smartbench.ir import (
    EvidencePack,
    EvidenceRef,
    FactKind,
    SemanticFact,
    SemanticIR,
)
from smartbench.llm.client import call_llm, parse_json_safe
from smartbench.llm.provider import load_api_keys_from_env
from smartbench.path_safety import read_text_prefix, resolve_project_file
from smartbench.prompts.factory import PromptFactory
from smartbench.subprocess_utils import run_bounded
from smartbench.terminal import safe_terminal_text


def _fallback_strategy(concern: str) -> str:
    """Choose a safe deterministic strategy when model routing is unavailable."""
    lowered = concern.lower()
    if any(word in lowered for word in ("architect", "design", "架构", "设计")):
        return "architecture_review"
    category = infer_problem_category([concern])
    if category == ProblemCategory.SECURITY:
        return "security_scan"
    if category in {
        ProblemCategory.PERFORMANCE,
        ProblemCategory.MEMORY_LEAK,
        ProblemCategory.DEADLOCK,
        ProblemCategory.CONCURRENCY,
    }:
        return "performance_analysis"
    return "correctness_audit"


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
        tmpdir = tempfile.mkdtemp(prefix="smartbench_clone_")
        try:
            result = run_bounded(
                ["git", "clone", "--depth", "1", input_path, tmpdir],
                timeout=120,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
            if result.returncode == 0:
                atexit.register(shutil.rmtree, tmpdir, ignore_errors=True)
                return tmpdir
            console.print(
                f"  [red]Clone failed (git exit {result.returncode})[/red]"
            )
        except Exception as e:
            console.print(f"  [red]Clone error: {type(e).__name__}[/red]")
        shutil.rmtree(tmpdir, ignore_errors=True)

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


def run_analysis_session(
    console: Console,
    project_path: str,
    *,
    build_evidence_packs: bool = False,
) -> AnalysisSession:
    """Build the shared full SemanticIR and deterministic result once."""
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task(
            "构建统一分析会话（扫描 → SemanticIR → Linker → 规则）...",
            total=None,
        )
        session = AnalysisSession.analyze(
            project_path,
            UnifiedDiagnosticConfig(
                build_evidence_packs=build_evidence_packs,
            ),
        )
        progress.remove_task(task)
    if session.ir is not None:
        console.print(
            "  [green]OK[/green] AnalysisSession: "
            f"{len(session.ir.source_units)} files, "
            f"{len(session.ir.operations)} operations, "
            f"{len(session.ir.operation_edges)} semantic edges, "
            f"{len(session.result.findings)} deterministic findings"
        )
    return session


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
            console.print(
                f"    [dim]语言分布: {safe_terminal_text(breakdown)}[/dim]"
            )

        # RAG vector index
        hybrid_retriever = None
        if build_rag:
            hybrid_retriever = _build_rag_index(
                console, project_path, fingerprint, main_graph
            )

        return main_graph, hybrid_retriever
    except Exception as e:
        console.print(
            f"  [yellow]代码图构建问题: {safe_terminal_text(e)}[/yellow]"
        )
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
        console.print(
            f"    [yellow]RAG 依赖未安装: {safe_terminal_text(e)}[/yellow]"
        )
        console.print(
            "    [dim]安装可选依赖: pip install smartbench[rag] 或"
            " pip install chromadb sentence-transformers[/dim]"
        )
    except Exception as e:
        console.print(
            f"    [yellow]RAG 索引跳过: {safe_terminal_text(e)}[/yellow]"
        )
    return None


def build_session_retrieval(
    console: Console,
    session: AnalysisSession,
    *,
    build_rag: bool = True,
) -> Tuple[Optional[SemanticIR], Optional[object]]:
    """Expose structural/vector retrieval over the session's complete IR."""
    semantic_ir = session.ir
    fingerprint = session.fingerprint
    if semantic_ir is None or fingerprint is None:
        return None, None
    hybrid = None
    if build_rag:
        hybrid = _build_rag_index(
            console,
            str(session.project_path),
            fingerprint,
            semantic_ir,
        )
    return semantic_ir, hybrid


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
    enable_sandbox: bool = False,
    analysis_session: Optional[AnalysisSession] = None,
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

    def llm_fn(
        prompt: str, role: str = "", timeout_seconds: float = 120
    ) -> str:
        return call_llm(
            api_config,
            prompt,
            role=role,
            timeout_seconds=timeout_seconds,
        ) or ""

    if analysis_session is not None:
        console.print("\n[bold]ProjectReader 读取项目语义...[/bold]")
        project_stage = analysis_session.run_project_reader(
            llm_fn,
            max_repairs=1,
        )
        supported = (
            len(project_stage.validation.protocols)
            if project_stage.validation is not None
            else 0
        )
        console.print(
            "  [dim]"
            f"status={project_stage.status}, "
            f"protocols={supported}, "
            f"findings={len(project_stage.findings)}, "
            f"repairs={project_stage.repair_attempts}"
            "[/dim]"
        )

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

    selected = _fallback_strategy(concern)
    strategy_prompt = factory.build_strategy_prompt(concern, strategies)
    strategy_response = call_llm(api_config, strategy_prompt)
    strategy = parse_json_safe(strategy_response) if strategy_response else None

    if strategy:
        candidate = strategy.get("selected_strategy", "")
        if not isinstance(candidate, str):
            candidate = ""
        valid_strategies = {item["name"] for item in strategies}
        if candidate in valid_strategies:
            selected = candidate
        elif candidate:
            console.print(
                f"  [dim]未知策略 {safe_terminal_text(repr(candidate))}，"
                f"使用确定性回退 {safe_terminal_text(selected)}[/dim]"
            )
        reasoning = strategy.get("reasoning", "")
        console.print(
            f"\n  [cyan]Strategy:[/cyan] {safe_terminal_text(selected)}"
        )
        if reasoning:
            console.print(f"  [dim]{safe_terminal_text(reasoning)}[/dim]")
    else:
        console.print(
            f"\n  [cyan]Strategy:[/cyan] {safe_terminal_text(selected)} "
            "[dim](deterministic fallback)[/dim]"
        )

    # Hybrid context retrieval (graph + RAG)
    retriever = GraphRetriever(graph, project_path, max_tokens_estimate=4000)
    if hybrid_retriever:
        code_context = hybrid_retriever.retrieve(concern)
    else:
        code_context = retriever.retrieve(concern)

    # Build the versioned semantic boundary once and hand the same
    # deterministic evidence pack to both the prompt and the debate engine.
    # The legacy graph context remains for compatibility with existing prompt
    # templates; the pack is the auditable factual boundary.
    if analysis_session is not None and analysis_session.ir is not None:
        semantic_ir = analysis_session.ir
        evidence_pack = analysis_session.build_evidence_pack(
            concern,
            hops=2,
            max_nodes=16,
        )
    else:
        semantic_ir = SemanticIR.from_graph(
            graph,
            project_path=str(Path(project_path).resolve()),
        )
        evidence_pack = DeterministicGraphRAG(semantic_ir).retrieve(
            concern,
            hops=2,
            max_nodes=12,
        )
    evidence_rag = DeterministicGraphRAG(semantic_ir)
    code_context = code_context + "\n\n" + evidence_rag.render(evidence_pack)

    # ── Execute diagnostic tools ──────────────────────────────────
    tool_context = ""
    if selected and selected != "auto":
        try:
            from smartbench.diagnostics.executor import run_tools_for_strategy
            tool_context = run_tools_for_strategy(
                console,
                project_path,
                fingerprint.primary_language,
                selected,
                symptoms=[concern],
                additional_languages=fingerprint.secondary_languages,
            )
            if tool_context:
                console.print("  [dim]诊断工具已执行[/dim]")
        except Exception as e:
            console.print(f"  [dim]工具执行跳过: {safe_terminal_text(e)}[/dim]")

    analysis_context = factory.build_analysis_context(
        code_context=code_context + tool_context,
        user_symptoms=concern,
    )

    # Verifier
    verifier = None
    if enable_verify:
        try:
            from smartbench.verifier.verifier import Verifier
            type_evidence = getattr(semantic_ir, "type_evidence", None)
            verifier = Verifier(
                project_path=project_path,
                graph=semantic_ir,
                graph_retriever=retriever,
                hybrid_retriever=hybrid_retriever,
                type_evidence=type_evidence,
            )
        except ImportError as e:
            console.print(f"  [dim]验证模块未加载: {safe_terminal_text(e)}[/dim]")
        except Exception as e:
            console.print(
                f"  [yellow]验证器初始化跳过: {safe_terminal_text(e)}[/yellow]"
            )

    # Phase 5: Multi-agent debate
    console.print("\n[bold]多 Agent 辩论中...[/bold]\n")
    if verifier:
        console.print("  [dim]证据核查已启用[/dim]")

    debate_engine = DebateEngine(
        llm_fn,
        prompt_factory=factory,
        verifier=verifier,
        evidence_policy=EvidencePolicy.EXCLUSIVE,
    )

    def on_progress(role: str, parsed_json, raw_text: str) -> None:
        show_debate_round(console, role, parsed_json, raw_text)

    result = debate_engine.debate(
        analysis_context, target=concern, on_progress=on_progress,
        strategy=selected,
        evidence_pack=evidence_pack,
    )
    if analysis_session is not None:
        result.analysis_report = analysis_session.report_dict()

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

    # ── Patch verification (Level 3, explicit opt-in) ─────────────
    if enable_sandbox and result.final_suggestions:
        try:
            from collections import Counter

            from smartbench.verifier.sandbox import SandboxVerifier

            console.print(
                "  [yellow]Running repository tests for proposed patches in a "
                "temporary copy (not an OS security sandbox)...[/yellow]"
            )
            sv = SandboxVerifier(project_path, timeout_seconds=30)
            sandboxed = sv.verify_all_proposals(result.final_suggestions)
            result.final_suggestions = sandboxed
            statuses = Counter(
                s.get("__sandbox_verification", {}).get("status", "skipped")
                for s in sandboxed if isinstance(s, dict)
            )
            console.print(
                "  [dim]补丁验证: "
                f"{statuses['passed']} 通过, {statuses['failed']} 失败, "
                f"{statuses['baseline_failed']} 基线失败, "
                f"{statuses['timeout']} 超时, {statuses['error']} 错误, "
                f"{statuses['skipped']} 跳过[/dim]"
            )
        except Exception as exc:
            console.print(
                f"  [yellow]补丁验证未完成: {safe_terminal_text(exc)}[/yellow]"
            )

    return result


def run_diagnosis_with_session(
    console: Console,
    session: AnalysisSession,
    api_config: Optional[Dict],
    concern: str,
    *,
    enable_verify: bool = True,
    enable_sandbox: bool = False,
) -> Optional[object]:
    """Run retrieval and Agent review over an existing full analysis session."""
    fingerprint = session.fingerprint
    semantic_ir = session.ir
    if fingerprint is None:
        console.print("[red]Project fingerprint is unavailable[/red]")
        return None
    if semantic_ir is None or not semantic_ir.nodes:
        console.print(
            "  [yellow]完整 SemanticIR 不可用，退回有界源码分析[/yellow]"
        )
        return run_fallback_analysis(
            console,
            str(session.project_path),
            fingerprint,
            api_config,
            concern,
        )

    if not api_config:
        from smartbench.cli.unified import print_diagnosis_result

        console.print(
            "[yellow]No LLM configured — returning deterministic session result[/yellow]"
        )
        print_diagnosis_result(console, session.result, session.project_path)
        return session.result

    _, hybrid_retriever = build_session_retrieval(
        console,
        session,
        build_rag=True,
    )
    return run_diagnosis_with_graph(
        console,
        str(session.project_path),
        fingerprint,
        semantic_ir,
        api_config,
        concern,
        hybrid_retriever=hybrid_retriever,
        enable_verify=enable_verify,
        enable_sandbox=enable_sandbox,
        analysis_session=session,
    )


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
    fallback_facts: list[SemanticFact] = []
    for entry_file in fingerprint.entry_points[:3]:
        try:
            entry_path = resolve_project_file(project_path, entry_file)
            if entry_path is None:
                continue
            content = read_text_prefix(entry_path, 64 * 1024)
            if content is None:
                continue
            excerpt = content[:2000]
            code_context += f"\n// {entry_file}\n{excerpt}\n"
            fallback_facts.append(
                SemanticFact(
                    subject=entry_file,
                    predicate=FactKind.SOURCE,
                    object="bounded fallback source",
                    evidence=(EvidenceRef(
                        file_path=entry_file,
                        line_start=1,
                        line_end=max(1, len(excerpt.splitlines())),
                        snippet=excerpt,
                        source="fallback_source",
                    ),),
                )
            )
        except Exception:
            pass

    if fingerprint.has_readme:
        try:
            readme_path = resolve_project_file(
                project_path, fingerprint.readme_path
            )
            if readme_path is None:
                raise OSError("README escaped project boundary")
            readme = read_text_prefix(readme_path, 64 * 1024)
            if readme is None:
                raise OSError("README could not be read safely")
            readme_excerpt = readme[:2000]
            code_context = (
                f"// {fingerprint.readme_path}\n{readme_excerpt}\n"
                + code_context
            )
            fallback_facts.append(
                SemanticFact(
                    subject=fingerprint.readme_path,
                    predicate=FactKind.SOURCE,
                    object="bounded fallback documentation",
                    evidence=(EvidenceRef(
                        file_path=fingerprint.readme_path,
                        line_start=1,
                        line_end=max(1, len(readme_excerpt.splitlines())),
                        snippet=readme_excerpt,
                        source="fallback_source",
                    ),),
                )
            )
        except Exception:
            pass

    analysis_context = factory.build_analysis_context(
        code_context=code_context,
        user_symptoms=concern,
    )

    def llm_fn(
        prompt: str, role: str = "", timeout_seconds: float = 120
    ) -> str:
        return call_llm(
            api_config,
            prompt,
            role=role,
            timeout_seconds=timeout_seconds,
        ) or ""

    version_material = "|".join(fact.fact_id for fact in fallback_facts)
    evidence_pack = EvidencePack.from_facts(
        concern,
        fallback_facts,
        retrieval_trace=("fallback:bounded-source",),
        graph_version=hashlib.sha256(version_material.encode("utf-8")).hexdigest()[:16],
    )
    debate_engine = DebateEngine(
        llm_fn,
        prompt_factory=factory,
        evidence_policy=EvidencePolicy.EXCLUSIVE,
    )
    result = debate_engine.debate(
        analysis_context,
        target=concern,
        evidence_pack=evidence_pack,
    )

    display_diagnosis_results(console, result, fingerprint, None)
    return result


# ═══════════════════════════════════════════════════════════════════════
# Entry Modes
# ═══════════════════════════════════════════════════════════════════════

def run_quick_mode(
    console: Console,
    project: Optional[str] = None,
    concern: Optional[str] = None,
    enable_sandbox: bool = False,
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
        console.print(f"[red]Cannot access: {safe_terminal_text(project)}[/red]")
        raise typer.Exit(1)

    api_config = load_api_keys_from_env()
    if not api_config:
        console.print(
            "[yellow]No API keys in environment — some features disabled[/yellow]"
        )

    session = run_analysis_session(console, project_path)
    fingerprint = session.fingerprint
    if fingerprint is None:
        console.print("[red]Could not fingerprint the project[/red]")
        return None
    display_fingerprint(console, fingerprint)

    if not concern:
        concern = "analyze the project for potential issues"

    result = run_diagnosis_with_session(
        console,
        session,
        api_config,
        concern,
        enable_sandbox=enable_sandbox,
    )

    console.print("\n[bold green]Done![/bold green]\n")
    return result


def run_diagnose_mode(
    console: Console,
    project: str,
    symptoms: Optional[str],
    performance: bool,
    system_probes: bool = False,
) -> Optional[Dict]:
    """Diagnosis-only mode — runs tools, shows results.

    Returns:
        Dict with tool results for --output capture.
    """
    project_path = resolve_project_path(console, project)
    if not project_path:
        console.print(f"[red]Cannot access: {safe_terminal_text(project)}[/red]")
        raise typer.Exit(1)

    fingerprint = run_phase1_detection(console, project_path)
    display_fingerprint(console, fingerprint)

    category = infer_problem_category(
        [symptoms] if symptoms else None,
        performance=performance,
    )
    registry = DiagnosticRegistry()
    for tool in ALL_TOOLS:
        registry.register(tool)

    results = registry.diagnose(
        fingerprint.primary_language,
        category,
        str(project_path),
        symptoms=[symptoms] if symptoms else None,
        include_system=system_probes,
        additional_languages=fingerprint.secondary_languages,
    )

    console.print("\n[bold]Diagnostic Results:[/bold]")
    for r in results:
        if r.success:
            console.print(
                f"  [green]OK[/green] {safe_terminal_text(r.tool_name)}: "
                f"{len(r.symptoms)} findings"
            )
            for s in r.symptoms:
                console.print(f"    - {safe_terminal_text(s)}")
            for sug in r.suggestions:
                console.print(
                    f"    [cyan]tip[/cyan] "
                    f"{safe_terminal_text(sug.get('title', ''))}"
                )
                if sug.get("command"):
                    console.print(
                        f"      [dim]{safe_terminal_text(sug['command'])}[/dim]"
                    )
        elif not r.success:
            console.print(
                f"  [dim]--[/dim] {safe_terminal_text(r.tool_name)}: "
                f"{safe_terminal_text(r.error or 'not available')}"
            )

    # Health check
    console.print("\n[bold]Tool Availability:[/bold]")
    health = registry.health_check(
        fingerprint.primary_language,
        fingerprint.secondary_languages,
    )
    table = Table("Tool", "Available")
    for name, available in health.items():
        table.add_row(
            safe_terminal_text(name),
            "[green]yes[/green]" if available else "[red]no[/red]",
        )
    console.print(table)
    return {"tool_results": [r.to_dict() for r in results]}
