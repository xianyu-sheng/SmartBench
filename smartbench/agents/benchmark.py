"""
Benchmark Agent

Responsible for executing performance benchmarks on the target system.
"""

import time
import subprocess
from typing import Dict, Any, Optional, List
from pathlib import Path
from dataclasses import dataclass, field

from smartbench.agents.base import BaseAgent, AgentResult, AgentStatus
from smartbench.core.types import Metrics


@dataclass
class BenchmarkConfig:
    """Configuration for a benchmark run."""
    # 基于你的 Raft KV 系统实际能力调整
    # 实测：363 QPS (4线程)，峰值 687 QPS
    target_qps: float = 400.0
    duration: int = 30  # 缩短到 30 秒
    threads: int = 4  # 你的系统 4 线程最优
    ops: int = 100
    warmup_duration: int = 5  # 缩短预热时间
    incremental: bool = True
    # QPS 探索级别：根据你的系统能力调整
    qps_steps: List[float] = field(default_factory=lambda: [300, 400, 500, 600])
    rounds: int = 1


@dataclass
class BenchmarkRun:
    """Result of a single benchmark run."""
    config: BenchmarkConfig
    metrics: Metrics
    success: bool
    duration: float
    timestamp: str = ""


class BenchmarkAgent(BaseAgent):
    """
    Benchmark Agent - executes performance benchmarks.

    Responsibilities:
    1. Execute benchmark tests at different QPS levels
    2. Warm-up runs before measurement
    3. Incremental stress testing
    4. Metrics collection and validation
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(
            name="benchmark",
            description="Execute performance benchmarks on target system",
            config=config,
        )
        self._runs: List[BenchmarkRun] = []

    def validate(self, context: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate benchmark configuration."""
        if "system_plugin" not in context:
            return False, "Missing system_plugin in context"
        return True, None

    def execute(self, context: Dict[str, Any]) -> AgentResult:
        """
        Execute benchmark tests.

        Expected context:
        - system_plugin: The system plugin to benchmark
        - config: BenchmarkConfig (optional)
        - target_qps: Target QPS (optional)
        - rounds: Number of benchmark rounds (optional)

        Returns:
            AgentResult with benchmark data
        """
        start_time = time.time()

        try:
            system_plugin = context.get("system_plugin")
            if not system_plugin:
                return AgentResult(
                    agent_name=self.name,
                    status=AgentStatus.FAILED,
                    error="No system plugin provided",
                )

            # Parse configuration
            rounds = context.get("rounds", 1)
            target_qps = context.get("target_qps", 400.0)  # 默认 400
            incremental = context.get("incremental", True)

            # Build benchmark config
            bench_config = BenchmarkConfig(
                target_qps=target_qps,
                rounds=rounds,
                incremental=incremental,
            )

            results = []

            # 智能预热：使用插件的快速预热方法
            warmup_success = False
            if hasattr(system_plugin, "fast_warmup"):
                warmup_success = system_plugin.fast_warmup(ops=50, threads=2)

            if not warmup_success:
                # 回退：使用标准预热
                warmup_metrics = self._run_warmup(system_plugin)
                results.append({
                    "type": "warmup",
                    "metrics": self._metrics_to_dict(warmup_metrics),
                    "success": warmup_metrics.qps > 0 if warmup_metrics else False,
                })

            # 运行主压测
            metrics = system_plugin.get_metrics()
            results.append({
                "round": 1,
                "metrics": self._metrics_to_dict(metrics),
                "success": metrics.qps > 0,
                "qps_gap_percent": ((target_qps - metrics.qps) / target_qps * 100)
                if target_qps > 0 else 0,
            })

            # 智能增量测试：只测试比当前 QPS 更高的级别
            incremental_results = []
            if incremental and hasattr(system_plugin, "explore_qps_range"):
                # 只探索比当前 QPS 更高的级别，跳过已知的低级别
                relevant_levels = [q for q in bench_config.qps_steps if q >= metrics.qps * 0.8]
                if relevant_levels:
                    incremental_results = system_plugin.explore_qps_range(
                        qps_levels=relevant_levels[:3],  # 最多 3 个级别
                        ops_per_level=80,
                    )
            else:
                # 标准增量测试
                for qps_target in bench_config.qps_steps[:3]:  # 最多 3 个级别
                    metrics = self._run_at_qps(system_plugin, qps_target)
                    incremental_results.append({
                        "target_qps": qps_target,
                        "actual_qps": metrics.qps,
                        "avg_latency": metrics.avg_latency,
                        "p99_latency": metrics.p99_latency,
                        "error_rate": metrics.error_rate,
                    })

            duration = time.time() - start_time

            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.SUCCESS,
                data={
                    "rounds": rounds,
                    "results": results,
                    "incremental_results": incremental_results,
                    "target_qps": target_qps,
                    "warmup": results[0] if results else {},
                    "total_duration": duration,
                },
                duration=duration,
                metadata={
                    "total_runs": len(results),
                    "successful_runs": sum(1 for r in results if r.get("success", False)),
                    "warmup_success": warmup_success,
                },
            )

        except Exception as e:
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.FAILED,
                error=str(e),
                duration=time.time() - start_time,
            )

    def _run_warmup(self, system_plugin) -> Optional[Metrics]:
        """Run warmup benchmark."""
        try:
            # Ensure leader exists
            if hasattr(system_plugin, "ensure_leader"):
                if not system_plugin.ensure_leader(max_wait=45):
                    return None

            # Try quick benchmark
            if hasattr(system_plugin, "run_quick_benchmark"):
                return system_plugin.run_quick_benchmark(ops=100, threads=2)

            # Fallback to get_metrics
            return system_plugin.get_metrics()
        except Exception as e:
            print(f"Warmup failed: {e}")
            return None

    def _metrics_to_dict(self, metrics: Optional[Metrics]) -> Dict[str, Any]:
        """Convert Metrics to dictionary."""
        if metrics is None:
            return {}
        from dataclasses import asdict
        return asdict(metrics)

    def _run_at_qps(self, system_plugin, target_qps: float) -> Metrics:
        """Run benchmark targeting specific QPS."""
        try:
            # Use quick benchmark if available
            if hasattr(system_plugin, "run_quick_benchmark"):
                ops = int(target_qps * 10)  # 10 second test
                return system_plugin.run_quick_benchmark(ops=ops, threads=4)
            return system_plugin.get_metrics()
        except Exception:
            return Metrics(qps=0.0, avg_latency=0.0)


