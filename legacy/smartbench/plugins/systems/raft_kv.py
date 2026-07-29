"""
Raft KV Storage System Plugin

Provides benchmark and metric collection for the custom Raft distributed KV storage.
"""

import re
import subprocess
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

from smartbench.core.types import Metrics, SystemType
from smartbench.plugins.systems.base import BaseSystemPlugin, BenchmarkResult


class RaftKVPlugin(BaseSystemPlugin):
    """
    Raft KV Storage System Plugin

    Adapts to the custom distributed KV storage system:
    1. Automated benchmark execution
    2. Multi-metric parsing (QPS, latency, error rate)
    3. Log collection and analysis
    4. Source code extraction
    """

    def __init__(
        self,
        project_path: str = "/home/xianyu-sheng/MyKV_storageBase_Raft_cpp",
        build_dir: str = "build",
        log_dir: Optional[str] = None,
    ):
        """
        Initialize Raft KV plugin.

        Args:
            project_path: Project root directory
            build_dir: Build directory name
            log_dir: Log directory (default: build)
        """
        super().__init__()
        self._project_path = Path(project_path)
        self._build_dir = self._project_path / build_dir
        self._log_dir = Path(log_dir) if log_dir else self._build_dir

        # 优先使用快速压测脚本，减少等待时间
        fast_script = self._project_path / "test_fast_bench.sh"
        self._bench_script = fast_script if fast_script.exists() else (self._project_path / "test_stable_bench.sh")

        self._key_files = [
            "Raft/Raft.cpp",
            "KvServer/KvServer.cpp",
            "Clerk/clerk.cpp",
            "Skiplist-CPP/skiplist.h",
            "myRPC/User/KrpcChannel.cc",
        ]

    @property
    def name(self) -> str:
        return "raft_kv"

    @property
    def system_type(self) -> SystemType:
        return SystemType.RAFT_KV

    def get_metrics(self) -> Metrics:
        """
        Run benchmark and get performance metrics.

        Executes test_stable_bench.sh and parses output for QPS, latency, etc.

        Returns:
            Metrics: Performance metrics object
        """
        if not self._build_dir.exists():
            return self._error_metrics()

        kvclient = self._build_dir / "kvclient"
        if not kvclient.exists():
            return self._error_metrics()

        try:
            result = self.run_command(f"bash {self._bench_script}", timeout=180)
            output = result.stdout + result.stderr
            return self._parse_benchmark_output(output)
        except subprocess.TimeoutExpired:
            return self._error_metrics()
        except Exception:
            return self._error_metrics()

    def _error_metrics(self) -> Metrics:
        """Create error state metrics."""
        return Metrics(
            qps=0.0,
            avg_latency=0.0,
            p50_latency=0.0,
            p99_latency=0.0,
            error_rate=1.0,
        )

    def run_quick_benchmark(self, ops: int = 100, threads: int = 4) -> Metrics:
        """
        Run a quick benchmark without restarting the service.

        Args:
            ops: Number of operations
            threads: Number of threads

        Returns:
            Metrics: Performance metrics
        """
        if not (self._build_dir / "kvclient").exists():
            return self._error_metrics()

        try:
            result = self.run_command(
                f"./kvclient -i ../myRPC/conf/myrpc.conf -- --bench --ops {ops} --threads {threads}",
                timeout=60,
                cwd=str(self._build_dir),
            )
            return self._parse_benchmark_output(result.stdout + result.stderr)
        except Exception:
            return self._error_metrics()

    def _parse_benchmark_output(self, output: str) -> Metrics:
        """
        Parse benchmark output.

        Supports both English and Chinese output format parsing.

        Args:
            output: Benchmark output text

        Returns:
            Metrics: Performance metrics object
        """
        # 优先使用 fallback 解析（支持中文格式）
        metrics = self._fallback_parse(output)

        # 如果 fallback 解析失败，尝试标准解析
        if not metrics:
            metrics = BenchmarkResult.extract_metrics(output)

        return Metrics(
            qps=metrics.get('qps', 0.0),
            avg_latency=metrics.get('avg_latency', 0.0),
            p50_latency=metrics.get('p50_latency', 0.0),
            p99_latency=metrics.get('p99_latency', 0.0),
            error_rate=metrics.get('error_rate', 0.0),
        )

    def _fallback_parse(self, output: str) -> Dict[str, float]:
        """
        Fallback parsing method when standard parsing fails.

        Supports both English and Chinese output formats:
        - QPS: 265.678 ops/s
        - 平均延迟: 3.07557 ms
        - p95 延迟: 7.663 ms
        - p99 延迟: 8.767 ms

        Args:
            output: Output text

        Returns:
            Metrics dictionary
        """
        metrics = {}

        patterns = {
            'qps': [
                r'QPS[:\s=]+([\d.]+)',
                r'([\d.]+)\s+ops/s',
                r'Throughput[:\s]+([\d.]+)',
                r'QPS:\s*([\d.]+)',
            ],
            'avg_latency': [
                r'(?:平均延迟|平均|Avg)[：:\s]+([\d.]+)\s*(?:ms|millisec)?',
                r'(?:Average|Avg)\s*Latency[:\s]+([\d.]+)',
                r'Latency[:\s]+([\d.]+)\s*ms',
            ],
            'p50_latency': [
                r'P50[^:]*[:\s]+([\d.]+)',
                r'P50\s*Latency[:\s]+([\d.]+)',
                r'Median\s+latency[:\s]+([\d.]+)',
            ],
            'p99_latency': [
                r'P99[^:]*[:\s]+([\d.]+)',
                r'P99\s*Latency[:\s]+([\d.]+)',
                r'p99[^:]*[:\s]+([\d.]+)',
                r'99%\s*([\d.]+)',
            ],
            'p95_latency': [
                r'P95[^:]*[:\s]+([\d.]+)',
                r'p95[^:]*[:\s]+([\d.]+)',
            ],
            'error_rate': [
                r'(?:Error|Fail)[：:\s]+([\d.]+)',
                r'错误率[:\s]+([\d.]+)',
            ],
        }

        for metric, pattern_list in patterns.items():
            for pattern in pattern_list:
                match = re.search(pattern, output, re.IGNORECASE)
                if match:
                    value = float(match.group(1))
                    if metric == 'error_rate' and value > 1:
                        value = value / 100
                    metrics[metric] = value
                    break

        return metrics

    def get_logs(self, lines: int = 100) -> str:
        """
        Get server logs.

        Args:
            lines: Number of recent lines to retrieve

        Returns:
            Log content string
        """
        logs = []

        log_files = sorted(
            self._log_dir.glob("kvserver*.log"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )

        for log_file in log_files[:3]:
            try:
                with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                    all_lines = f.readlines()
                    logs.append(f"=== {log_file.name} ===")
                    logs.extend(all_lines[-lines:])
                    logs.append("")
            except Exception:
                continue

        return "\n".join(logs)

    def start_cluster(self, wait_seconds: int = 30) -> Dict[str, Any]:
        """
        Start the Raft KV cluster and wait for leader election.

        Args:
            wait_seconds: Maximum seconds to wait for leader election

        Returns:
            Dictionary with start result
        """
        import subprocess
        import time as time_module

        # Kill existing processes
        try:
            subprocess.run(
                ["pkill", "-9", "kvserver"],
                cwd=str(self._build_dir),
                capture_output=True,
            )
            subprocess.run(
                ["pkill", "-9", "kvclient"],
                cwd=str(self._build_dir),
                capture_output=True,
            )
            time_module.sleep(2)
        except Exception:
            pass

        # Start ZooKeeper
        try:
            subprocess.run(
                ["zkServer.sh", "start"],
                capture_output=True,
                timeout=5,
            )
            time_module.sleep(2)
        except Exception:
            pass

        # Start kvserver nodes
        for i in range(3):
            try:
                env = {**subprocess.os.environ.copy(), "RAFT_ME": str(i)}
                log_path = self._log_dir / f"kvserver{i}.log"
                with open(log_path, "w") as log_file:
                    subprocess.Popen(
                        ["./kvserver", "-i", f"../myRPC/conf/myrpc_{i}.conf"],
                        cwd=str(self._build_dir),
                        env=env,
                        stdout=log_file,
                        stderr=subprocess.STDOUT,
                    )
                    # flush to ensure file is written before handle closes
                    log_file.flush()
            except Exception:
                pass

        # Wait for leader election - check every 2 seconds
        for attempt in range(wait_seconds // 2):
            time_module.sleep(2)
            health = self.get_cluster_health()
            if health.get("leader_elected", False):
                return {
                    "success": True,
                    "leader": health.get("leader_id"),
                    "wait_seconds": attempt * 2,
                }
            # Log progress
            if attempt % 3 == 0:
                terms = health.get("terms", {})
                print(f"  等待 Leader 选举... ({attempt*2}s) Terms: {terms}")

        return {
            "success": False,
            "leader": None,
            "wait_seconds": wait_seconds,
        }

    def ensure_leader(self, max_wait: int = 45) -> bool:
        """
        Ensure leader is elected, start cluster if needed.

        Args:
            max_wait: Maximum seconds to wait

        Returns:
            True if leader is elected
        """
        health = self.get_cluster_health()
        if health.get("leader_elected", False):
            return True

        # Try to start cluster
        result = self.start_cluster(wait_seconds=max_wait)
        if result.get("success"):
            return True

        # If still no leader, try running the full benchmark script
        # which has better retry logic
        try:
            self.run_command(f"bash {self._bench_script}", timeout=120)
            return True
        except Exception:
            return False

    def get_error_logs(self, lines: int = 50) -> str:
        """
        Get error logs.

        Args:
            lines: Number of recent error log lines to retrieve

        Returns:
            Error log content
        """
        logs = []

        log_files = sorted(
            self._log_dir.glob("kvserver*.log"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )

        for log_file in log_files[:3]:
            try:
                with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                    all_lines = f.readlines()

                error_lines = [
                    line for line in all_lines[-500:]
                    if any(x in line.upper() for x in ['ERROR', 'FATAL', 'FAIL', 'WRONG'])
                ]

                if error_lines:
                    logs.append(f"=== {log_file.name} (ERRORS) ===")
                    logs.extend(error_lines[-lines:])
                    logs.append("")
            except Exception:
                continue

        return "\n".join(logs)

    def get_source_code(self, path: str) -> str:
        """
        Get source code snippet.

        Args:
            path: Relative path from project root

        Returns:
            Source code content
        """
        full_path = self._project_path / path
        if full_path.exists():
            try:
                with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                    return f.read()
            except Exception:
                return ""
        return ""

    def get_key_source_files(self) -> Dict[str, str]:
        """
        Get key source files for analysis.

        Returns:
            Dictionary of file path to content
        """
        result = {}
        for file_path in self._key_files:
            content = self.get_source_code(file_path)
            if content:
                result[file_path] = content
        return result

    def get_config(self, config_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Get configuration information.

        Args:
            config_name: Config file name (None for all)

        Returns:
            Configuration dictionary
        """
        configs = {}
        config_dir = self._project_path / "myRPC" / "conf"

        if config_dir.exists():
            if config_name:
                config_file = config_dir / config_name
                if config_file.exists():
                    try:
                        with open(config_file, 'r') as f:
                            configs[config_name] = f.read()
                    except Exception:
                        pass
            else:
                for config_file in config_dir.glob("*.conf"):
                    try:
                        with open(config_file, 'r') as f:
                            configs[config_file.name] = f.read()
                    except Exception:
                        continue

        return configs

    def check_leader_status(self) -> Dict[str, Any]:
        """
        Check Leader election status.

        Returns:
            Status information dictionary
        """
        status = {
            "has_leader": False,
            "leader_id": None,
            "terms": {},
            "ready": False,
            "last_log_index": {},
            "commit_index": {},
            "node_count": 0,
            "errors": [],
        }

        for i in range(3):
            log_file = self._log_dir / f"kvserver{i}.log"
            if not log_file.exists():
                status["errors"].append(f"kvserver{i}.log not found")
                continue

            status["node_count"] += 1

            try:
                with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()

                term_match = re.search(r'term\{(\d+)\}', content)
                if term_match:
                    status["terms"][f"node_{i}"] = int(term_match.group(1))

                last_log_match = re.search(r'lastLogIndex[:\s=]+(\d+)', content)
                if last_log_match:
                    status["last_log_index"][f"node_{i}"] = int(last_log_match.group(1))

                commit_match = re.search(r'commitIndex[:\s=]+(\d+)', content)
                if commit_match:
                    status["commit_index"][f"node_{i}"] = int(commit_match.group(1))

                leader_patterns = [
                    "I am the leader",
                    "become Leader",
                    "elected as leader",
                    "Leader election",
                    "doHeartBeat()-Leader:",
                    "Leader:{",
                    "发送心跳",
                ]
                if any(p in content for p in leader_patterns):
                    status["has_leader"] = True
                    status["leader_id"] = i

                ready_patterns = [
                    "Leader election complete",
                    "election complete",
                    "Leader ready",
                    "start to serve",
                ]
                if any(p in content for p in ready_patterns):
                    status["ready"] = True

                error_patterns = ["ERROR", "FATAL", "WRONG"]
                for line in content.split('\n')[-50:]:
                    if any(p in line for p in error_patterns):
                        status["errors"].append(f"node_{i}: {line.strip()[:100]}")

            except Exception as e:
                status["errors"].append(f"node_{i}: Log read failed - {e}")

        return status

    def get_cluster_health(self) -> Dict[str, Any]:
        """
        Get cluster health status.

        Returns:
            Health status dictionary
        """
        leader_status = self.check_leader_status()

        # Check log files for leader indicators
        if not leader_status["has_leader"]:
            for i in range(3):
                log_file = self._log_dir / f"kvserver{i}.log"
                if log_file.exists():
                    try:
                        with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                            content = f.read()
                        if "doHeartBeat()-Leader:" in content or "Leader:{" in content:
                            leader_status["has_leader"] = True
                            leader_status["leader_id"] = i
                            break
                    except Exception:
                        pass

        health = {
            "healthy": False,
            "reason": "",
            "leader_elected": leader_status["has_leader"],
            "all_nodes_ready": leader_status["ready"],
            "node_count": leader_status["node_count"],
            "errors": leader_status["errors"],
        }

        if not leader_status["has_leader"]:
            health["reason"] = "No leader found, election in progress"
        elif not leader_status["ready"]:
            health["reason"] = "Leader elected but cluster not ready"
        elif len(leader_status["terms"]) < 3:
            health["reason"] = f"Only {len(leader_status['terms'])} node logs available"
        else:
            terms = list(leader_status["terms"].values())
            if len(set(terms)) == 1:
                health["healthy"] = True
                health["reason"] = "Cluster healthy, leader elected, all nodes in same term"
            else:
                health["reason"] = f"Node terms inconsistent: {leader_status['terms']}"

        return health

    # ==================== 快速压测优化方法 ====================

    def fast_warmup(self, ops: int = 50, threads: int = 2) -> bool:
        """
        快速预热：不重启服务，仅发送少量请求确保连接正常。

        Args:
            ops: 操作数
            threads: 线程数

        Returns:
            True if warmup successful
        """
        if not (self._build_dir / "kvclient").exists():
            return False

        try:
            result = self.run_command(
                f"./kvclient -i ../myRPC/conf/myrpc.conf -- --bench --ops {ops} --threads {threads}",
                timeout=30,
                cwd=str(self._build_dir),
            )
            output = result.stdout + result.stderr
            metrics = self._fallback_parse(output)
            return metrics.get('qps', 0) > 0
        except Exception:
            return False

    def smart_ready_check(self, max_wait: int = 10) -> bool:
        """
        智能就绪检测：快速检查 Leader 是否就绪。

        Args:
            max_wait: 最大等待秒数

        Returns:
            True if cluster is ready
        """
        health = self.get_cluster_health()
        if health.get("healthy", False):
            return True

        # 快速检查日志
        for i in range(max_wait):
            for log_file in self._log_dir.glob("kvserver*.log"):
                try:
                    with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                    if "doHeartBeat()-Leader:" in content or "Leader" in content:
                        term_match = re.search(r'term\{(\d+)\}', content)
                        if term_match and int(term_match.group(1)) > 0:
                            time.sleep(0.5)  # 短暂等待确保稳定
                            return True
                except Exception:
                    continue
            time.sleep(1)

        return False

    def run_explore_qps(self, target_qps: float, ops: int = 200) -> Metrics:
        """
        探索指定 QPS 级别的性能表现。

        Args:
            target_qps: 目标 QPS
            ops: 操作数

        Returns:
            Metrics at this QPS level
        """
        if not (self._build_dir / "kvclient").exists():
            return self._error_metrics()

        # 估算需要的线程数（基于你的系统：4线程约 363 QPS）
        # 线程数 = 目标QPS / 90.75 (保守估计)
        threads = max(2, min(16, int(target_qps / 90) + 1))

        try:
            result = self.run_command(
                f"./kvclient -i ../myRPC/conf/myrpc.conf -- --bench --ops {ops} --threads {threads}",
                timeout=60,
                cwd=str(self._build_dir),
            )
            return self._parse_benchmark_output(result.stdout + result.stderr)
        except Exception:
            return self._error_metrics()

    def explore_qps_range(
        self,
        qps_levels: List[float] = None,
        ops_per_level: int = 150,
    ) -> List[Dict[str, Any]]:
        """
        并行探索多个 QPS 级别的性能（不重启服务）。

        Args:
            qps_levels: QPS 级别列表，默认 [300, 400, 500, 600, 700]
            ops_per_level: 每个级别的操作数

        Returns:
            每个级别的压测结果
        """
        if qps_levels is None:
            qps_levels = [300, 400, 500, 600, 700]

        results = []

        # 先确保服务就绪
        if not self.smart_ready_check(max_wait=10):
            # 需要重启
            self.start_cluster(wait_seconds=20)

        for qps in qps_levels:
            metrics = self.run_explore_qps(target_qps=qps, ops=ops_per_level)
            results.append({
                "target_qps": qps,
                "actual_qps": metrics.qps,
                "avg_latency": metrics.avg_latency,
                "p99_latency": metrics.p99_latency,
                "error_rate": metrics.error_rate,
                "success": metrics.qps > 0,
            })

        return results

    def incremental_benchmark(
        self,
        start_qps: float = 100,
        max_qps: float = 800,
        step: float = 100,
        ops_per_level: int = 100,
    ) -> Dict[str, Any]:
        """
        增量压测：从低 QPS 到高 QPS，逐步探索系统极限。

        Args:
            start_qps: 起始 QPS
            max_qps: 最大 QPS
            step: QPS 增量步长
            ops_per_level: 每个级别的操作数

        Returns:
            包含所有级别结果和关键指标的字典
        """
        qps_levels = []
        current = start_qps
        while current <= max_qps:
            qps_levels.append(current)
            current += step

        results = self.explore_qps_range(qps_levels, ops_per_level)

        # 分析结果
        max_achievable = 0.0
        bottleneck_level = None

        for r in results:
            if r["success"] and r["actual_qps"] > 0:
                if r["error_rate"] < 0.05:  # 5% 错误率阈值
                    if r["actual_qps"] > max_achievable:
                        max_achievable = r["actual_qps"]
                else:
                    bottleneck_level = r["target_qps"]
                    break

        return {
            "qps_levels": results,
            "max_achievable_qps": max_achievable,
            "bottleneck_qps": bottleneck_level,
            "optimal_config": self._find_optimal_config(results),
        }

    def _find_optimal_config(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """从压测结果中找到最优配置。"""
        valid = [r for r in results if r["success"] and r["error_rate"] < 0.05]

        if not valid:
            return {"threads": 4, "ops": 100, "reason": "no_valid_results"}

        # 综合评分：QPS 高、延迟低、错误率低
        def score(r):
            qps_score = r["actual_qps"] / 400  # 归一化
            latency_score = 10 / (r["p99_latency"] + 0.1)  # 延迟越低越好
            return qps_score * 0.6 + latency_score * 0.4

        best = max(valid, key=score)

        # 估算最佳线程数
        threads = max(2, min(16, int(best["actual_qps"] / 90) + 1))

        return {
            "threads": threads,
            "ops": 200,
            "expected_qps": best["actual_qps"],
            "expected_p99": best["p99_latency"],
            "reason": "综合评分最优",
        }
