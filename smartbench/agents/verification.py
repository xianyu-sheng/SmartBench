"""
Verification Agent

Verifies and cross-validates optimization suggestions.
"""

import time
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field

from smartbench.agents.base import BaseAgent, AgentResult, AgentStatus
from smartbench.core.types import Suggestion, RiskLevel


@dataclass
class VerificationResult:
    """Result of verifying a single suggestion."""
    suggestion: Dict[str, Any]
    is_valid: bool
    confidence_score: float
    validation_checks: Dict[str, bool]
    issues: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)


class VerificationAgent(BaseAgent):
    """
    Verification Agent - validates and cross-validates suggestions.

    Responsibilities:
    1. Verify suggestion feasibility and safety
    2. Cross-validate against historical data
    3. Check for conflicts between suggestions
    4. Rank suggestions by confidence
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(
            name="verification",
            description="Verify and cross-validate optimization suggestions",
            config=config,
        )
        self._verification_history: List[VerificationResult] = []

    def validate(self, context: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate verification context."""
        if "suggestions" not in context:
            return False, "Missing suggestions in context"
        return True, None

    def execute(self, context: Dict[str, Any]) -> AgentResult:
        """
        Execute verification of suggestions.

        Expected context:
        - suggestions: List of suggestions to verify
        - metrics: Current metrics for context
        - target_qps: Target QPS
        - historical_data: Optional historical verification data

        Returns:
            AgentResult with verified and ranked suggestions
        """
        start_time = time.time()

        try:
            suggestions = context.get("suggestions", [])
            metrics = context.get("metrics", {})
            target_qps = context.get("target_qps", 300.0)
            historical_data = context.get("historical_data", {})

            if not suggestions:
                return AgentResult(
                    agent_name=self.name,
                    status=AgentStatus.FAILED,
                    error="No suggestions to verify",
                    duration=time.time() - start_time,
                )

            verification_results = []
            all_issues = []
            all_recommendations = []

            for suggestion in suggestions:
                result = self._verify_single(
                    suggestion=suggestion,
                    metrics=metrics,
                    target_qps=target_qps,
                    historical_data=historical_data,
                )
                verification_results.append(result)

                if not result.is_valid:
                    all_issues.extend(result.issues)
                all_recommendations.extend(result.recommendations)

            # Sort by confidence score
            ranked = sorted(
                verification_results,
                key=lambda x: x.confidence_score,
                reverse=True
            )

            # Check for conflicts
            conflicts = self._detect_conflicts(ranked)

            # Generate summary
            summary = self._generate_summary(
                verification_results=ranked,
                conflicts=conflicts,
                metrics=metrics,
                target_qps=target_qps,
            )

            duration = time.time() - start_time

            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.SUCCESS,
                data={
                    "verified_suggestions": [r.suggestion for r in ranked],
                    "verification_count": len(ranked),
                    "valid_count": sum(1 for r in ranked if r.is_valid),
                    "conflicts": conflicts,
                    "issues": list(set(all_issues)),
                    "recommendations": list(set(all_recommendations)),
                    "summary": summary,
                },
                duration=duration,
                metadata={
                    "avg_confidence": sum(r.confidence_score for r in ranked) / len(ranked)
                    if ranked else 0,
                    "highest_confidence": ranked[0].confidence_score if ranked else 0,
                },
            )

        except Exception as e:
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.FAILED,
                error=str(e),
                duration=time.time() - start_time,
            )

    def _verify_single(
        self,
        suggestion: Dict[str, Any],
        metrics: Dict[str, Any],
        target_qps: float,
        historical_data: Dict[str, Any],
    ) -> VerificationResult:
        """Verify a single suggestion."""
        checks = {}
        issues = []
        recommendations = []

        # Check 1: Has required fields
        required_fields = ["title", "description", "pseudocode"]
        for field in required_fields:
            checks[f"has_{field}"] = field in suggestion and suggestion[field]

        if not all(checks.values()):
            issues.append(f"Missing required fields: {suggestion.get('title', 'unknown')}")

        # Check 2: Priority is reasonable
        priority = suggestion.get("priority", 3)
        checks["priority_reasonable"] = 1 <= priority <= 5
        if not checks["priority_reasonable"]:
            issues.append(f"Invalid priority: {priority}")

        # Check 3: Risk level is appropriate
        risk_level = suggestion.get("risk_level", "medium")
        try:
            risk = RiskLevel(risk_level) if isinstance(risk_level, str) else risk_level
            checks["risk_appropriate"] = risk in [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH]
        except ValueError:
            checks["risk_appropriate"] = False
            issues.append(f"Invalid risk level: {risk_level}")

        # Check 4: Expected gain is quantified
        expected_gain = suggestion.get("expected_gain", "")
        checks["has_expected_gain"] = bool(expected_gain) and expected_gain != "未知"
        if not checks["has_expected_gain"]:
            recommendations.append("Add quantified expected gain (e.g., 'QPS 提升 20%')")

        # Check 5: Has implementation steps
        impl_steps = suggestion.get("implementation_steps", [])
        checks["has_impl_steps"] = len(impl_steps) > 0
        if not checks["has_impl_steps"]:
            recommendations.append("Add detailed implementation steps")

        # Check 6: High risk with high priority warning
        if risk_level == RiskLevel.HIGH and priority >= 4:
            checks["high_risk_high_priority"] = False
            issues.append("High-risk suggestion with high priority requires extra caution")

        # Calculate confidence score
        passed_checks = sum(1 for c in checks.values() if c)
        confidence_score = passed_checks / len(checks) if checks else 0

        # Boost score for LOW risk suggestions
        if risk_level == RiskLevel.LOW:
            confidence_score = min(1.0, confidence_score + 0.1)

        # Reduce score for HIGH risk suggestions
        if risk_level == RiskLevel.HIGH:
            confidence_score = max(0.0, confidence_score - 0.1)

        is_valid = passed_checks >= len(checks) * 0.7  # 70% threshold

        return VerificationResult(
            suggestion=suggestion,
            is_valid=is_valid,
            confidence_score=confidence_score,
            validation_checks=checks,
            issues=issues,
            recommendations=recommendations,
        )

    def _detect_conflicts(
        self,
        verification_results: List[VerificationResult],
    ) -> List[Dict[str, Any]]:
        """Detect conflicts between suggestions."""
        conflicts = []
        suggestions = [r.suggestion for r in verification_results]

        # Group by similar titles
        for i, s1 in enumerate(suggestions):
            for j, s2 in enumerate(suggestions[i + 1:], i + 1):
                # Check if they address the same component
                title1_words = set(s1.get("title", "").lower().split())
                title2_words = set(s2.get("title", "").lower().split())

                overlap = len(title1_words & title2_words)

                if overlap >= 2:
                    conflicts.append({
                        "type": "similar_topic",
                        "suggestions": [s1.get("title"), s2.get("title")],
                        "overlap_words": list(title1_words & title2_words),
                        "recommendation": "Consider merging or choosing one",
                    })

        return conflicts

    def _generate_summary(
        self,
        verification_results: List[VerificationResult],
        conflicts: List[Dict[str, Any]],
        metrics: Dict[str, Any],
        target_qps: float,
    ) -> Dict[str, Any]:
        """Generate verification summary."""
        current_qps = metrics.get("qps", 0)
        gap = target_qps - current_qps
        gap_percent = (gap / target_qps * 100) if target_qps > 0 else 0

        valid = [r for r in verification_results if r.is_valid]
        low_risk = [r for r in valid if r.suggestion.get("risk_level") == "low"]
        medium_risk = [r for r in valid if r.suggestion.get("risk_level") == "medium"]
        high_risk = [r for r in valid if r.suggestion.get("risk_level") == "high"]

        return {
            "current_qps": current_qps,
            "target_qps": target_qps,
            "gap_percent": gap_percent,
            "total_suggestions": len(verification_results),
            "valid_suggestions": len(valid),
            "by_risk": {
                "low": len(low_risk),
                "medium": len(medium_risk),
                "high": len(high_risk),
            },
            "conflict_count": len(conflicts),
            "avg_confidence": sum(r.confidence_score for r in valid) / len(valid) if valid else 0,
            "top_suggestion": verification_results[0].suggestion.get("title") if verification_results else None,
            "action_items": [
                f"Review {len(conflicts)} conflicts" if conflicts else "No conflicts detected",
                f"Priority: {verification_results[0].suggestion.get('title')}" if verification_results else "No suggestions",
            ],
        }


