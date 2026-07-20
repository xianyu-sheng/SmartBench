"""
数据类型定义模块

定义 SmartBench 系统中使用的所有数据结构。
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Literal
from enum import Enum
from datetime import datetime


class RiskLevel(Enum):
    """
    风险等级枚举
    
    用于评估优化建议的实施风险:
    - LOW: 低风险，影响范围小，可快速实施
    - MEDIUM: 中风险，需要充分测试
    - HIGH: 高风险，可能影响系统稳定性
    """
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SystemType(Enum):
    """
    系统类型枚举
    
    定义支持的被测系统类型。
    """
    GENERIC = "generic"           # 通用系统（自定义解析器）
    RAFT_KV = "raft_kv"          # Raft KV 存储
    HTTP_API = "http_api"        # HTTP API 服务
    DATABASE = "database"         # 数据库


class ModelProvider(Enum):
    """
    模型提供商枚举
    
    定义支持的 AI 模型提供商。
    """
    OPENAI_COMPATIBLE = "openai_compatible"  # OpenAI 兼容接口
    ANTHROPIC = "anthropic"                  # Anthropic (Claude)
    DASHSCOPE = "dashscope"                   # 阿里云 DashScope


@dataclass
class Metrics:
    """
    性能指标数据类
    
    存储压测得到的各项性能指标。
    
    Attributes:
        qps: 每秒查询数 (Queries Per Second)
        avg_latency: 平均延迟 (毫秒)
        p50_latency: P50 延迟 (毫秒)
        p99_latency: P99 延迟 (毫秒)
        error_rate: 错误率 (0-1)
        timestamp: 采集时间
    """
    qps: float
    avg_latency: float
    p50_latency: float = 0.0
    p99_latency: float = 0.0
    error_rate: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)
    
    def is_healthy(self) -> bool:
        """判断性能是否健康（错误率 < 1%）"""
        return self.error_rate < 0.01
    
    def gap_to_target(self, target_qps: float) -> float:
        """计算与目标 QPS 的差距百分比"""
        if target_qps <= 0:
            return 0.0
        return (target_qps - self.qps) / target_qps * 100


@dataclass
class Suggestion:
    """
    优化建议数据类
    
    存储一条优化建议的完整信息。
    
    Attributes:
        title: 建议标题
        description: 问题分析描述
        pseudocode: 伪代码实现
        priority: 优先级 (1-5, 5 为最高)
        risk_level: 风险等级
        expected_gain: 预期收益描述
        implementation_steps: 实施步骤列表
        source_model: 来源模型名称
        self_confidence: 自评置信度 (0-1)
        base_weight: 基础权重
        final_weight: 最终权重（计算后填充）
    """
    title: str
    description: str
    pseudocode: str
    priority: int
    risk_level: RiskLevel
    expected_gain: str
    implementation_steps: List[str] = field(default_factory=list)
    source_model: str = ""
    self_confidence: float = 0.5
    base_weight: float = 1.0
    final_weight: float = 0.0
    
    def __post_init__(self):
        """数据验证"""
        if not 1 <= self.priority <= 5:
            self.priority = 3
        if not 0 <= self.self_confidence <= 1:
            self.self_confidence = 0.5
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            "title": self.title,
            "description": self.description,
            "pseudocode": self.pseudocode,
            "priority": self.priority,
            "risk_level": self.risk_level.value,
            "expected_gain": self.expected_gain,
            "implementation_steps": self.implementation_steps,
            "source_model": self.source_model,
            "self_confidence": self.self_confidence,
            "final_weight": self.final_weight,
        }


@dataclass
class AnalysisContext:
    """
    分析上下文数据类
    
    封装传递给模型的分析所需的所有信息。
    
    Attributes:
        system_name: 系统名称
        system_type: 系统类型
        metrics: 性能指标
        logs: 日志内容
        source_code: 源码片段
        config: 配置信息
        target_qps: 目标 QPS
    """
    system_name: str
    system_type: SystemType
    metrics: Metrics
    logs: str = ""
    source_code: str = ""
    config: Dict[str, Any] = field(default_factory=dict)
    target_qps: float = 0.0
    
    def to_prompt_text(self) -> str:
        """转换为提示词文本"""
        lines = [
            f"# 性能分析上下文",
            f"",
            f"## 系统信息",
            f"- 系统名称: {self.system_name}",
            f"- 系统类型: {self.system_type.value}",
            f"- 目标 QPS: {self.target_qps}",
            f"",
            f"## 性能指标",
            f"- QPS: {self.metrics.qps:.1f}",
            f"- 平均延迟: {self.metrics.avg_latency:.1f}ms",
            f"- P50 延迟: {self.metrics.p50_latency:.1f}ms",
            f"- P99 延迟: {self.metrics.p99_latency:.1f}ms",
            f"- 错误率: {self.metrics.error_rate:.2%}",
            f"",
        ]
        
        if self.logs:
            lines.extend([
                f"## 日志片段（最近 100 行）",
                f"```",
                self.logs[-5000:] if len(self.logs) > 5000 else self.logs,
                f"```",
                f"",
            ])
        
        if self.source_code:
            lines.extend([
                f"## 关键代码片段",
                f"```cpp",
                self.source_code[:3000] if len(self.source_code) > 3000 else self.source_code,
                f"```",
                f"",
            ])
        
        return "\n".join(lines)


@dataclass
class AnalysisResult:
    """
    分析结果数据类
    
    存储单个模型的分析结果。
    
    Attributes:
        model_name: 模型名称
        suggestions: 建议列表
        raw_response: 原始响应内容
        processing_time: 处理时间（秒）
        error: 错误信息（如果有）
    """
    model_name: str
    suggestions: List[Suggestion] = field(default_factory=list)
    raw_response: str = ""
    processing_time: float = 0.0
    error: Optional[str] = None
    
    @property
    def is_success(self) -> bool:
        """判断是否成功"""
        return self.error is None and len(self.suggestions) > 0


@dataclass
class OptimizationReport:
    """
    优化报告数据类
    
    存储完整的优化分析报告。
    
    Attributes:
        timestamp: 生成时间
        target_system: 目标系统名称
        current_metrics: 当前性能指标
        target_qps: 目标 QPS
        suggestions: 优化建议列表
        summary: 总结摘要
        raw_data_path: 原始数据路径
        report_path: 报告文件路径
    """
    timestamp: datetime
    target_system: str
    current_metrics: Metrics
    target_qps: float
    suggestions: List[Suggestion] = field(default_factory=list)
    summary: str = ""
    raw_data_path: str = ""
    report_path: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            "timestamp": self.timestamp.isoformat(),
            "target_system": self.target_system,
            "target_qps": self.target_qps,
            "current_metrics": {
                "qps": self.current_metrics.qps,
                "avg_latency": self.current_metrics.avg_latency,
                "p99_latency": self.current_metrics.p99_latency,
                "error_rate": self.current_metrics.error_rate,
            },
            "summary": self.summary,
            "suggestions": [s.to_dict() for s in self.suggestions],
            "report_path": self.report_path,
            "raw_data_path": self.raw_data_path,
        }