class BenchmarkOrchestrator:
    """
    Orchestrates multiple benchmark runs with different configurations.
    """

    def __init__(self):
        self.results: List[Dict[str, Any]] = []

    def run_suite(
        self,
        system_plugin,
        configs: List[BenchmarkConfig],
    ) -> List[AgentResult]:
        """
        Run a suite of benchmarks.

        Args:
            system_plugin: System plugin to benchmark
            configs: List of benchmark configurations

        Returns:
            List of AgentResult for each config
        """
        results = []

        for i, config in enumerate(configs):
            context = {
                "system_plugin": system_plugin,
                "target_qps": config.target_qps,
                "rounds": 3,
                "incremental": config.incremental,
            }

            agent = BenchmarkAgent()
            result = agent.execute(context)
            results.append(result)

            self.results.append({
                "config": config,
                "result": result,
            })

        return results

    def get_summary(self) -> Dict[str, Any]:
        """Get summary of all benchmark results."""
        if not self.results:
            return {"error": "No results available"}

        all_metrics = []
        for item in self.results:
            result = item["result"]
            if result.is_success():
                all_metrics.extend(result.data.get("results", []))

        if not all_metrics:
            return {"error": "No successful benchmarks"}

        successful = [m for m in all_metrics if m.get("success", False)]

        return {
            "total_runs": len(all_metrics),
            "successful_runs": len(successful),
            "avg_qps": sum(m.get("metrics", {}).get("qps", 0) for m in successful)
            / len(successful) if successful else 0,
            "best_qps": max(
                (m.get("metrics", {}).get("qps", 0) for m in successful),
                default=0
            ),
        }
