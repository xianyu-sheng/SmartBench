"""CLI package — commands, display helpers, phases, and interactive wizard."""

from smartbench.cli.display import (
    display_diagnosis_results,
    display_fingerprint,
    display_graph_stats,
    display_project_understanding,
    show_debate_round,
)
from smartbench.cli.main import app
from smartbench.cli.phases import (
    resolve_project_path,
    run_diagnose_mode,
    run_diagnosis_with_graph,
    run_fallback_analysis,
    run_phase1_detection,
    run_phase4_graph,
    run_quick_mode,
)
from smartbench.cli.wizard import run_interactive_wizard

__all__ = [
    "app",
    "show_debate_round",
    "display_fingerprint",
    "display_project_understanding",
    "display_diagnosis_results",
    "display_graph_stats",
    "resolve_project_path",
    "run_phase1_detection",
    "run_phase4_graph",
    "run_diagnosis_with_graph",
    "run_fallback_analysis",
    "run_quick_mode",
    "run_diagnose_mode",
    "run_interactive_wizard",
]
