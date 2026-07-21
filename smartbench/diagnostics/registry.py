"""
DiagnosticRegistry — pluggable diagnostic tool management.

Tools register themselves with applicable languages and problem types.
The registry routes a diagnosis request to the right tools.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional
import subprocess
import shutil

from smartbench.detector.fingerprint import Language


class ProblemCategory(Enum):
    """High-level problem categories."""
    CRASH = "crash"
    DEADLOCK = "deadlock"
    MEMORY_LEAK = "memory_leak"
    PERFORMANCE = "performance"
    STARTUP_FAILURE = "startup_failure"
    CONCURRENCY = "concurrency"
    SECURITY = "security"
    CODE_QUALITY = "code_quality"
    DEPENDENCY = "dependency"
    CONFIGURATION = "configuration"
    UNKNOWN = "unknown"


class Severity(Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class DiagnosisResult:
    """Result from a single diagnostic tool run."""
    tool_name: str
    problem_category: ProblemCategory
    severity: Severity = Severity.MEDIUM
    symptoms: List[str] = field(default_factory=list)
    root_causes: List[str] = field(default_factory=list)
    evidence: str = ""                    # raw tool output
    suggestions: List[Dict] = field(default_factory=list)
    confidence: float = 0.5
    commands_used: List[str] = field(default_factory=list)
    success: bool = True
    error: str = ""

    def to_dict(self) -> Dict:
        return {
            "tool_name": self.tool_name,
            "problem_category": self.problem_category.value,
            "severity": self.severity.value,
            "symptoms": self.symptoms,
            "root_causes": self.root_causes,
            "evidence": self.evidence[:2000],
            "suggestions": self.suggestions,
            "confidence": self.confidence,
            "commands_used": self.commands_used,
            "success": self.success,
            "error": self.error,
        }


class DiagnosticTool(ABC):
    """
    Abstract base for a diagnostic tool.

    Each tool declares:
    - Which languages it supports
    - Which problem categories it handles
    - Whether the tool binary is available on this system
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique tool name."""
        ...

    @property
    @abstractmethod
    def applicable_languages(self) -> List[Language]:
        """Languages this tool can diagnose."""
        ...

    @property
    @abstractmethod
    def applicable_categories(self) -> List[ProblemCategory]:
        """Problem categories this tool handles."""
        ...

    def is_available(self) -> bool:
        """Check if the tool binary is installed."""
        return shutil.which(self.name) is not None

    @abstractmethod
    def diagnose(self, target_path: str, category: ProblemCategory,
                 symptoms: Optional[List[str]] = None,
                 extra_args: Optional[Dict] = None) -> DiagnosisResult:
        """
        Run the diagnostic tool against a target.

        Args:
            target_path: Path to binary / project / core dump
            category: Problem category to diagnose
            symptoms: User-described symptoms
            extra_args: Tool-specific parameters

        Returns:
            DiagnosisResult with findings
        """
        ...

    def _run_command(self, cmd: str, timeout: int = 30,
                     cwd: Optional[str] = None) -> subprocess.CompletedProcess:
        """Run a shell command and return the result."""
        try:
            return subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=timeout, cwd=cwd,
            )
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(cmd, -1, "", "TIMEOUT")
        except Exception as e:
            return subprocess.CompletedProcess(cmd, -1, "", str(e))


class DiagnosticRegistry:
    """
    Registry of all available diagnostic tools.

    Routes diagnosis requests to the correct tools based on
    language + problem category.
    """

    def __init__(self):
        self._tools: Dict[str, DiagnosticTool] = {}

    def register(self, tool: DiagnosticTool) -> None:
        """Register a diagnostic tool."""
        self._tools[tool.name] = tool

    def get_tool(self, name: str) -> Optional[DiagnosticTool]:
        return self._tools.get(name)

    def list_available(self) -> List[DiagnosticTool]:
        """List all tools whose binaries are installed."""
        return [t for t in self._tools.values() if t.is_available()]

    def list_all(self) -> List[DiagnosticTool]:
        """List all registered tools (available or not)."""
        return list(self._tools.values())

    def find_tools(self, language: Language,
                   category: Optional[ProblemCategory] = None) -> List[DiagnosticTool]:
        """Find tools matching a language and optional category."""
        matches = []
        for tool in self._tools.values():
            if language not in tool.applicable_languages:
                continue
            if category and category not in tool.applicable_categories:
                continue
            if tool.is_available():
                matches.append(tool)
        return matches

    def health_check(self, language: Language) -> Dict[str, bool]:
        """Check which tools are available for a language."""
        result = {}
        for tool in self._tools.values():
            if language in tool.applicable_languages:
                result[tool.name] = tool.is_available()
        return result

    def diagnose(self, language: Language, category: ProblemCategory,
                 target_path: str, symptoms: Optional[List[str]] = None) -> List[DiagnosisResult]:
        """Run all applicable tools for a language + category."""
        tools = self.find_tools(language, category)
        results = []
        for tool in tools:
            try:
                result = tool.diagnose(target_path, category, symptoms)
                results.append(result)
            except Exception as e:
                results.append(DiagnosisResult(
                    tool_name=tool.name,
                    problem_category=category,
                    success=False,
                    error=str(e),
                ))
        return results
