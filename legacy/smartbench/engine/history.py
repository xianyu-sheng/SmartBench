"""
Benchmark History Database

存储和管理压测历史数据，支持趋势分析和增量对比。
"""

import json
import hashlib
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict


@dataclass
class BenchmarkRecord:
    """单次压测记录"""
    timestamp: str
    system: str
    target_qps: float
    actual_qps: float
    avg_latency: float
    p99_latency: float
    error_rate: float
    threads: int
    ops: int
    suggestions: List[Dict] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BenchmarkRecord":
        return cls(**data)

    @property
    def qps_gap_percent(self) -> float:
        """QPS 差距百分比"""
        if self.target_qps <= 0:
            return 0
        return ((self.target_qps - self.actual_qps) / self.target_qps) * 100

    @property
    def success(self) -> bool:
        """压测是否成功（QPS 达标且错误率低）"""
        return (
            self.actual_qps >= self.target_qps * 0.9 and
            self.error_rate < 0.05
        )


@dataclass
class TrendAnalysis:
    """趋势分析结果"""
    metric_name: str
    values: List[float]
    timestamps: List[str]
    trend: str  # "increasing", "decreasing", "stable"
    change_percent: float
    avg_value: float
    min_value: float
    max_value: float


class BenchmarkHistoryDB:
    """
    压测历史数据库

    功能：
    1. 存储每次压测的完整数据
    2. 支持按时间范围查询
    3. 趋势分析（QPS、延迟变化）
    4. 建议采纳率跟踪
    5. 缓存分析结果避免重复 API 调用
    """

    def __init__(self, db_path: str = "./data/benchmark_history.json"):
        """
        初始化历史数据库

        Args:
            db_path: 数据库文件路径
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._records: List[BenchmarkRecord] = []
        self._load()

        # 分析缓存：基于 metrics hash 缓存分析结果
        self._analysis_cache: Dict[str, Dict[str, Any]] = {}
        self._cache_ttl = 3600  # 缓存有效期 1 小时

    def add_record(self, record: BenchmarkRecord) -> str:
        """
        添加压测记录

        Args:
            record: 压测记录

        Returns:
            记录的 ID
        """
        # 生成唯一 ID
        record_id = self._generate_record_id(record)
        record.id = record_id

        self._records.append(record)
        self._save()
        return record_id

    def _generate_record_id(self, record: BenchmarkRecord) -> str:
        """生成记录 ID"""
        content = f"{record.timestamp}:{record.actual_qps}:{record.target_qps}"
        return hashlib.md5(content.encode()).hexdigest()[:12]

    def get_recent(self, count: int = 10) -> List[BenchmarkRecord]:
        """获取最近的 N 条记录"""
        sorted_records = sorted(
            self._records,
            key=lambda r: r.timestamp,
            reverse=True
        )
        return sorted_records[:count]

    def get_by_timerange(
        self,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None
    ) -> List[BenchmarkRecord]:
        """按时间范围查询"""
        results = self._records

        if start:
            results = [
                r for r in results
                if datetime.fromisoformat(r.timestamp) >= start
            ]

        if end:
            results = [
                r for r in results
                if datetime.fromisoformat(r.timestamp) <= end
            ]

        return sorted(results, key=lambda r: r.timestamp)

    def get_trends(
        self,
        metric: str = "actual_qps",
        days: int = 7
    ) -> TrendAnalysis:
        """
        获取指标趋势分析

        Args:
            metric: 指标名（actual_qps, avg_latency, p99_latency, error_rate）
            days: 分析天数

        Returns:
            趋势分析结果
        """
        cutoff = datetime.now() - timedelta(days=days)
        records = self.get_by_timerange(start=cutoff)

        if not records:
            return TrendAnalysis(
                metric_name=metric,
                values=[],
                timestamps=[],
                trend="no_data",
                change_percent=0,
                avg_value=0,
                min_value=0,
                max_value=0,
            )

        # 提取值
        values = []
        timestamps = []
        for r in sorted(records, key=lambda x: x.timestamp):
            val = getattr(r, metric, 0)
            if val is not None:
                values.append(val)
                timestamps.append(r.timestamp)

        if len(values) < 2:
            return TrendAnalysis(
                metric_name=metric,
                values=values,
                timestamps=timestamps,
                trend="insufficient_data",
                change_percent=0,
                avg_value=values[0] if values else 0,
                min_value=values[0] if values else 0,
                max_value=values[0] if values else 0,
            )

        # 计算趋势
        first_half = values[:len(values)//2]
        second_half = values[len(values)//2:]
        first_avg = sum(first_half) / len(first_half)
        second_avg = sum(second_half) / len(second_half)

        if second_avg > first_avg * 1.05:
            trend = "increasing"
        elif second_avg < first_avg * 0.95:
            trend = "decreasing"
        else:
            trend = "stable"

        change = ((second_avg - first_avg) / first_avg * 100) if first_avg > 0 else 0

        return TrendAnalysis(
            metric_name=metric,
            values=values,
            timestamps=timestamps,
            trend=trend,
            change_percent=change,
            avg_value=sum(values) / len(values),
            min_value=min(values),
            max_value=max(values),
        )

    def get_suggestion_stats(self) -> Dict[str, Any]:
        """获取建议采纳统计"""
        total_suggestions = 0
        adopted = 0
        by_priority = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
        by_risk = {"low": 0, "medium": 0, "high": 0}

        for record in self._records:
            for suggestion in record.suggestions:
                total_suggestions += 1
                if suggestion.get("adopted", False):
                    adopted += 1
                priority = suggestion.get("priority", 3)
                if priority in by_priority:
                    by_priority[priority] += 1
                risk = suggestion.get("risk_level", "medium")
                if risk in by_risk:
                    by_risk[risk] += 1

        return {
            "total_suggestions": total_suggestions,
            "adopted": adopted,
            "adoption_rate": adopted / total_suggestions if total_suggestions > 0 else 0,
            "by_priority": by_priority,
            "by_risk": by_risk,
        }

    def get_best_result(self, metric: str = "actual_qps") -> Optional[BenchmarkRecord]:
        """获取最佳结果"""
        if not self._records:
            return None
        return max(self._records, key=lambda r: getattr(r, metric, 0))

    def get_summary(self) -> Dict[str, Any]:
        """获取汇总统计"""
        if not self._records:
            return {
                "total_records": 0,
                "success_rate": 0,
                "avg_qps": 0,
                "avg_latency": 0,
            }

        successful = [r for r in self._records if r.success]

        return {
            "total_records": len(self._records),
            "successful_records": len(successful),
            "success_rate": len(successful) / len(self._records),
            "avg_qps": sum(r.actual_qps for r in self._records) / len(self._records),
            "avg_latency": sum(r.avg_latency for r in self._records) / len(self._records),
            "avg_p99": sum(r.p99_latency for r in self._records) / len(self._records),
            "best_qps": max(r.actual_qps for r in self._records),
            "latest_record": self._records[-1].timestamp if self._records else None,
        }

    # ==================== 分析缓存 ====================

    def get_cached_analysis(self, metrics_hash: str) -> Optional[Dict[str, Any]]:
        """
        获取缓存的分析结果

        Args:
            metrics_hash: 指标数据的 hash

        Returns:
            缓存的分析结果或 None
        """
        if metrics_hash in self._analysis_cache:
            cached = self._analysis_cache[metrics_hash]
            cached_time = cached.get("_cached_at", 0)
            if datetime.now().timestamp() - cached_time < self._cache_ttl:
                return cached.get("result")
        return None

    def cache_analysis(self, metrics_hash: str, result: Dict[str, Any]):
        """
        缓存分析结果

        Args:
            metrics_hash: 指标数据的 hash
            result: 分析结果
        """
        self._analysis_cache[metrics_hash] = {
            "result": result,
            "_cached_at": datetime.now().timestamp(),
        }

    def generate_metrics_hash(self, metrics: Dict[str, Any]) -> str:
        """生成指标的 hash"""
        # 只取关键指标
        key_metrics = {
            "qps": metrics.get("qps", 0),
            "avg_latency": metrics.get("avg_latency", 0),
            "p99_latency": metrics.get("p99_latency", 0),
            "error_rate": metrics.get("error_rate", 0),
        }
        content = json.dumps(key_metrics, sort_keys=True)
        return hashlib.md5(content.encode()).hexdigest()

    # ==================== 持久化 ====================

    def _load(self):
        """加载数据库"""
        if self.db_path.exists():
            try:
                with open(self.db_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self._records = [
                        BenchmarkRecord.from_dict(r)
                        for r in data.get("records", [])
                    ]
            except (json.JSONDecodeError, IOError):
                self._records = []

    def _save(self):
        """保存数据库"""
        try:
            with open(self.db_path, 'w', encoding='utf-8') as f:
                json.dump({
                    "records": [r.to_dict() for r in self._records],
                    "updated_at": datetime.now().isoformat(),
                }, f, ensure_ascii=False, indent=2)
        except IOError:
            pass

    def export_csv(self, path: str = "./data/benchmark_history.csv"):
        """导出为 CSV 格式"""
        import csv

        with open(path, 'w', newline='', encoding='utf-8') as f:
            if not self._records:
                return

            fieldnames = [
                "timestamp", "system", "target_qps", "actual_qps",
                "avg_latency", "p99_latency", "error_rate",
                "threads", "ops", "success"
            ]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            for r in self._records:
                writer.writerow({
                    "timestamp": r.timestamp,
                    "system": r.system,
                    "target_qps": r.target_qps,
                    "actual_qps": r.actual_qps,
                    "avg_latency": r.avg_latency,
                    "p99_latency": r.p99_latency,
                    "error_rate": r.error_rate,
                    "threads": r.threads,
                    "ops": r.ops,
                    "success": r.success,
                })
