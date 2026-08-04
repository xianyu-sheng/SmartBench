"""
Verifier — top-level evidence verification orchestrator.

This is the single entry point used by the DebateEngine.
It coordinates all verification layers:
  - LocationVerifier (disk checks)
  - EvidenceExtractor (code reading)
  - CrossChecker (graph + source cross-validation)
  - VerdictScorer (scoring and flagging)

All verification is ZERO-LLM (deterministic I/O only).
"""

import logging
from typing import Any, Dict, List, Optional

from smartbench.graph.retriever import GraphRetriever
from smartbench.graph.schema import CodeGraph
from smartbench.verifier.cross_checker import CrossChecker
from smartbench.verifier.extractor import EvidenceExtractor
from smartbench.verifier.location import LocationVerifier
from smartbench.verifier.scorer import VerdictScorer

logger = logging.getLogger(__name__)


class Verifier:
    """
    Top-level verifier for debate engine integration.

    Usage:
        verifier = Verifier(project_path, graph, retriever)
        verified_proposals = verifier.verify_proposals(proposals)
        verified_critique = verifier.verify_critique(critique, proposals)
        summary = verifier.build_summary(proposals)  # for prompt injection
    """

    def __init__(self, project_path: str,
                 graph: CodeGraph,
                 graph_retriever: GraphRetriever,
                 hybrid_retriever=None,
                 type_evidence=None):
        """
        Args:
            project_path: Root directory of the project
            graph: The project's code graph
            graph_retriever: GraphRetriever for structural context
            hybrid_retriever: Optional HybridRetriever for RAG verification
            type_evidence: Optional iterable of TypeEvidence (SemanticIR
                type evidence) for resource_type claim verification.
        """
        self.project_path = project_path or getattr(graph, "project_path", "")
        self.graph = getattr(graph, "graph", graph)
        self.loc_verifier = LocationVerifier(project_path)
        self.extractor = EvidenceExtractor(project_path, graph)
        self.cross_checker = CrossChecker(
            graph, project_path, graph_retriever, hybrid_retriever,
            type_evidence=type_evidence,
        )
        self.scorer = VerdictScorer()

    # ── Public API ──────────────────────────────────────────────────────

    def verify_proposals(self, proposals: List[Dict]) -> List[Dict]:
        """
        Verify each proposal and annotate with verification data.

        Pipeline:
          1. CrossChecker.verify_proposals() — extract claims, check disk + graph
          2. VerdictScorer.score_proposals() — normalize scores, add breakdowns

        Args:
            proposals: List of proposal dicts from Proposer

        Returns:
            Proposals with "__verification" field containing scores, locations, flags
        """
        if not proposals:
            return proposals

        # Step 1: Cross-check against actual code
        verified = self.cross_checker.verify_proposals(proposals)

        # Step 2: Score and flag
        scored = self.scorer.score_proposals(verified)

        return scored

    def verify_critique(self, critique: Dict,
                        proposals: List[Dict]) -> Dict:
        """
        Verify critique verdicts against actual code.

        Checks whether:
          - Cited concerns reference real code
          - "Reject" verdicts have valid (verifiable) reasons
          - "Accept" verdicts don't overlook hallucinated proposals

        Args:
            critique: Critique output dict
            proposals: Original proposals (already verified)

        Returns:
            Critique dict with "__verification" annotations
        """
        if not critique or not isinstance(critique, dict):
            return critique

        return self.cross_checker.verify_critique(critique, proposals)

    def build_summary(self, proposals: List[Dict]) -> str:
        """
        Build a Chinese-formatted verification summary for prompt injection.

        This is what gets injected into the Critique and Judge prompts.

        Args:
            proposals: Verified proposals (with __verification)

        Returns:
            Formatted markdown string
        """
        return self.scorer.build_verification_prompt_context(proposals)

    def verify_single(self, file_path: str,
                      line: Optional[int] = None,
                      function_name: Optional[str] = None) -> Dict:
        """
        Quick single-claim verification.

        Args:
            file_path: Claimed file path
            line: Claimed line number
            function_name: Optional function name

        Returns:
            Dict with verification result
        """
        result = self.loc_verifier.verify(file_path, line, function_name)
        return result.to_dict()

    def is_available(self) -> bool:
        """Check if verification is operational."""
        # Verification is always available (pure I/O, no dependencies)
        return True

    # ── Statistics ──────────────────────────────────────────────────────

    def get_verification_stats(self,
                               proposals: List[Dict]) -> Dict[str, Any]:
        """
        Compute aggregate verification statistics for display.

        Returns:
            {
                "total_proposals": N,
                "verified": N,
                "partial": N,
                "hallucinated": N,
                "overall_score": 0.85,
                "hallucination_rate": 0.2,
            }
        """
        total = len(proposals)
        verified_count = 0
        partial_count = 0
        hallucinated_count = 0
        scores = []

        for p in proposals:
            if not isinstance(p, dict):
                continue
            verif = p.get("__verification", {})
            if not isinstance(verif, dict):
                continue
            verdict = verif.get("verdict", "unverifiable")
            try:
                score = float(verif.get("verification_score", 0))
            except (TypeError, ValueError):
                score = 0.0
            score = max(0.0, min(score, 1.0))

            if verdict == "verified":
                verified_count += 1
            elif verdict == "partial":
                partial_count += 1
            elif verdict == "hallucinated":
                hallucinated_count += 1

            scores.append(score)

        avg_score = sum(scores) / max(len(scores), 1)

        return {
            "total_proposals": total,
            "verified": verified_count,
            "partial": partial_count,
            "hallucinated": hallucinated_count,
            "overall_score": round(avg_score, 2),
            "hallucination_rate": round(hallucinated_count / max(total, 1), 2),
        }
