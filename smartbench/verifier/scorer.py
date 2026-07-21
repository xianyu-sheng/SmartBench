"""
VerdictScorer — assign verification scores and flag hallucinations.

Scoring factors (weighted):
  1. Location validity (40%): file exists + line in range
  2. Function existence (30%): named function exists in graph
  3. Call chain accuracy (20%): claimed calls match graph edges
  4. Source match (10%): actual code matches description
"""

from typing import List, Dict, Any, Optional
import logging


logger = logging.getLogger(__name__)


class VerdictScorer:
    """
    Scores proposals and flags potential hallucinations.

    A proposal scoring below 0.3 is flagged as likely hallucinated.
    """

    # Score thresholds (adjusted for realistic partial matches)
    VERIFIED_THRESHOLD = 0.6    # Above this → "verified"
    PARTIAL_THRESHOLD = 0.25    # Between → "partial"
                                # Below → "hallucinated"

    def score_proposals(self,
                        proposals: List[Dict]) -> List[Dict]:
        """
        Score multiple proposals and annotate each with scored verification.

        Also generates a summary suitable for prompt injection.

        Args:
            proposals: Proposal dicts with "__verification" fields

        Returns:
            Proposals with "__verification" enriched with score breakdown
        """
        scored = []
        for p in proposals:
            if not isinstance(p, dict):
                scored.append(p)
                continue

            verif = p.get("__verification", {})
            if not verif:
                p["__verification"] = {
                    "verification_score": 0.0,
                    "verdict": "unverifiable",
                    "flags": ["无法验证：提案未包含可验证声明"],
                    "detail": "请在提案中添加 evidence_claims",
                }
                scored.append(p)
                continue

            # Enrich with detailed breakdown
            verif["breakdown"] = self._compute_breakdown(verif)
            p["__verification"] = verif
            scored.append(p)

        return scored

    def flag_hallucinations(self, proposals: List[Dict],
                            threshold: float = 0.3) -> Dict[str, Any]:
        """
        Identify proposals likely to be LLM hallucinations.

        Returns:
            {
                "flagged": [...],    # proposals below threshold
                "clean": [...],      # proposals above threshold
                "summary": "..."     # Chinese summary
            }
        """
        flagged = []
        clean = []

        for p in proposals:
            if not isinstance(p, dict):
                clean.append(p)
                continue

            verif = p.get("__verification", {})
            score = verif.get("verification_score", 0)

            if score < threshold:
                flagged.append({
                    "title": p.get("title", "?"),
                    "score": score,
                    "hallucinated_locations": verif.get("hallucinated_locations", []),
                    "partial_locations": verif.get("partial_locations", []),
                })
            else:
                clean.append(p)

        # Build summary
        summary_parts = []
        if flagged:
            summary_parts.append(
                f"## 疑似幻觉 ({len(flagged)} 条)\n"
            )
            for f in flagged:
                summary_parts.append(
                    f"- [[X]] **{f['title']}** (得分: {f['score']:.0%})\n"
                )
                for loc in f.get("hallucinated_locations", []):
                    summary_parts.append(f"  - 文件不存在: `{loc}`\n")
                for loc in f.get("partial_locations", []):
                    summary_parts.append(f"  - 路径偏离: {loc}\n")

        if clean:
            summary_parts.append(
                f"\n## 已验证 ({len(clean)} 条)\n"
            )
            for c in clean:
                if isinstance(c, dict):
                    verif = c.get("__verification", {})
                    score = verif.get("verification_score", 0)
                    summary_parts.append(
                        f"- [✓] **{c.get('title', '?')}** (得分: {score:.0%})\n"
                    )

        return {
            "flagged": flagged,
            "clean": clean,
            "summary": "".join(summary_parts) if summary_parts else "无评分数据",
        }

    def build_verification_prompt_context(self,
                                          proposals: List[Dict]) -> str:
        """
        Build a formatted verification summary for injection into
        the next debate round's prompt.

        Returns:
            Chinese-formatted markdown string
        """
        flagged = self.flag_hallucinations(proposals)
        return flagged["summary"]

    # ── Internals ───────────────────────────────────────────────────────

    def _compute_breakdown(self, verif: Dict) -> Dict:
        """Compute detailed score breakdown."""
        verified = len(verif.get("verified_locations", []))
        partial = len(verif.get("partial_locations", []))
        hallucinated = len(verif.get("hallucinated_locations", []))
        total = verified + partial + hallucinated

        return {
            "total_claims": total,
            "verified_count": verified,
            "partial_count": partial,
            "hallucinated_count": hallucinated,
            "verification_rate": verified / max(total, 1),
            "hallucination_rate": hallucinated / max(total, 1),
        }

    @staticmethod
    def merge_scores(scores: List[float],
                     weights: Optional[List[float]] = None) -> float:
        """
        Merge multiple sub-scores into an aggregate score.

        Args:
            scores: List of 0.0-1.0 scores
            weights: Optional weight for each score (default: equal)

        Returns:
            Weighted average score
        """
        if not scores:
            return 0.0

        if weights is None:
            weights = [1.0] * len(scores)

        total_weight = sum(weights)
        if total_weight == 0:
            return sum(scores) / len(scores)

        return sum(s * w for s, w in zip(scores, weights)) / total_weight
