"""
性能回归对比模块

功能：
1. 记录每次压测结果
2. 对比优化前后的 QPS、延迟
3. 检测性能回归
4. 生成趋势报告
"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from enum import Enum


class PerformanceTrend(Enum):
    """性能趋势"""
    IMPROVING = "improving"
    STABLE = "stable"
    DEGRADING = "degrading"
    UNKNOWN = "unknown"


@dataclass
class PerformanceSnapshot:
    """性能快照"""
    timestamp: str
    qps: float
    avg_latency: float
    p99_latency: float
    error_rate: float
    target_qps: float
    notes: str = ""


@dataclass
class RegressionResult:
    """回归检测结果"""
    has_regression: bool
    qps_change: float  # 百分比变化
    latency_change: float
    error_rate_change: float
    severity: str  # "none", "minor", "moderate", "severe"
    details: str


@dataclass
class TrendAnalysis:
    """趋势分析"""
    metric: str
    current: float
    previous: float
    change_percent: float
    trend: PerformanceTrend
    data_points: List[float]


class PerformanceRegression:
    """
    性能回归检测器

    功能：
    1. 记录性能快照
    2. 对比历史数据
    3. 检测性能回归
    4. 生成趋势分析
    """

    def __init__(self, data_dir: str = "./data/regression"):
        """
        初始化回归检测器

        Args:
            data_dir: 数据存储目录
        """
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.history_file = self.data_dir / "history.json"
        self.snapshots: List[PerformanceSnapshot] = self._load_history()

    def _load_history(self) -> List[PerformanceSnapshot]:
        """加载历史数据"""
        if self.history_file.exists():
            try:
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return [PerformanceSnapshot(**s) for s in data]
            except Exception:
                pass
        return []

    def _save_history(self):
        """保存历史数据"""
        try:
            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump([asdict(s) for s in self.snapshots], f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def record_snapshot(
        self,
        qps: float,
        avg_latency: float,
        p99_latency: float,
        error_rate: float,
        target_qps: float,
        notes: str = "",
    ) -> str:
        """
        记录性能快照

        Args:
            qps: QPS
            avg_latency: 平均延迟
            p99_latency: P99 延迟
            error_rate: 错误率
            target_qps: 目标 QPS
            notes: 备注

        Returns:
            快照 ID
        """
        snapshot = PerformanceSnapshot(
            timestamp=datetime.now().isoformat(),
            qps=qps,
            avg_latency=avg_latency,
            p99_latency=p99_latency,
            error_rate=error_rate,
            target_qps=target_qps,
            notes=notes,
        )

        self.snapshots.append(snapshot)
        self._save_history()

        return snapshot.timestamp

    def get_latest(self, count: int = 5) -> List[PerformanceSnapshot]:
        """获取最近的快照"""
        return sorted(self.snapshots, key=lambda s: s.timestamp, reverse=True)[:count]

    def get_baseline(self) -> Optional[PerformanceSnapshot]:
        """获取基准快照（最早的）"""
        if not self.snapshots:
            return None
        return min(self.snapshots, key=lambda s: s.timestamp)

    def compare(
        self,
        current: PerformanceSnapshot,
        baseline: Optional[PerformanceSnapshot] = None,
    ) -> RegressionResult:
        """
        对比性能变化

        Args:
            current: 当前快照
            baseline: 基准快照（None 表示与上次对比）

        Returns:
            回归检测结果
        """
        if baseline is None:
            # 与上次对比
            if len(self.snapshots) < 2:
                return RegressionResult(
                    has_regression=False,
                    qps_change=0,
                    latency_change=0,
                    error_rate_change=0,
                    severity="none",
                    details="Not enough data for comparison",
                )
            # 找到 current 之前的快照
            sorted_snaps = sorted(self.snapshots, key=lambda s: s.timestamp)
            idx = sorted_snaps.index(current)
            if idx > 0:
                baseline = sorted_snaps[idx - 1]
            else:
                return RegressionResult(
                    has_regression=False,
                    qps_change=0,
                    latency_change=0,
                    error_rate_change=0,
                    severity="none",
                    details="No previous snapshot",
                )

        # 计算变化
        qps_change = ((current.qps - baseline.qps) / baseline.qps * 100) if baseline.qps > 0 else 0
        latency_change = ((current.avg_latency - baseline.avg_latency) / baseline.avg_latency * 100) if baseline.avg_latency > 0 else 0
        error_change = current.error_rate - baseline.error_rate

        # 判断是否回归
        has_regression = qps_change < -5 or error_change > 0.01  # QPS 下降 5% 或错误率上升 1%

        # 判断严重程度
        if qps_change < -20 or error_change > 0.05:
            severity = "severe"
        elif qps_change < -10 or error_change > 0.02:
            severity = "moderate"
        elif qps_change < -5 or error_change > 0.01:
            severity = "minor"
        else:
            severity = "none"

        details = f"QPS 变化: {qps_change:+.1f}%, 延迟变化: {latency_change:+.1f}%, 错误率变化: {error_change:+.2%}"

        return RegressionResult(
            has_regression=has_regression,
            qps_change=qps_change,
            latency_change=latency_change,
            error_rate_change=error_change,
            severity=severity,
            details=details,
        )

    def analyze_trend(
        self,
        metric: str = "qps",
        days: int = 7,
    ) -> TrendAnalysis:
        """
        分析性能趋势

        Args:
            metric: 指标名（qps, avg_latency, p99_latency, error_rate）
            days: 分析天数

        Returns:
            趋势分析结果
        """
        cutoff = datetime.now() - timedelta(days=days)

        filtered = [
            s for s in self.snapshots
            if datetime.fromisoformat(s.timestamp) >= cutoff
        ]

        if len(filtered) < 2:
            return TrendAnalysis(
                metric=metric,
                current=0,
                previous=0,
                change_percent=0,
                trend=PerformanceTrend.UNKNOWN,
                data_points=[],
            )

        # 获取数据点
        metric_map = {
            "qps": lambda s: s.qps,
            "avg_latency": lambda s: s.avg_latency,
            "p99_latency": lambda s: s.p99_latency,
            "error_rate": lambda s: s.error_rate,
        }

        get_value = metric_map.get(metric, metric_map["qps"])
        data_points = [get_value(s) for s in sorted(filtered, key=lambda s: s.timestamp)]

        current = data_points[-1]
        previous = data_points[0]

        change_percent = ((current - previous) / previous * 100) if previous > 0 else 0

        # 判断趋势
        if change_percent > 5:
            trend = PerformanceTrend.IMPROVING
        elif change_percent < -5:
            trend = PerformanceTrend.DEGRADING
        else:
            trend = PerformanceTrend.STABLE

        return TrendAnalysis(
            metric=metric,
            current=current,
            previous=previous,
            change_percent=change_percent,
            trend=trend,
            data_points=data_points,
        )

    def generate_report(
        self,
        current_snapshot: PerformanceSnapshot,
    ) -> str:
        """
        生成性能报告

        Args:
            current_snapshot: 当前快照

        Returns:
            报告文本
        """
        lines = []
        lines.append("=" * 50)
        lines.append("📊 性能报告")
        lines.append("=" * 50)
        lines.append("")

        # 当前状态
        lines.append("当前状态:")
        lines.append(f"  QPS: {current_snapshot.qps:.1f}")
        lines.append(f"  平均延迟: {current_snapshot.avg_latency:.2f} ms")
        lines.append(f"  P99 延迟: {current_snapshot.p99_latency:.2f} ms")
        lines.append(f"  错误率: {current_snapshot.error_rate:.2%}")
        lines.append(f"  目标 QPS: {current_snapshot.target_qps}")
        lines.append("")

        # 与上次对比
        if len(self.snapshots) > 1:
            comparison = self.compare(current_snapshot)
            lines.append("与上次对比:")
            lines.append(f"  {comparison.details}")
            if comparison.has_regression:
                lines.append(f"  ⚠️ 检测到性能回归 (严重程度: {comparison.severity})")
            else:
                lines.append("  ✅ 性能稳定")
            lines.append("")

        # 趋势分析
        for metric in ["qps", "avg_latency"]:
            trend = self.analyze_trend(metric)
            if trend.trend != PerformanceTrend.UNKNOWN:
                trend_icon = "📈" if trend.trend == PerformanceTrend.IMPROVING else "📉" if trend.trend == PerformanceTrend.DEGRADING else "➡️"
                lines.append(f"{trend_icon} {metric.upper()} 趋势 ({trend.change_percent:+.1f}%): {trend.trend.value}")
        lines.append("")

        # 历史快照
        recent = self.get_latest(5)
        if len(recent) > 1:
            lines.append("最近 5 次测试:")
            lines.append(f"{'时间':<25} {'QPS':>10} {'延迟':>10} {'错误率':>10}")
            lines.append("-" * 55)
            for s in sorted(recent, key=lambda x: x.timestamp):
                time_str = s.timestamp[:19].replace('T', ' ')
                lines.append(f"{time_str:<25} {s.qps:>10.1f} {s.avg_latency:>9.2f}ms {s.error_rate:>9.2%}")

        lines.append("")
        lines.append("=" * 50)

        return '\n'.join(lines)


# 全局实例
_global_regression: Optional[PerformanceRegression] = None


def get_regression_engine(data_dir: str = "./data/regression") -> PerformanceRegression:
    """获取回归检测器实例"""
    global _global_regression
    if _global_regression is None:
        _global_regression = PerformanceRegression(data_dir)
    return _global_regression
