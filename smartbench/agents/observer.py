"""
Observer Agent

Responsible for collecting and observing system data during benchmarks.
"""

import time
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from datetime import datetime

from smartbench.agents.base import BaseAgent, AgentResult, AgentStatus
from smartbench.core.types import Metrics


@dataclass
class ObservationPoint:
    """A single observation data point."""
    timestamp: str
    metrics: Dict[str, float]
    logs: str
    errors: List[str] = field(default_factory=list)
    annotations: Dict[str, Any] = field(default_factory=dict)


class ObserverAgent(BaseAgent):
    """
    Observer Agent - collects and observes system data.

    Responsibilities:
    1. Collect metrics during/after benchmarks
    2. Gather logs and error information
    3. Monitor system health
    4. Annotate observations with context
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(
            name="observer",
            description="Collect and observe system data during benchmarks",
            config=config,
        )
        self._observations: List[ObservationPoint] = []

    def validate(self, context: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate observer configuration."""
        if "system_plugin" not in context:
            return False, "Missing system_plugin in context"
        return True, None

    def execute(self, context: Dict[str, Any]) -> AgentResult:
        """
        Execute observation tasks.

        Expected context:
        - system_plugin: The system plugin to observe
        - benchmark_result: Previous benchmark results (optional)
        - observation_types: List of observation types to perform

        Returns:
            AgentResult with observation data
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

            observation_types = context.get(
                "observation_types",
                ["metrics", "logs", "health", "errors"]
            )

            observations = {}
            errors = []

            # Collect metrics
            if "metrics" in observation_types:
                metrics = self._collect_metrics(system_plugin)
                observations["metrics"] = metrics

            # Collect logs
            if "logs" in observation_types:
                logs = self._collect_logs(system_plugin)
                observations["logs"] = logs

            # Check health
            if "health" in observation_types:
                health = self._check_health(system_plugin)
                observations["health"] = health

            # Collect errors
            if "errors" in observation_types:
                error_data = self._collect_errors(system_plugin)
                observations["errors"] = error_data
                errors = error_data.get("errors", [])

            # Collect configuration
            if "config" in observation_types:
                config_data = self._collect_config(system_plugin)
                observations["config"] = config_data

            # Collect source code if available
            if "source_code" in observation_types:
                source_data = self._collect_source_code(system_plugin)
                observations["source_code"] = source_data

            duration = time.time() - start_time

            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.SUCCESS,
                data={
                    "observations": observations,
                    "observation_count": len(observations),
                    "has_errors": len(errors) > 0,
                    "error_count": len(errors),
                },
                duration=duration,
                metadata={
                    "observation_types": observation_types,
                    "timestamp": datetime.now().isoformat(),
                },
            )

        except Exception as e:
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.FAILED,
                error=str(e),
                duration=time.time() - start_time,
            )

    def _collect_metrics(self, system_plugin) -> Dict[str, Any]:
        """Collect current metrics from system."""
        try:
            metrics = system_plugin.get_metrics()
            return {
                "qps": metrics.qps,
                "avg_latency": metrics.avg_latency,
                "p50_latency": metrics.p50_latency,
                "p99_latency": metrics.p99_latency,
                "error_rate": metrics.error_rate,
                "is_healthy": metrics.is_healthy(),
                "timestamp": datetime.now().isoformat(),
            }
        except Exception as e:
            return {"error": str(e)}

    def _collect_logs(self, system_plugin, lines: int = 200) -> Dict[str, Any]:
        """Collect recent logs from system."""
        try:
            logs = system_plugin.get_logs(lines=lines)
            return {
                "content": logs[-5000:] if len(logs) > 5000 else logs,
                "length": len(logs),
                "timestamp": datetime.now().isoformat(),
            }
        except Exception as e:
            return {"error": str(e)}

    def _check_health(self, system_plugin) -> Dict[str, Any]:
        """Check system health status."""
        try:
            if hasattr(system_plugin, "get_cluster_health"):
                health = system_plugin.get_cluster_health()
                return health
            return {"status": "unknown", "reason": "Health check not available"}
        except Exception as e:
            return {"status": "error", "reason": str(e)}

    def _collect_errors(self, system_plugin) -> Dict[str, Any]:
        """Collect error information."""
        try:
            if hasattr(system_plugin, "get_error_logs"):
                error_logs = system_plugin.get_error_logs(lines=50)
                error_lines = [
                    line for line in error_logs.split('\n')
                    if line.strip()
                ]
                return {
                    "errors": error_lines,
                    "count": len(error_lines),
                    "timestamp": datetime.now().isoformat(),
                }
            return {"errors": [], "count": 0}
        except Exception as e:
            return {"errors": [], "error": str(e)}

    def _collect_config(self, system_plugin) -> Dict[str, Any]:
        """Collect system configuration."""
        try:
            if hasattr(system_plugin, "get_config"):
                config = system_plugin.get_config()
                return {
                    "files": list(config.keys()),
                    "timestamp": datetime.now().isoformat(),
                }
            return {}
        except Exception as e:
            return {"error": str(e)}

    def _collect_source_code(self, system_plugin) -> Dict[str, Any]:
        """Collect relevant source code."""
        try:
            if hasattr(system_plugin, "get_key_source_files"):
                files = system_plugin.get_key_source_files()
                return {
                    "files": list(files.keys()),
                    "file_count": len(files),
                    "timestamp": datetime.now().isoformat(),
                }
            return {}
        except Exception as e:
            return {"error": str(e)}


class ContinuousObserver:
    """
    Continuous observation over time.

    Periodically collects metrics during benchmark execution.
    """

    def __init__(self, system_plugin, interval: float = 1.0):
        self.system_plugin = system_plugin
        self.interval = interval
        self.samples: List[Dict[str, Any]] = []
        self._running = False

    def start(self):
        """Start continuous observation."""
        self._running = True

    def sample(self) -> Dict[str, Any]:
        """Take a single sample."""
        sample = {
            "timestamp": datetime.now().isoformat(),
            "metrics": {},
        }
        try:
            metrics = self.system_plugin.get_metrics()
            sample["metrics"] = {
                "qps": metrics.qps,
                "avg_latency": metrics.avg_latency,
                "p99_latency": metrics.p99_latency,
                "error_rate": metrics.error_rate,
            }
            self.samples.append(sample)
        except Exception as e:
            sample["error"] = str(e)
        return sample

    def stop(self) -> List[Dict[str, Any]]:
        """Stop observation and return samples."""
        self._running = False
        return self.samples

    def get_summary(self) -> Dict[str, Any]:
        """Get summary statistics of observed samples."""
        if not self.samples:
            return {"error": "No samples collected"}

        qps_values = [s["metrics"].get("qps", 0) for s in self.samples if "metrics" in s]
        latency_values = [s["metrics"].get("avg_latency", 0) for s in self.samples if "metrics" in s]

        return {
            "sample_count": len(self.samples),
            "avg_qps": sum(qps_values) / len(qps_values) if qps_values else 0,
            "max_qps": max(qps_values) if qps_values else 0,
            "min_qps": min(qps_values) if qps_values else 0,
            "avg_latency": sum(latency_values) / len(latency_values) if latency_values else 0,
            "duration": len(self.samples) * self.interval,
        }
