"""
LLM-enhanced diagnostic rules.

These rules use language models to provide more sophisticated analysis.
"""

from abc import abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from smartbench.core.rules.base import (
    DiagnosticRule,
    Finding,
    Severity,
)
from smartbench.graph.schema import CodeGraph, NodeType


@dataclass
class CodeSnippet:
    """A code snippet to analyze."""

    file_path: str
    line_start: int
    line_end: int
    language: str
    code: str


class LLMClient:
    """Simple LLM client wrapper (placeholder - use your existing LLM client)."""

    def __init__(
        self,
        provider: str = "deepseek",
        model: str = "deepseek-chat",
        api_key: Optional[str] = None,
    ):
        self.provider = provider
        self.model = model
        self.api_key = api_key

    def analyze_code(
        self,
        prompt: str,
        code_snippet: CodeSnippet,
        temperature: float = 0.7,
        max_tokens: int = 1000,
    ) -> str:
        """Analyze code with an LLM.

        This is a placeholder - integrate with your actual LLM client.
        """
        # Placeholder - integrate your actual LLM client here
        # For now, return empty
        return "{}"


class LLMBasedRule(DiagnosticRule):
    """Base class for LLM-enhanced diagnostic rules."""

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client

    @property
    def requires_llm(self) -> bool:
        return True

    @abstractmethod
    def get_prompt_template(self) -> str:
        """Get the prompt template for this rule."""
        pass

    def extract_code_snippets(
        self, ir: CodeGraph
    ) -> List[CodeSnippet]:
        """Extract code snippets to analyze from the IR."""
        snippets: List[CodeSnippet] = []

        files: Dict[str, str] = {}
        for n in ir.nodes.values():
            if n.file_path not in files:
                source = self._read_source(ir, n.file_path)
                if source:
                    files[n.file_path] = source

        for file_path, source in files.items():
            lines = source.split("\n")
            func_nodes = [
                n for n in ir.nodes.values()
                if n.node_type == NodeType.FUNCTION
                and n.file_path == file_path
            ]

            for fn in func_nodes:
                line_start = max(0, fn.line_start - 1)
                line_end = min(len(lines), (fn.line_end or fn.line_start) + 2)
                code = "\n".join(lines[line_start:line_end])
                snippet = CodeSnippet(
                    file_path=file_path,
                    line_start=fn.line_start,
                    line_end=fn.line_end or fn.line_start,
                    language=fn.language,
                    code=code,
                )
                snippets.append(snippet)

        return snippets

    def _read_source(self, ir: CodeGraph, file_path: str) -> Optional[str]:
        try:
            project_path = ir.meta.get("project_path")
            if project_path:
                full_path = Path(project_path) / file_path
                from smartbench.path_safety import read_text_bounded
                return read_text_bounded(full_path, 2 * 1024 * 1024)
        except Exception:
            pass
        return None


class ComplexityReviewRule(LLMBasedRule):
    """Uses LLM to review code complexity and suggest improvements."""

    @property
    def rule_id(self) -> str:
        return "complexity_review"

    @property
    def rule_name(self) -> str:
        return "Complexity Review"

    @property
    def severity(self) -> Severity:
        return Severity.INFO

    @property
    def description(self) -> str:
        return "Uses LLM to review code complexity and suggest improvements"

    def get_prompt_template(self) -> str:
        return """Review this code for complexity, readability, and maintainability.

Look for:
1. Functions that are too long
2. Deep nesting
3. Complex conditionals
4. Poor naming
5. Missing comments/documentation
6. Opportunities for simplification

Return findings in JSON format:
{
  "findings": [
    {
      "severity": "warning|info",
      "line": 1,
      "message": "...",
      "suggestion": "..."
    }
  ]
}"""

    def analyze(self, ir: CodeGraph) -> List[Finding]:
        findings: List[Finding] = []

        if not self.llm_client:
            return findings

        snippets = self.extract_code_snippets(ir)
        # Limit to prevent too many LLM calls
        for snippet in snippets[:5]:  # Analyze up to 5 functions
            try:
                self.llm_client.analyze_code(
                    self.get_prompt_template(),
                    snippet,
                    max_tokens=500,
                )
                # Parse the JSON result and create findings
                # This is a placeholder - implement actual JSON parsing
                pass
            except Exception:
                continue

        return findings


class SecurityReviewRule(LLMBasedRule):
    """Uses LLM to review code for security vulnerabilities."""

    @property
    def rule_id(self) -> str:
        return "security_review"

    @property
    def rule_name(self) -> str:
        return "Security Review"

    @property
    def severity(self) -> Severity:
        return Severity.WARNING

    @property
    def description(self) -> str:
        return "Uses LLM to review code for security vulnerabilities"

    def get_prompt_template(self) -> str:
        return """Review this code for security vulnerabilities.

Look for:
1. Injection vulnerabilities (SQL, command, XSS)
2. Insecure cryptography
3. Authentication/authorization issues
4. Information exposure
5. Insecure dependencies
6. Input validation issues

Return findings in JSON format:
{
  "findings": [
    {
      "severity": "error|warning|info",
      "line": 1,
      "message": "...",
      "suggestion": "..."
    }
  ]
}"""

    def analyze(self, ir: CodeGraph) -> List[Finding]:
        findings: List[Finding] = []

        if not self.llm_client:
            return findings

        snippets = self.extract_code_snippets(ir)
        for snippet in snippets[:10]:  # Analyze up to 10 functions
            try:
                self.llm_client.analyze_code(
                    self.get_prompt_template(),
                    snippet,
                    max_tokens=800,
                )
                # Parse the JSON result and create findings
                pass
            except Exception:
                continue

        return findings


def register_llm_rules(registry, llm_client: Optional[LLMClient] = None):
    """Register LLM-enhanced rules."""
    registry.register(ComplexityReviewRule(llm_client))
    registry.register(SecurityReviewRule(llm_client))
