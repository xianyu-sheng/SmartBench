"""
Orchestrator Agent

Unified coordinator for the multi-agent benchmark system.
"""

import time
import uuid
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from smartbench.agents.base import BaseAgent, AgentResult, AgentStatus
from smartbench.agents.benchmark import BenchmarkAgent
from smartbench.agents.observer import ObserverAgent
from smartbench.agents.analysis import AnalysisAgent, IncrementalAnalysisAgent
from smartbench.agents.verification import VerificationAgent, CrossValidationAgent


class PipelineMode(Enum):
    """Execution mode for the pipeline."""
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"
    INCREMENTAL = "incremental"


@dataclass
class PipelineConfig:
    """Configuration for the agent pipeline."""
    benchmark_rounds: int = 1
    analysis_rounds: int = 2
    verification_rounds: int = 1
    incremental_analysis: bool = True
    cross_validation: bool = True
    max_suggestions: int = 5
    models: List[str] = field(default_factory=lambda: ["deepseek"])


class OrchestratorAgent(BaseAgent):
    """
    Orchestrator Agent - unified coordinator for all agents.

    This is the main entry point that:
    1. Coordinates BenchmarkAgent, ObserverAgent, AnalysisAgent
    2. Manages multi-round incremental analysis
    3. Handles cross-validation
    4. Produces final optimized suggestions
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(
            name="orchestrator",
            description="Unified coordinator for multi-agent benchmark system",
            config=config,
        )
        self._benchmark_agent = BenchmarkAgent()
        self._observer_agent = ObserverAgent()
        self._analysis_agent = AnalysisAgent()
        self._verification_agent = VerificationAgent()
        self._cross_validation_agent = CrossValidationAgent()
        self._incremental_analysis = IncrementalAnalysisAgent()

    def validate(self, context: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate orchestrator context."""
        if "system_plugin" not in context:
            return False, "Missing system_plugin in context"
        return True, None

    def execute(self, context: Dict[str, Any]) -> AgentResult:
        """
        Execute the complete multi-agent pipeline.

        Expected context:
        - system_plugin: The system plugin to benchmark
        - target_qps: Target QPS (default: 300.0)
        - benchmark_rounds: Number of benchmark rounds (default: 1)
        - analysis_rounds: Number of analysis refinement rounds (default: 2)
        - models: List of model names to use (default: ["deepseek"])
        - system_name: System name (default: "unknown")
        - system_type: System type (default: "raft_kv")

        Returns:
            AgentResult with final optimized suggestions
        """
        start_time = time.time()
        pipeline_id = str(uuid.uuid4())

        try:
            # Parse configuration
            target_qps = context.get("target_qps", 300.0)
            benchmark_rounds = context.get("benchmark_rounds", 1)
            analysis_rounds = context.get("analysis_rounds", 2)
            models = context.get("models", ["deepseek"])
            system_name = context.get("system_name", "unknown")
            system_type = context.get("system_type", "raft_kv")
            cross_validation = context.get("cross_validation", True)

            pipeline_log = {
                "pipeline_id": pipeline_id,
                "start_time": datetime.now().isoformat(),
                "stages": [],
            }

            results = {}
            all_suggestions = []
            all_verified = []

            # ===== Stage 1: Benchmark =====
            benchmark_result = self._run_benchmark(
                context=context,
                rounds=benchmark_rounds,
                target_qps=target_qps,
            )
            results["benchmark"] = benchmark_result
            pipeline_log["stages"].append({
                "stage": "benchmark",
                "status": benchmark_result.status.value,
                "duration": benchmark_result.duration,
            })

            if not benchmark_result.is_success():
                return self._create_error_result(
                    error="Benchmark failed",
                    pipeline_id=pipeline_id,
                    start_time=start_time,
                    results=results,
                )

            # ===== Stage 2: Observation =====
            observation_result = self._run_observation(
                context=context,
                benchmark_result=benchmark_result,
            )
            results["observation"] = observation_result
            pipeline_log["stages"].append({
                "stage": "observation",
                "status": observation_result.status.value,
                "duration": observation_result.duration,
            })

            # ===== Stage 3: Analysis =====
            analysis_result = self._run_analysis(
                context=context,
                observation_result=observation_result,
                target_qps=target_qps,
                models=models,
                system_name=system_name,
                system_type=system_type,
                rounds=analysis_rounds,
            )
            results["analysis"] = analysis_result
            pipeline_log["stages"].append({
                "stage": "analysis",
                "status": analysis_result.status.value,
                "duration": analysis_result.duration,
            })

            if analysis_result.is_success():
                all_suggestions = analysis_result.data.get("suggestions", [])

            # ===== Stage 4: Verification =====
            if cross_validation and all_suggestions:
                verification_result = self._run_verification(
                    suggestions=all_suggestions,
                    context=context,
                    target_qps=target_qps,
                )
                results["verification"] = verification_result
                pipeline_log["stages"].append({
                    "stage": "verification",
                    "status": verification_result.status.value,
                    "duration": verification_result.duration,
                })

                if verification_result.is_success():
                    all_verified = verification_result.data.get("verified_suggestions", [])
            else:
                all_verified = all_suggestions

            # ===== Generate Final Report =====
            duration = time.time() - start_time
            pipeline_log["end_time"] = datetime.now().isoformat()
            pipeline_log["total_duration"] = duration

            # Extract current QPS from benchmark result
            current_qps = 0.0
            if "benchmark" in results and hasattr(results["benchmark"], "data"):
                bench_data = results["benchmark"].data
                if "results" in bench_data and bench_data["results"]:
                    metrics = bench_data["results"][0].get("metrics", {})
                    current_qps = metrics.get("qps", 0)

            final_result = AgentResult(
                agent_name=self.name,
                status=AgentStatus.SUCCESS,
                data={
                    "pipeline_id": pipeline_id,
                    "target_qps": target_qps,
                    "current_qps": current_qps,
                    "suggestions": all_verified[:5],
                    "all_suggestions": all_suggestions,
                    "stages": pipeline_log["stages"],
                    "total_duration": duration,
                },
                duration=duration,
                metadata={
                    "benchmark_rounds": benchmark_rounds,
                    "analysis_rounds": analysis_rounds,
                    "models_used": models,
                },
            )

            return final_result

        except Exception as e:
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.FAILED,
                error=str(e),
                duration=time.time() - start_time,
            )

    def _run_benchmark(
        self,
        context: Dict[str, Any],
        rounds: int,
        target_qps: float,
    ) -> AgentResult:
        """Run benchmark agent."""
        benchmark_context = {
            "system_plugin": context.get("system_plugin"),
            "target_qps": target_qps,
            "rounds": rounds,
            "incremental": context.get("incremental", True),
        }
        return self._benchmark_agent.execute(benchmark_context)

    def _run_observation(
        self,
        context: Dict[str, Any],
        benchmark_result: AgentResult,
    ) -> AgentResult:
        """Run observer agent."""
        observation_context = {
            "system_plugin": context.get("system_plugin"),
            "benchmark_result": benchmark_result.data,
            "observation_types": ["metrics", "logs", "health", "errors", "config"],
        }
        return self._observer_agent.execute(observation_context)

    def _run_analysis(
        self,
        context: Dict[str, Any],
        observation_result: AgentResult,
        target_qps: float,
        models: List[str],
        system_name: str,
        system_type: str,
        rounds: int,
    ) -> AgentResult:
        """Run analysis agent with optional incremental refinement."""
        analysis_context = {
            "observations": observation_result.data,
            "metrics": context.get("metrics", {}),
            "target_qps": target_qps,
            "models": models,
            "system_name": system_name,
            "system_type": system_type,
            "model_configs": context.get("model_configs", []),
        }

        # Use incremental analysis for multiple rounds
        if rounds > 1 and context.get("incremental_analysis", True):
            return self._incremental_analysis.execute_with_refinement(
                context=analysis_context,
                rounds=rounds,
            )
        else:
            return self._analysis_agent.execute(analysis_context)

    def _run_verification(
        self,
        suggestions: List[Dict],
        context: Dict[str, Any],
        target_qps: float,
    ) -> AgentResult:
        """Run verification agent."""
        metrics = context.get("metrics", {})

        # Get benchmark metrics if available
        if "benchmark_result" in context:
            bench_data = context["benchmark_result"]
            if "results" in bench_data and bench_data["results"]:
                metrics = bench_data["results"][0].get("metrics", {})

        verification_context = {
            "suggestions": suggestions,
            "metrics": metrics,
            "target_qps": target_qps,
            "historical_data": {},
        }

        return self._verification_agent.execute(verification_context)

    def _create_error_result(
        self,
        error: str,
        pipeline_id: str,
        start_time: float,
        results: Dict[str, AgentResult],
    ) -> AgentResult:
        """Create error result."""
        return AgentResult(
            agent_name=self.name,
            status=AgentStatus.FAILED,
            error=error,
            duration=time.time() - start_time,
            metadata={
                "pipeline_id": pipeline_id,
                "completed_stages": list(results.keys()),
            },
        )


def create_default_pipeline() -> OrchestratorAgent:
    """Create orchestrator with default configuration."""
    return OrchestratorAgent()


def run_full_pipeline(
    system_plugin,
    target_qps: float = 300.0,
    benchmark_rounds: int = 1,
    analysis_rounds: int = 2,
    models: List[str] = None,
    system_name: str = "unknown",
    system_type: str = "raft_kv",
) -> AgentResult:
    """
    Convenience function to run the full pipeline.

    Args:
        system_plugin: The system plugin to benchmark
        target_qps: Target QPS
        benchmark_rounds: Number of benchmark rounds
        analysis_rounds: Number of analysis refinement rounds
        models: List of model names to use
        system_name: System name
        system_type: System type

    Returns:
        AgentResult with final optimized suggestions
    """
    if models is None:
        models = ["deepseek"]

    orchestrator = create_default_pipeline()

    context = {
        "system_plugin": system_plugin,
        "target_qps": target_qps,
        "benchmark_rounds": benchmark_rounds,
        "analysis_rounds": analysis_rounds,
        "models": models,
        "system_name": system_name,
        "system_type": system_type,
        "cross_validation": True,
    }

    return orchestrator.execute(context)