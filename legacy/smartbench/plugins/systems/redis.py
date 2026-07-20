"""
Redis 缓存系统插件

支持 Redis 性能压测和指标采集。
"""

import re
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional

from smartbench.core.types import Metrics, SystemType
from smartbench.plugins.systems.base import BaseSystemPlugin, BenchmarkResult


class RedisPlugin(BaseSystemPlugin):
    """
    Redis 缓存系统插件

    支持：
    1. redis-benchmark 压测
    2. redis-cli info 指标采集
    3. 延迟监控
    """

    def __init__(
        self,
        project_path: str = "/tmp",
        host: str = "localhost",
        port: int = 6379,
        password: str = "",
        db: int = 0,
    ):
        """
        初始化 Redis 插件

        Args:
            project_path: 项目路径
            host: Redis 主机
            port: Redis 端口
            password: Redis 密码
            db: 数据库编号
        """
        super().__init__()
        self._project_path = Path(project_path)
        self.host = host
        self.port = port
        self.password = password
        self.db = db

    @property
    def name(self) -> str:
        return "redis"

    @property
    def system_type(self) -> SystemType:
        return SystemType.GENERIC

    def _get_redis_cli(self, *args) -> str:
        """执行 redis-cli 命令"""
        cmd = f"redis-cli -h {self.host} -p {self.port}"
        if self.password:
            cmd += f" -a {self.password}"
        if self.db != 0:
            cmd += f" -n {self.db}"
        cmd += " " + " ".join(args)
        return cmd

    def get_metrics(self) -> Metrics:
        """
        获取 Redis 性能指标

        Returns:
            Metrics: 性能指标对象
        """
        try:
            # 获取 INFO 统计
            info = self._get_info()

            qps = float(info.get('instantaneous_ops_per_sec', 0))
            avg_latency = float(info.get('avg_cmd_latency_us', 0)) / 1000  # us to ms
            p99_latency = avg_latency * 2  # 估算
            error_rate = self._get_error_rate(info)

            return Metrics(
                qps=qps,
                avg_latency=avg_latency,
                p50_latency=avg_latency * 0.8,
                p99_latency=p99_latency,
                error_rate=error_rate,
            )
        except Exception:
            return self._error_metrics()

    def _get_info(self) -> Dict[str, str]:
        """获取 Redis INFO"""
        try:
            result = self.run_command(
                self._get_redis_cli("INFO", "stats"),
                timeout=10,
            )
            output = result.stdout

            info = {}
            for line in output.split('\n'):
                if ':' in line and not line.startswith('#'):
                    key, value = line.strip().split(':', 1)
                    info[key] = value

            return info
        except Exception:
            return {}

    def _get_error_rate(self, info: Dict[str, str]) -> float:
        """计算错误率"""
        try:
            total_commands = int(info.get('total_commands_processed', 0))
            rejected_commands = int(info.get('rejected_commands', 0))
            if total_commands > 0:
                return rejected_commands / total_commands
        except Exception:
            pass
        return 0.0

    def _error_metrics(self) -> Metrics:
        """创建错误状态指标"""
        return Metrics(
            qps=0.0,
            avg_latency=0.0,
            p50_latency=0.0,
            p99_latency=0.0,
            error_rate=1.0,
        )

    def run_benchmark(
        self,
        requests: int = 100000,
        clients: int = 50,
        threads: int = 4,
        keyspace_size: int = 10000,
    ) -> Metrics:
        """
        运行 redis-benchmark

        Args:
            requests: 总请求数
            clients: 客户端数量
            threads: 线程数
            keyspace_size: 键空间大小

        Returns:
            Metrics: 性能指标
        """
        try:
            cmd = (
                f"redis-benchmark -h {self.host} -p {self.port}"
                + (f" -a {self.password}" if self.password else "")
                + f" -n {requests} -c {clients} -t set,get"
                + f" -r {keyspace_size}"
                + f" --threads {threads}"
            )

            result = self.run_command(cmd, timeout=300)
            output = result.stdout + result.stderr

            return self._parse_benchmark_output(output)
        except Exception:
            return self._error_metrics()

    def _parse_benchmark_output(self, output: str) -> Metrics:
        """解析 redis-benchmark 输出"""
        metrics = {}

        # 解析 SET 操作
        set_match = re.search(
            r'SET'
            r'.*?(\d+\.?\d*)\s+requests\s+in\s+(\d+\.?\d*)\s+s',
            output,
            re.IGNORECASE
        )

        # 解析 GET 操作
        get_match = re.search(
            r'GET'
            r'.*?(\d+\.?\d*)\s+requests\s+in\s+(\d+\.?\d*)\s+s',
            output,
            re.IGNORECASE
        )

        # 解析延迟
        latency_pattern = r'(?:Latency|Avg)\s*[:\-]?\s*([\d.]+)\s*(?:ms|us|microseconds)?'
        latency_match = re.search(latency_pattern, output, re.IGNORECASE)

        # 解析 P99
        p99_pattern = r'99th\s+percentile\s*[:\-]?\s*([\d.]+)'
        p99_match = re.search(p99_pattern, output, re.IGNORECASE)

        qps = 0.0
        if set_match:
            requests = float(set_match.group(1))
            duration = float(set_match.group(2))
            qps = requests / duration if duration > 0 else 0.0

        if get_match and qps == 0:
            requests = float(get_match.group(1))
            duration = float(get_match.group(2))
            qps = requests / duration if duration > 0 else 0.0

        avg_latency = 0.0
        if latency_match:
            avg_latency = float(latency_match.group(1))
            # 如果是微秒，转换为毫秒
            if 'us' in latency_match.group(0).lower() or 'micro' in latency_match.group(0).lower():
                avg_latency /= 1000

        p99_latency = avg_latency * 2
        if p99_match:
            p99_latency = float(p99_match.group(1))
            if 'us' in p99_match.group(0).lower():
                p99_latency /= 1000

        return Metrics(
            qps=qps,
            avg_latency=avg_latency,
            p50_latency=avg_latency * 0.8,
            p99_latency=p99_latency,
            error_rate=0.0,
        )

    def get_memory_info(self) -> Dict[str, Any]:
        """
        获取内存信息

        Returns:
            内存信息字典
        """
        info = self._get_info()

        return {
            "used_memory": int(info.get('used_memory', 0)),
            "used_memory_human": info.get('used_memory_human', '0B'),
            "maxmemory": info.get('maxmemory', '0'),
            "mem_fragmentation_ratio": float(info.get('mem_fragmentation_ratio', 0)),
            "total_connections_received": int(info.get('total_connections_received', 0)),
            "connected_clients": int(info.get('connected_clients', 0)),
        }

    def get_replication_info(self) -> Dict[str, Any]:
        """
        获取复制信息

        Returns:
            复制信息字典
        """
        try:
            result = self.run_command(
                self._get_redis_cli("INFO", "replication"),
                timeout=10,
            )
            output = result.stdout

            info = {}
            for line in output.split('\n'):
                if ':' in line and not line.startswith('#'):
                    key, value = line.strip().split(':', 1)
                    info[key] = value

            return {
                "role": info.get('role', 'unknown'),
                "master_link_status": info.get('master_link_status', 'down'),
                "repl_backlog_active": info.get('repl_backlog_active', '0'),
                "connected_slaves": int(info.get('connected_slaves', 0)),
            }
        except Exception:
            return {"error": "获取复制信息失败"}

    def get_persistence_info(self) -> Dict[str, Any]:
        """
        获取持久化信息

        Returns:
            持久化信息字典
        """
        try:
            result = self.run_command(
                self._get_redis_cli("INFO", "persistence"),
                timeout=10,
            )
            output = result.stdout

            info = {}
            for line in output.split('\n'):
                if ':' in line and not line.startswith('#'):
                    key, value = line.strip().split(':', 1)
                    info[key] = value

            return {
                "loading": info.get('loading', '0'),
                "rdb_changes_since_last_save": info.get('rdb_changes_since_last_save', '0'),
                "aof_enabled": info.get('aof_enabled', '0'),
                "aof_last_write_status": info.get('aof_last_write_status', 'ok'),
            }
        except Exception:
            return {"error": "获取持久化信息失败"}

    def ping(self) -> bool:
        """
        测试 Redis 连接

        Returns:
            是否连接成功
        """
        try:
            result = self.run_command(
                self._get_redis_cli("PING"),
                timeout=5,
            )
            return "PONG" in result.stdout
        except Exception:
            return False
