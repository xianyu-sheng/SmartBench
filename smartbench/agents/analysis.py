"""
Analysis Agent

Multi-model analysis agent that coordinates AI models for performance analysis.
"""

import time
from typing import Dict, Any, Optional, List
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from smartbench.agents.base import BaseAgent, AgentResult, AgentStatus
from smartbench.core.types import (
    AnalysisContext,
    AnalysisResult,
    Metrics,
    SystemType,
    Suggestion,
)
from smartbench.plugins.models.base import BaseModelPlugin
from smartbench.engine.aggregator import SuggestionAggregator
from smartbench.engine.weight import WeightEngine


@dataclass
class AnalysisConfig:
    """Configuration for analysis."""
    models: List[str] = field(default_factory=list)  # model names to use
    max_suggestions: int = 5
    confidence_threshold: float = 0.3
    include_source_code: bool = True
    include_logs: bool = True
    max_retries: int = 2


class AnalysisAgent(BaseAgent):
    """
    Analysis Agent - coordinates multi-model analysis.

    Responsibilities:
    1. Build analysis context from observations
    2. Dispatch analysis to multiple AI models in parallel
    3. Aggregate and rank suggestions
    4. Cross-validate suggestions across models
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(
            name="analysis",
            description="Multi-model performance analysis and suggestion generation",
            config=config,
        )
        self._weight_engine: Optional[WeightEngine] = None
        self._aggregator: Optional[SuggestionAggregator] = None

    def validate(self, context: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate analysis configuration."""
        if "observations" not in context and "metrics" not in context:
            return False, "Missing observations or metrics in context"
        return True, None

    def execute(self, context: Dict[str, Any]) -> AgentResult:
        """
        Execute multi-model analysis.

        Expected context:
        - observations: Observer agent results
        - metrics: Current metrics
        - target_qps: Target QPS
        - system_name: System name
        - system_type: System type
        - models: List of model names to use (optional)

        Returns:
            AgentResult with analysis data and suggestions
        """
        start_time = time.time()

        try:
            # Extract context data
            observations = context.get("observations", {})
            metrics_data = context.get("metrics", {})
            target_qps = context.get("target_qps", 300.0)
            system_name = context.get("system_name", "unknown")
            system_type_str = context.get("system_type", "raft_kv")
            model_names = context.get("models", ["deepseek"])

            # Parse system type
            try:
                system_type = SystemType(system_type_str)
            except ValueError:
                system_type = SystemType.RAFT_KV

            # Build analysis context
            analysis_context = self._build_context(
                observations=observations,
                metrics_data=metrics_data,
                target_qps=target_qps,
                system_name=system_name,
                system_type=system_type,
            )

            # Get model plugins
            model_plugins = self._get_model_plugins(context, model_names)

            if not model_plugins:
                return AgentResult(
                    agent_name=self.name,
                    status=AgentStatus.FAILED,
                    error="No model plugins available",
                    duration=time.time() - start_time,
                )

            # Run parallel analysis
            analysis_results = self._run_parallel_analysis(
                context=analysis_context,
                plugins=model_plugins,
            )

            # Aggregate suggestions
            aggregated = self._aggregate_suggestions(
                analysis_results=analysis_results,
                confidence_threshold=context.get("confidence_threshold", 0.3),
            )

            # Cross-validate
            validation = self._cross_validate(aggregated)

            duration = time.time() - start_time

            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.SUCCESS,
                data={
                    "suggestions": [s.to_dict() for s in aggregated],
                    "suggestion_count": len(aggregated),
                    "model_results": [
                        {
                            "model": r.model_name,
                            "suggestions": len(r.suggestions),
                            "success": r.is_success,
                            "error": r.error,
                            "duration": r.processing_time,
                        }
                        for r in analysis_results
                    ],
                    "validation": validation,
                    "context": {
                        "target_qps": target_qps,
                        "system_name": system_name,
                        "current_qps": metrics_data.get("qps", 0),
                    },
                },
                duration=duration,
                metadata={
                    "models_used": [p.name for p in model_plugins],
                    "total_suggestions_before_agg": sum(
                        len(r.suggestions) for r in analysis_results
                    ),
                },
            )

        except Exception as e:
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.FAILED,
                error=str(e),
                duration=time.time() - start_time,
            )

    def _build_context(
        self,
        observations: Dict[str, Any],
        metrics_data: Dict[str, Any],
        target_qps: float,
        system_name: str,
        system_type: SystemType,
    ) -> AnalysisContext:
        """Build analysis context from observations."""

        # Extract metrics
        if isinstance(metrics_data, dict):
            metrics = Metrics(
                qps=metrics_data.get("qps", 0),
                avg_latency=metrics_data.get("avg_latency", 0),
                p50_latency=metrics_data.get("p50_latency", 0),
                p99_latency=metrics_data.get("p99_latency", 0),
                error_rate=metrics_data.get("error_rate", 0),
            )
        else:
            metrics = metrics_data

        # Extract logs
        logs = ""
        if isinstance(observations, dict):
            obs_data = observations.get("observations", {})
            if isinstance(obs_data, dict):
                logs = obs_data.get("logs", {}).get("content", "")
                if not logs:
                    logs = obs_data.get("logs", "")

        # Extract source code
        source_code = ""
        if isinstance(observations, dict):
            obs_data = observations.get("observations", {})
            if isinstance(obs_data, dict):
                source_files = obs_data.get("source_code", {}).get("files", {})

        return AnalysisContext(
            system_name=system_name,
            system_type=system_type,
            metrics=metrics,
            logs=logs,
            source_code=source_code,
            target_qps=target_qps,
        )

    def _get_model_plugins(
        self,
        context: Dict[str, Any],
        model_names: List[str],
    ) -> List[BaseModelPlugin]:
        """Get model plugins based on configuration."""
        from smartbench.plugins.models.openai_compat import OpenAICompatiblePlugin
        from smartbench.plugins.models.anthropic import AnthropicPlugin
        from smartbench.core.config import ConfigLoader

        plugins = []
        model_configs = context.get("model_configs", [])

        # Try to load from config
        if not model_configs:
            try:
                cfg = ConfigLoader.load("config/default.yaml")
                model_configs = [
                    m for m in cfg.models
                    if m.enabled and m.name in model_names
                ]
            except Exception:
                pass

        for config in model_configs:
            try:
                if config.provider == "anthropic":
                    plugin = AnthropicPlugin(
                        api_key=config.api_key,
                        model=config.model,
                    )
                else:
                    plugin = OpenAICompatiblePlugin(
                        api_key=config.api_key,
                        base_url=config.base_url,
                        model=config.model,
                    )
                plugins.append(plugin)
            except Exception:
                continue

        return plugins

    def _run_parallel_analysis(
        self,
        context: AnalysisContext,
        plugins: List[BaseModelPlugin],
    ) -> List[AnalysisResult]:
        """Run analysis in parallel across all models."""
        results = []

        with ThreadPoolExecutor(max_workers=len(plugins)) as executor:
            futures = {
                executor.submit(plugin.analyze, context): plugin
                for plugin in plugins
            }

            for future in as_completed(futures):
                plugin = futures[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    results.append(AnalysisResult(
                        model_name=plugin.name,
                        error=str(e),
                    ))

        return results

    def _aggregate_suggestions(
        self,
        analysis_results: List[AnalysisResult],
        confidence_threshold: float,
    ) -> List[Suggestion]:
        """Aggregate suggestions from all models."""
        if not self._weight_engine:
            self._weight_engine = WeightEngine()

        if not self._aggregator:
            self._aggregator = SuggestionAggregator(
                weight_engine=self._weight_engine,
                confidence_threshold=confidence_threshold,
            )

        # Filter successful results
        successful_results = [r for r in analysis_results if r.is_success]

        if not successful_results:
            return []

        return self._aggregator.aggregate(
            results=successful_results,
            max_suggestions=5,
        )

    def _cross_validate(self, suggestions: List[Suggestion]) -> Dict[str, Any]:
        """
        Cross-validate suggestions.

        Checks consensus among multiple models and validates suggestion quality.
        """
        if not suggestions:
            return {
                "valid": False,
                "reason": "No suggestions to validate",
                "consensus_score": 0,
            }

        # Calculate consensus
        high_confidence = [s for s in suggestions if s.final_weight > 0.5]
        low_confidence = [s for s in suggestions if s.final_weight <= 0.5]

        # Group by similar topics
        topics = self._group_by_topic(suggestions)

        return {
            "valid": len(high_confidence) > 0,
            "high_confidence_count": len(high_confidence),
            "consensus_score": len(high_confidence) / len(suggestions) if suggestions else 0,
            "topic_groups": len(topics),
            "recommendations": [
                f"Focus on: {topic}"
                for topic, group in topics.items()
                if len(group) >= 2
            ],
        }

    def _group_by_topic(self, suggestions: List[Suggestion]) -> Dict[str, List[Suggestion]]:
        """Group suggestions by topic/keyword similarity."""
        groups: Dict[str, List[Suggestion]] = {}

        for suggestion in suggestions:
            # Extract key words from title
            words = suggestion.title.lower().split()
            key = " ".join(words[:2])  # Use first two words as key
            if key not in groups:
                groups[key] = []
            groups[key].append(suggestion)

        return groups


class IncrementalAnalysisAgent(AnalysisAgent):
    """
    Incremental analysis that builds on previous results.

    Performs multiple rounds of analysis with refined context.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self._previous_results: List[AgentResult] = []

    def execute_with_refinement(
        self,
        context: Dict[str, Any],
        rounds: int = 2,
    ) -> AgentResult:
        """
        Execute analysis with multiple refinement rounds.

        Args:
            context: Initial analysis context
            rounds: Number of refinement rounds

        Returns:
            Final AgentResult with refined suggestions
        """
        all_suggestions: List[Suggestion] = []
        refinement_history = []

        for round_num in range(rounds):
            # Add previous suggestions to context for refinement
            if all_suggestions:
                context["previous_suggestions"] = [
                    s.to_dict() for s in all_suggestions
                ]
                context["refinement_round"] = round_num + 1

            result = self.execute(context)

            if result.is_success():
                suggestions_data = result.data.get("suggestions", [])
                refinement_history.append({
                    "round": round_num + 1,
                    "suggestions": suggestions_data,
                })

                # Convert back to Suggestion objects
                for s_dict in suggestions_data:
                    suggestion = Suggestion(
                        title=s_dict.get("title", ""),
                        description=s_dict.get("description", ""),
                        pseudocode=s_dict.get("pseudocode", ""),
                        priority=s_dict.get("priority", 3),
                        risk_level=s_dict.get("risk_level", "medium"),
                        expected_gain=s_dict.get("expected_gain", ""),
                    )
                    all_suggestions.append(suggestion)

            # Reset context for next round
            context.pop("previous_suggestions", None)
            context.pop("refinement_round", None)

        return AgentResult(
            agent_name=self.name,
            status=AgentStatus.SUCCESS,
            data={
                "suggestions": [s.to_dict() for s in all_suggestions],
                "refinement_rounds": rounds,
                "refinement_history": refinement_history,
            },
        )