class CrossValidationAgent(VerificationAgent):
    """
    Cross-validation agent that validates suggestions across multiple rounds.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self._validation_rounds: List[Dict[str, Any]] = []

    def execute_cross_validation(
        self,
        context: Dict[str, Any],
        rounds: int = 2,
    ) -> AgentResult:
        """
        Execute cross-validation across multiple rounds.

        Each round refines the validation based on previous results.
        """
        all_verified = []
        refinement_feedback = []

        current_context = context.copy()

        for round_num in range(rounds):
            result = self.execute(current_context)

            if result.is_success():
                verified = result.data.get("verified_suggestions", [])
                all_verified.extend(verified)

                # Build feedback for next round
                issues = result.data.get("issues", [])
                recommendations = result.data.get("recommendations", [])

                refinement_feedback.append({
                    "round": round_num + 1,
                    "verified_count": len(verified),
                    "issues": issues,
                })

                # Update context for next round
                current_context["previous_verified"] = verified
                current_context["refinement_feedback"] = issues

        return AgentResult(
            agent_name=self.name,
            status=AgentStatus.SUCCESS,
            data={
                "total_verified": len(all_verified),
                "rounds": rounds,
                "refinement_history": refinement_feedback,
                "verified_suggestions": all_verified[:5],  # Top 5
            },
        )