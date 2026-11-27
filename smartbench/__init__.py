"""
SmartBench — AI-powered universal code diagnosis tool.

Analyzes any codebase with multi-agent debate engine + code graph + pluggable diagnostics.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path
from typing import Optional

from smartbench.core import (
    AnalysisSession,
    UnifiedDiagnosticConfig,
    UnifiedDiagnosticEngine,
)

try:
    # Single source of truth is pyproject.toml; read it from installed metadata
    # so the version can never drift between the two (issue #3).
    __version__ = _pkg_version("smartbench")
except PackageNotFoundError:  # source tree without an install
    __version__ = "0.0.0.dev0"

__author__ = "xianyu-sheng"


def analyze(
    project_path: Path | str,
    config: Optional[UnifiedDiagnosticConfig] = None,
    *,
    engine: Optional[UnifiedDiagnosticEngine] = None,
) -> AnalysisSession:
    """Analyze a repository with the shared deterministic session."""
    return AnalysisSession.analyze(project_path, config, engine=engine)


__all__ = [
    "AnalysisSession",
    "UnifiedDiagnosticConfig",
    "UnifiedDiagnosticEngine",
    "__author__",
    "__version__",
    "analyze",
]
