"""CLI package — commands, display helpers, and interactive wizard."""

from smartbench.cli.main import app
from smartbench.cli.display import (
    show_debate_round,
    display_fingerprint,
    display_project_understanding,
    display_diagnosis_results,
    display_graph_stats,
)

__all__ = [
    "app",
    "show_debate_round",
    "display_fingerprint",
    "display_project_understanding",
    "display_diagnosis_results",
    "display_graph_stats",
]
