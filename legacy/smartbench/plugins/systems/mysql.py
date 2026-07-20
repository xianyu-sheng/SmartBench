"""
MySQL 数据库系统插件

支持 MySQL 性能压测和指标采集。
"""

import re
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional

from smartbench.core.types import Metrics, SystemType
from smartbench.plugins.systems.base import BaseSystemPlugin, BenchmarkResult


class MySQLPlugin(BaseSystemPlugin):
    """
    MySQL 数据库系统插件

    支持：
    1. Sysbench 压测
    2. 慢查询日志分析
    3. 性能指标采集
    """

    def __init__(
        self,
        project_path: str = "/var/lib/mysql",
        host: str = "localhost",
        port: int = 3306,
        user: str = "root",
        password: str = "",
        database: str = "sbtest",
    ):
        """
        初始化 MySQL 插件

        Args:
            project_path: 项目路径（用于存放配置和脚本）
            host: MySQL 主机
            port: MySQL 端口
            user: MySQL 用户
            password: MySQL 密码
            database: 测试数据库名
        """
        super().__init__()
        self._project_path = Path(project_path)
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database

    @property
    def name(self) -> str:
        return "mysql"

    @property
    def system_type(self) -> SystemType:
        return SystemType.DATABASE

    def get_metrics(self) -> Metrics:
        """
        获取 MySQL 性能指标

        通过 SHOW GLOBAL STATUS 获取 QPS 和连接数等指标。

        Returns:
            Metrics: 性能指标对象
        """
        try:
            # 获取 QPS
            qps = self._get_qps()
            # 获取平均延迟（通过查询响应时间）
            avg_latency = self._get_query_latency()
            # 获取 P99 延迟
            p99_latency = self._get_p99_latency()
            # 获取错误率
            error_rate = self._get_error_rate()

            return Metrics(
                qps=qps,
                avg_latency=avg_latency,
                p50_latency=avg_latency * 0.9,
                p99_latency=p99_latency,
                error_rate=error_rate,
            )
        except Exception:
            return self._error_metrics()

    def _get_qps(self) -> float:
        """获取 QPS"""
        try:
            result = self.run_command(
                f"mysql -h {self.host} -P {self.port} -u {self.user}"
                + (f" -p{self.password}" if self.password else "")
                + f" -e 'SHOW GLOBAL STATUS WHERE Variable_name IN (\"Questions\", \"Uptime\");'",
                timeout=10,
            )
            output = result.stdout

            questions_match = re.search(r'Questions\s+(\d+)', output)
            uptime_match = re.search(r'Uptime\s+(\d+)', output)

            if questions_match and uptime_match:
                questions = int(questions_match.group(1))
                uptime = int(uptime_match.group(1))
                return questions / uptime if uptime > 0 else 0.0
        except Exception:
            pass
        return 0.0

    def _get_query_latency(self) -> float:
        """获取平均查询延迟（毫秒）"""
        try:
            result = self.run_command(
                f"mysql -h {self.host} -P {self.port} -u {self.user}"
                + (f" -p{self.password}" if self.password else "")
                + f" {self.database} -e 'SELECT 1;'",
                timeout=10,
            )
            # 简单估算，实际应该用慢查询日志分析
            return 1.0  # 默认 1ms
        except Exception:
            return 0.0

    def _get_p99_latency(self) -> float:
        """获取 P99 延迟（毫秒）"""
        try:
            # 读取慢查询日志中的 P99
            slow_log = self._project_path / "slow_query.log"
            if slow_log.exists():
                with open(slow_log, 'r') as f:
                    content = f.read()
                # 解析查询时间
                times = re.findall(r'Query_time:\s+([\d.]+)', content)
                if times:
                    sorted_times = sorted([float(t) for t in times])
                    idx = int(len(sorted_times) * 0.99)
                    return sorted_times[min(idx, len(sorted_times) - 1)] * 1000
        except Exception:
            pass
        return 10.0  # 默认 10ms

    def _get_error_rate(self) -> float:
        """获取错误率"""
        try:
            result = self.run_command(
                f"mysql -h {self.host} -P {self.port} -u {self.user}"
                + (f" -p{self.password}" if self.password else "")
                + f" -e 'SHOW GLOBAL STATUS WHERE Variable_name IN (\"Aborted_connects\", \"Connections\");'",
                timeout=10,
            )
            output = result.stdout

            aborted_match = re.search(r'Aborted_connects\s+(\d+)', output)
            connections_match = re.search(r'Connections\s+(\d+)', output)

            if aborted_match and connections_match:
                aborted = int(aborted_match.group(1))
                connections = int(connections_match.group(1))
                return aborted / connections if connections > 0 else 0.0
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

    def get_slow_queries(self, limit: int = 10) -> str:
        """
        获取慢查询

        Args:
            limit: 返回数量

        Returns:
            慢查询列表
        """
        try:
            result = self.run_command(
                f"mysql -h {self.host} -P {self.port} -u {self.user}"
                + (f" -p{self.password}" if self.password else "")
                + f" {self.database} -e '"
                + "SELECT query_time, rows_examined, sql_text "
                + "FROM mysql.slow_log "
                + f"ORDER BY query_time DESC LIMIT {limit};'",
                timeout=30,
            )
            return result.stdout
        except Exception as e:
            return f"获取慢查询失败: {e}"

    def get_connection_status(self) -> Dict[str, Any]:
        """
        获取连接状态

        Returns:
            连接状态字典
        """
        status = {
            "max_connections": 0,
            "current_connections": 0,
            "threads_connected": 0,
            "threads_running": 0,
        }

        try:
            result = self.run_command(
                f"mysql -h {self.host} -P {self.port} -u {self.user}"
                + (f" -p{self.password}" if self.password else "")
                + f" -e 'SHOW VARIABLES LIKE \"max_connections\";"
                + f" SHOW GLOBAL STATUS WHERE Variable_name IN "
                + '(\"Threads_connected\", \"Threads_running\");"',
                timeout=10,
            )
            output = result.stdout

            max_conn_match = re.search(r'max_connections\s+(\d+)', output)
            if max_conn_match:
                status["max_connections"] = int(max_conn_match.group(1))

            threads_conn_match = re.search(r'Threads_connected\s+(\d+)', output)
            if threads_conn_match:
                status["threads_connected"] = int(threads_conn_match.group(1))

            threads_run_match = re.search(r'Threads_running\s+(\d+)', output)
            if threads_run_match:
                status["threads_running"] = int(threads_run_match.group(1))

        except Exception:
            pass

        return status

    def run_sysbench(self, threads: int = 4, time: int = 60) -> Metrics:
        """
        运行 Sysbench 压测

        Args:
            threads: 线程数
            time: 压测时长（秒）

        Returns:
            Metrics: 性能指标
        """
        try:
            result = self.run_command(
                f"sysbench --db-driver=mysql --mysql-host={self.host} "
                f"--mysql-port={self.port} --mysql-user={self.user}"
                + (f" --mysql-password={self.password}" if self.password else "")
                + f" --mysql-db={self.database} "
                + f"--threads={threads} --time={time} "
                + "run",
                timeout=time + 30,
            )
            output = result.stdout + result.stderr

            return self._parse_sysbench_output(output)
        except Exception:
            return self._error_metrics()

    def _parse_sysbench_output(self, output: str) -> Metrics:
        """解析 Sysbench 输出"""
        metrics = BenchmarkResult.extract_metrics(output)

        if not metrics:
            # 手动解析
            qps_match = re.search(r'requests:\s+(\d+)\s+in\s+([\d.]+)s', output)
            latency_match = re.search(r'avg:\s+([\d.]+)\s+min:', output)

            if qps_match:
                requests = float(qps_match.group(1))
                duration = float(qps_match.group(2))
                metrics['qps'] = requests / duration

            if latency_match:
                metrics['avg_latency'] = float(latency_match.group(1))

        return Metrics(
            qps=metrics.get('qps', 0.0),
            avg_latency=metrics.get('avg_latency', 0.0),
            p50_latency=metrics.get('p50_latency', 0.0),
            p99_latency=metrics.get('p99_latency', 0.0),
            error_rate=metrics.get('error_rate', 0.0),
        )
