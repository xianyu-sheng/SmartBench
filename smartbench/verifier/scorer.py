"""Normalize CrossChecker scores, summarize evidence, and flag weak claims.

CrossChecker owns claim verification and aggregate scoring. VerdictScorer keeps
that score on a bounded 0..1 scale, adds deterministic count/rate breakdowns,
and creates the human-readable hallucination summary.
"""

import logging
import math
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class VerdictScorer:
    """
    Scores proposals and flags potential hallucinations.

    By default, a proposal scoring below 0.3 is flagged as likely hallucinated.
    """

    DEFAULT_FLAG_THRESHOLD = 0.3
    _VALID_VERDICTS = {
        "verified", "partial", "hallucinated", "unverifiable",
    }

    def score_proposals(self,
                        proposals: List[Dict]) -> List[Dict]:
        """
        Normalize verification annotations and add a count/rate breakdown.

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

            raw_verif = p.get("__verification", {})
            if not isinstance(raw_verif, dict) or not raw_verif:
                reason = (
                    "无法验证：核验结果格式无效"
                    if raw_verif
                    else "无法验证：提案未包含可验证声明"
                )
                p["__verification"] = {
                    "verification_score": 0.0,
                    "verdict": "unverifiable",
                    "flags": [reason],
                    "detail": "请在提案中添加 evidence_claims",
                    "verified_locations": [],
                    "partial_locations": [],
                    "hallucinated_locations": [],
                    "breakdown": self._compute_breakdown({}),
                }
                scored.append(p)
                continue

            verif = dict(raw_verif)
            score, score_is_valid = self._coerce_score(
                verif.get("verification_score")
            )
            verdict = verif.get("verdict", "unverifiable")
            if not isinstance(verdict, str) or verdict not in self._VALID_VERDICTS:
                verdict = "unverifiable"
            flags = self._list_value(verif.get("flags"))
            if not score_is_valid:
                verdict = "unverifiable"
                flags.append("[?] 核验得分格式无效，已按 0 处理")

            verif["verification_score"] = score
            verif["verdict"] = verdict
            verif["flags"] = flags
            for key in (
                "verified_locations",
                "partial_locations",
                "hallucinated_locations",
            ):
                verif[key] = self._list_value(verif.get(key))
            verif["breakdown"] = self._compute_breakdown(verif)
            p["__verification"] = verif
            scored.append(p)

        return scored

    def flag_hallucinations(
        self,
        proposals: List[Dict],
        threshold: float = DEFAULT_FLAG_THRESHOLD,
    ) -> Dict[str, Any]:
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
        normalized_threshold, threshold_is_valid = self._coerce_score(threshold)
        if not threshold_is_valid:
            normalized_threshold = self.DEFAULT_FLAG_THRESHOLD

        for p in proposals:
            if not isinstance(p, dict):
                clean.append(p)
                continue

            verif = p.get("__verification", {})
            if not isinstance(verif, dict):
                verif = {}
            score, _ = self._coerce_score(verif.get("verification_score"))

            if score < normalized_threshold:
                flagged.append({
                    "title": p.get("title", "?"),
                    "score": score,
                    "hallucinated_locations": self._list_value(
                        verif.get("hallucinated_locations")
                    ),
                    "partial_locations": self._list_value(
                        verif.get("partial_locations")
                    ),
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
                    if not isinstance(verif, dict):
                        verif = {}
                    score, _ = self._coerce_score(
                        verif.get("verification_score")
                    )
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

    @staticmethod
    def _list_value(value: Any) -> List:
        """Return a shallow copy only for actual list values."""
        return list(value) if isinstance(value, list) else []

    @staticmethod
    def _coerce_score(value: Any) -> tuple[float, bool]:
        """Convert an external score to a finite value in the range 0..1."""
        try:
            score = float(value)
        except (TypeError, ValueError):
            return 0.0, False
        if not math.isfinite(score):
            return 0.0, False
        return max(0.0, min(score, 1.0)), True

    def _compute_breakdown(self, verif: Dict) -> Dict:
        """Compute detailed score breakdown."""
        if not isinstance(verif, dict):
            verif = {}
        verified = len(self._list_value(verif.get("verified_locations")))
        partial = len(self._list_value(verif.get("partial_locations")))
        hallucinated = len(
            self._list_value(verif.get("hallucinated_locations"))
        )
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

        normalized_scores = []
        for score in scores:
            try:
                value = float(score)
            except (TypeError, ValueError):
                raise ValueError("scores must contain finite numbers") from None
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError("scores must be finite values between 0 and 1")
            normalized_scores.append(value)

        if weights is None:
            normalized_weights = [1.0] * len(normalized_scores)
        else:
            if len(weights) != len(normalized_scores):
                raise ValueError("weights must have the same length as scores")
            normalized_weights = []
            for weight in weights:
                try:
                    value = float(weight)
                except (TypeError, ValueError):
                    raise ValueError(
                        "weights must contain finite numbers"
                    ) from None
                if not math.isfinite(value) or value < 0:
                    raise ValueError(
                        "weights must be finite and non-negative"
                    )
                normalized_weights.append(value)

        total_weight = sum(normalized_weights)
        if total_weight == 0:
            return sum(normalized_scores) / len(normalized_scores)

        return sum(
            score * weight
            for score, weight in zip(normalized_scores, normalized_weights)
        ) / total_weight
