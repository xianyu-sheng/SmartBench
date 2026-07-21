"""
Verifier module — evidence-based claim verification for SmartBench.

Layers:
  1. LocationVerifier — file:line existence check + fuzzy path resolution
  2. EvidenceExtractor — read actual source code at claimed locations
  3. CrossChecker — cross-validate proposals against code graph + source
  4. VerdictScorer — score proposals by evidence strength
  5. Verifier — top-level orchestration

All verification is DETERMINISTIC (no LLM calls) — zero hallucination risk.
"""

from enum import Enum
from dataclasses import dataclass
from typing import Optional, Dict


class VerificationStatus(Enum):
    """Outcome of verifying a claim against actual code."""
    VERIFIED = "verified"            # File exists, line in range, code matches
    PARTIAL = "partial"              # File exists but path/detail differs
    HALLUCINATED = "hallucinated"    # File or function does NOT exist
    UNVERIFIABLE = "unverifiable"    # Claim too vague to verify


@dataclass
class VerificationResult:
    """Result of verifying a single claim."""
    status: VerificationStatus
    claim: str = ""                             # Original claim text
    claimed_file: Optional[str] = None          # What the claim says
    claimed_line: Optional[int] = None
    resolved_file: Optional[str] = None         # What actually exists
    resolved_line: Optional[int] = None
    actual_function: Optional[str] = None       # Function name at resolved location
    actual_content: Optional[str] = None        # Code at the resolved location
    confidence: float = 0.0                     # 0.0 - 1.0
    detail: str = ""                            # Human-readable (Chinese)

    def to_dict(self) -> Dict:
        return {
            "status": self.status.value,
            "claim": self.claim,
            "claimed_file": self.claimed_file,
            "claimed_line": self.claimed_line,
            "resolved_file": self.resolved_file,
            "resolved_line": self.resolved_line,
            "actual_function": self.actual_function,
            "actual_content": self.actual_content,
            "confidence": self.confidence,
            "detail": self.detail,
        }


# Lazy imports to avoid circular dependencies  # noqa: E402
from smartbench.verifier.location import LocationVerifier  # noqa: E402
from smartbench.verifier.extractor import EvidenceExtractor  # noqa: E402
from smartbench.verifier.cross_checker import CrossChecker  # noqa: E402
from smartbench.verifier.scorer import VerdictScorer  # noqa: E402
from smartbench.verifier.verifier import Verifier  # noqa: E402

__all__ = [
    "VerificationStatus",
    "VerificationResult",
    "LocationVerifier",
    "EvidenceExtractor",
    "CrossChecker",
    "VerdictScorer",
    "Verifier",
]
