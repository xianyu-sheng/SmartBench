"""
SmartBench — AI-powered universal code diagnosis tool.

Analyzes any codebase with multi-agent debate engine + code graph + pluggable diagnostics.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    # Single source of truth is pyproject.toml; read it from installed metadata
    # so the version can never drift between the two (issue #3).
    __version__ = _pkg_version("smartbench")
except PackageNotFoundError:  # source tree without an install
    __version__ = "0.0.0.dev0"

__author__ = "xianyu-sheng"

__all__ = ["__author__", "__version__"]
