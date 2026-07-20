"""
Raft KV 专业分析报告生成器

针对 Raft KV 存储系统的深度分析报告生成。
"""

import json
import re
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, Any, Tuple
from dataclasses import dataclass

from smartbench.core.types import Suggestion, Metrics, RiskLevel


@dataclass
class CodeLocation:
    """代码位置信息"""
    file: str
    line_start: int
    line_end: int
    function: str = ""
    description: str = ""


@dataclass
class RaftAnalysisContext:
    """Raft KV 分析上下文"""
    metrics: Metrics
    logs: str = ""
    source_code: Dict[str, str] = None
    config: Dict[str, str] = None
    system_info: Dict[str, Any] = None


class RaftKVReportGenerator:
    """
    Raft KV 专业分析报告生成器

    功能：
    1. 生成包含具体代码位置的分析报告
    2. 基于 Raft KV 系统架构的专项分析
    3. 可执行的实施建议
    4. 历史趋势对比
    """

    # Raft KV 关键文件路径
    KEY_FILES = {
        "raft_core": "Raft/Raft.cpp",
        "raft_header": "Raft/Raft.h",
        "kv_server": "KvServer/KvServer.cpp",
        "clerk": "Clerk/clerk.cpp",
        "skiplist": "Skiplist-CPP/skiplist.h",
        "persister": "Raft/Persister.cpp",
        "rpc_channel": "myRPC/User/KrpcChannel.cc",
    }

    # 性能瓶颈关键词映射
    BOTTLENECK_PATTERNS = {
        "append_batch": {
            "keywords": ["batch", "AppendEntries", "send", "buffer"],
            "file": "raft_core",
            "suggestion": "批量追加优化",
            "risk": RiskLevel.MEDIUM,
        },
        "pipeline_window": {
            "keywords": ["pipeline", "window", "nextIndex", "in_flight"],
            "file": "raft_core",
            "suggestion": "Pipeline 滑动窗口调优",
            "risk": RiskLevel.MEDIUM,
        },
        "readindex": {
            "keywords": ["ReadIndex", "read", "linearizable", "lease"],
            "file": "raft_core",
            "suggestion": "ReadIndex 线性安全读优化",
            "risk": RiskLevel.LOW,
        },
        "persister": {
            "keywords": ["persist", "save", "fsync", "snapshot"],
            "file": "persister",
            "suggestion": "异步持久化优化",
            "risk": RiskLevel.MEDIUM,
        },
        "skiplist": {
            "keywords": ["skiplist", "search", "insert", "lock"],
            "file": "skiplist",
            "suggestion": "SkipList 读写优化",
            "risk": RiskLevel.LOW,
        },
        "rpc": {
            "keywords": ["rpc", "channel", "connect", "timeout"],
            "file": "rpc_channel",
            "suggestion": "RPC 连接池优化",
            "risk": RiskLevel.LOW,
        },
    }

    def __init__(self, project_path: str = "/home/xianyu-sheng/MyKV_storageBase_Raft_cpp"):
        self.project_path = Path(project_path)

    def generate_report(
        self,
        suggestions: List[Suggestion],
        metrics: Metrics,
        target_qps: float,
        context: Optional[RaftAnalysisContext] = None,
    ) -> Dict[str, Any]:
        """
        生成完整的 Raft KV 分析报告

        Args:
            suggestions: AI 模型生成的建议
            metrics: 当前性能指标
            target_qps: 目标 QPS
            context: 分析上下文（日志、源码等）

        Returns:
            报告字典
        """
        timestamp = datetime.now()

        # 基础指标分析
        metrics_analysis = self._analyze_metrics(metrics, target_qps)

        # 建议增强：添加代码位置
        enhanced_suggestions = self._enhance_suggestions(suggestions, context)

        # 按类别分组建议
        grouped = self._group_by_category(enhanced_suggestions)

        # 生成报告内容
        report_content = self._build_engineering_report(
            metrics=metrics,
            target_qps=target_qps,
            metrics_analysis=metrics_analysis,
            suggestions=enhanced_suggestions,
            grouped=grouped,
            context=context,
            timestamp=timestamp,
        )

        return {
            "timestamp": timestamp.isoformat(),
            "metrics": self._metrics_to_dict(metrics),
            "target_qps": target_qps,
            "metrics_analysis": metrics_analysis,
            "suggestions": [self._suggestion_to_dict(s) for s in enhanced_suggestions],
            "grouped_by_category": grouped,
            "report_content": report_content,
            "action_items": self._generate_action_items(enhanced_suggestions, metrics_analysis),
        }

    def _analyze_metrics(
        self,
        metrics: Metrics,
        target_qps: float,
    ) -> Dict[str, Any]:
        """分析指标，找出瓶颈点"""
        analysis = {
            "current_qps": metrics.qps,
            "target_qps": target_qps,
            "gap_percent": ((target_qps - metrics.qps) / target_qps * 100) if target_qps > 0 else 0,
            "is_achievable": metrics.qps >= target_qps * 0.9,
            "bottlenecks": [],
            "strengths": [],
        }

        # 分析 QPS
        if metrics.qps >= 350:
            analysis["strengths"].append(f"QPS 达到 {metrics.qps:.0f}，表现优秀")
        elif metrics.qps < 200:
            analysis["bottlenecks"].append("QPS 偏低，存在较大优化空间")

        # 分析延迟
        if metrics.avg_latency > 10:
            analysis["bottlenecks"].append(f"平均延迟 {metrics.avg_latency:.1f}ms 偏高")
        elif metrics.avg_latency < 5:
            analysis["strengths"].append(f"平均延迟 {metrics.avg_latency:.1f}ms 优秀")

        if metrics.p99_latency > 50:
            analysis["bottlenecks"].append(f"P99 延迟 {metrics.p99_latency:.1f}ms 较高，尾部延迟问题")
        elif metrics.p99_latency < 15:
            analysis["strengths"].append(f"P99 延迟 {metrics.p99_latency:.1f}ms 控制良好")

        # 分析错误率
        if metrics.error_rate > 0.05:
            analysis["bottlenecks"].append(f"错误率 {metrics.error_rate:.2%} 偏高")
        elif metrics.error_rate < 0.01:
            analysis["strengths"].append("错误率极低，系统稳定")

        # 综合评分
        score = 0
        if metrics.qps >= 300:
            score += 30
        elif metrics.qps >= 200:
            score += 20
        elif metrics.qps >= 100:
            score += 10

        if metrics.avg_latency <= 5:
            score += 25
        elif metrics.avg_latency <= 10:
            score += 15

        if metrics.p99_latency <= 20:
            score += 25
        elif metrics.p99_latency <= 50:
            score += 15

        if metrics.error_rate < 0.01:
            score += 20
        elif metrics.error_rate < 0.05:
            score += 10

        analysis["overall_score"] = score
        analysis["grade"] = "A" if score >= 80 else "B" if score >= 60 else "C" if score >= 40 else "D"

        return analysis

    def _enhance_suggestions(
        self,
        suggestions: List[Suggestion],
        context: Optional[RaftAnalysisContext],
    ) -> List[Suggestion]:
        """为建议添加代码位置和具体实现细节"""
        enhanced = []

        for s in suggestions:
            # 克隆建议
            enhanced_s = self._clone_suggestion(s)

            # 尝试匹配代码位置
            code_loc = self._find_code_location(s.title, s.description)
            if code_loc:
                enhanced_s.implementation_steps.extend([
                    f"文件位置: {code_loc.file}",
                    f"代码行数: {code_loc.line_start}-{code_loc.line_end}",
                ])

            # 添加 Raft KV 特定的上下文
            if context and context.source_code:
                related_code = self._find_related_code(s, context.source_code)
                if related_code:
                    enhanced_s.pseudocode = self._format_with_context(
                        enhanced_s.pseudocode,
                        related_code
                    )

            enhanced.append(enhanced_s)

        return enhanced

    def _find_code_location(self, title: str, description: str) -> Optional[CodeLocation]:
        """根据建议内容找到相关代码位置"""
        text = f"{title} {description}".lower()

        for pattern_name, pattern_info in self.BOTTLENECK_PATTERNS.items():
            if any(kw in text for kw in pattern_info["keywords"]):
                file_key = pattern_info["file"]
                file_path = self.KEY_FILES.get(file_key)
                if file_path and (self.project_path / file_path).exists():
                    lines = self._get_file_lines(self.project_path / file_path)
                    return CodeLocation(
                        file=file_path,
                        line_start=1,
                        line_end=len(lines),
                        description=f"相关文件: {pattern_info['suggestion']}",
                    )

        return None

    def _get_file_lines(self, path: Path) -> List[str]:
        """读取文件行"""
        try:
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                return f.readlines()
        except Exception:
            return []

    def _find_related_code(
        self,
        suggestion: Suggestion,
        source_code: Dict[str, str],
    ) -> Optional[str]:
        """找到相关的源码片段"""
        keywords = self._extract_keywords(suggestion.title + " " + suggestion.description)

        for file_path, content in source_code.items():
            for kw in keywords:
                if kw.lower() in content.lower():
                    # 找到包含关键词的部分
                    lines = content.split('\n')
                    for i, line in enumerate(lines):
                        if kw.lower() in line.lower():
                            start = max(0, i - 2)
                            end = min(len(lines), i + 5)
                            return '\n'.join(lines[start:end])

        return None

    def _extract_keywords(self, text: str) -> List[str]:
        """提取关键词"""
        # 提取技术术语
        technical_terms = [
            "AppendEntries", "Pipeline", "Batch", "nextIndex", "matchIndex",
            "ReadIndex", "Snapshot", "Persister", "SkipList", "Lock",
            "buffer", "channel", "RPC", "commit", "apply", "log",
            "raft", "leader", "follower", "candidate", "election",
        ]
        return [t for t in technical_terms if t.lower() in text.lower()]

    def _format_with_context(self, pseudocode: str, related_code: str) -> str:
        """格式化伪代码，加入相关源码"""
        formatted = f"// 相关源码片段:\n// {related_code.replace(chr(10), chr(10) + '// ')}\n\n"
        formatted += pseudocode
        return formatted

    def _clone_suggestion(self, s: Suggestion) -> Suggestion:
        """克隆建议对象"""
        return Suggestion(
            title=s.title,
            description=s.description,
            pseudocode=s.pseudocode,
            priority=s.priority,
            risk_level=s.risk_level,
            expected_gain=s.expected_gain,
            implementation_steps=list(s.implementation_steps),
            source_model=s.source_model,
            base_weight=s.base_weight,
            self_confidence=s.self_confidence,
        )

    def _group_by_category(self, suggestions: List[Suggestion]) -> Dict[str, List[Suggestion]]:
        """按类别分组建议"""
        categories = {
            "raft_protocol": [],    # Raft 协议层优化
            "storage": [],         # 存储层优化
            "rpc_network": [],     # 网络/RPC 优化
            "concurrency": [],     # 并发优化
            "other": [],           # 其他
        }

        for s in suggestions:
            text = f"{s.title} {s.description}".lower()

            if any(kw in text for kw in ["raft", "append", "pipeline", "leader", "follower", "election"]):
                categories["raft_protocol"].append(s)
            elif any(kw in text for kw in ["skiplist", "storage", "persist", "disk", "snapshot"]):
                categories["storage"].append(s)
            elif any(kw in text for kw in ["rpc", "network", "channel", "connection", "tcp"]):
                categories["rpc_network"].append(s)
            elif any(kw in text for kw in ["lock", "mutex", "thread", "concurrent", "parallel"]):
                categories["concurrency"].append(s)
            else:
                categories["other"].append(s)

        return categories

    def _build_engineering_report(
        self,
        metrics: Metrics,
        target_qps: float,
        metrics_analysis: Dict[str, Any],
        suggestions: List[Suggestion],
        grouped: Dict[str, List[Suggestion]],
        context: Optional[RaftAnalysisContext],
        timestamp: datetime,
    ) -> str:
        """构建工程化报告"""
        lines = [
            "# Raft KV 性能优化分析报告",
            "",
            f"**生成时间**: {timestamp.strftime('%Y-%m-%d %H:%M:%S')}",
            f"**系统**: Raft KV 分布式存储",
            "",
            "---",
            "",
            "## 一、性能指标分析",
            "",
            "| 指标 | 当前值 | 目标值 | 状态 |",
            "|:-----|:-------|:-------|:-----|",
            f"| QPS | {metrics.qps:.1f} | {target_qps} | {'✅ 达标' if metrics.qps >= target_qps else '❌ 未达标'} |",
            f"| 平均延迟 | {metrics.avg_latency:.2f} ms | < 5 ms | {'✅' if metrics.avg_latency < 5 else '⚠️'} |",
            f"| P99 延迟 | {metrics.p99_latency:.2f} ms | < 20 ms | {'✅' if metrics.p99_latency < 20 else '⚠️'} |",
            f"| 错误率 | {metrics.error_rate:.2%} | < 1% | {'✅' if metrics.error_rate < 0.01 else '⚠️'} |",
            f"| **综合评分** | **{metrics_analysis['overall_score']}/100** | | **{metrics_analysis['grade']}** |",
            "",
            "### 1.1 优势分析",
            "",
        ]

        for strength in metrics_analysis.get("strengths", []):
            lines.append(f"- {strength}")

        if not metrics_analysis.get("strengths"):
            lines.append("- 暂无明显优势")

        lines.extend([
            "",
            "### 1.2 瓶颈识别",
            "",
        ])

        for bottleneck in metrics_analysis.get("bottlenecks", []):
            lines.append(f"- ⚠️ {bottleneck}")

        if not metrics_analysis.get("bottlenecks"):
            lines.append("- 未发现明显瓶颈")

        # 优化建议
        lines.extend([
            "",
            "---",
            "",
            "## 二、优化建议（按优先级排序）",
            "",
        ])

        if not suggestions:
            lines.extend([
                "未生成有效建议，可能原因：",
                "1. 当前性能已接近系统极限",
                "2. 压测数据不足以支撑分析",
                "3. 建议提高目标 QPS 重新测试",
                "",
            ])
        else:
            for i, s in enumerate(suggestions, 1):
                risk_icon = "🟢" if s.risk_level == RiskLevel.LOW else "🟡" if s.risk_level == RiskLevel.MEDIUM else "🔴"
                lines.extend([
                    f"### 方案 {i}: {s.title}",
                    f"",
                    f"| 属性 | 值 |",
                    "|:-----|:---|",
                    f"| 优先级 | {'⭐' * s.priority} |",
                    f"| 风险等级 | {risk_icon} {s.risk_level.value} |",
                    f"| 预期收益 | {s.expected_gain} |",
                    f"| 置信度 | {s.self_confidence:.0%} |",
                    "",
                    "**问题分析**：",
                    s.description,
                    "",
                    "**伪代码实现**：",
                    "```cpp",
                    s.pseudocode if "```" not in s.pseudocode else s.pseudocode,
                    "```",
                    "",
                    "**实施步骤**：",
                ])
                for j, step in enumerate(s.implementation_steps, 1):
                    lines.append(f"{j}. {step}")
                lines.append("")

        # 分类优化建议
        lines.extend([
            "---",
            "",
            "## 三、分类优化建议",
            "",
        ])

        category_names = {
            "raft_protocol": "Raft 协议层",
            "storage": "存储引擎层",
            "rpc_network": "网络通信层",
            "concurrency": "并发控制层",
            "other": "其他",
        }

        for cat, cat_suggestions in grouped.items():
            if cat_suggestions:
                lines.append(f"### 3.{list(grouped.keys()).index(cat)+1} {category_names.get(cat, cat)}")
                lines.append("")
                for s in cat_suggestions[:3]:
                    lines.append(f"- **{s.title}** ({s.risk_level.value} 风险)")
                lines.append("")

        # 实施路线图
        lines.extend([
            "---",
            "",
            "## 四、实施路线图",
            "",
            "### 4.1 推荐实施顺序",
            "",
            "| 阶段 | 优化项 | 预计收益 | 风险 | 实施难度 |",
            "|:-----|:-------|:--------|:-----|:--------|",
        ])

        # 按风险和优先级排序
        priority_order = sorted(suggestions, key=lambda x: (x.risk_level.value == "low", -x.priority))
        for i, s in enumerate(priority_order[:5], 1):
            risk_color = "🟢低" if s.risk_level == RiskLevel.LOW else "🟡中" if s.risk_level == RiskLevel.MEDIUM else "🔴高"
            lines.append(f"| {i} | {s.title} | {s.expected_gain} | {risk_color} | {'⭐⭐' if s.priority >= 4 else '⭐'} |")

        lines.extend([
            "",
            "### 4.2 验证流程",
            "",
            "1. **单次验证**：每次只实施一个优化方案",
            "2. **基准对比**：记录优化前后的 QPS、延迟指标",
            "3. **回归测试**：确保修改不引入新的问题",
            "4. **压力测试**：在更高并发下验证稳定性",
            "",
            "### 4.3 回滚预案",
            "",
            "```bash",
            "# 快速回滚命令",
            "cd $RAFT_KV_PATH",
            "git checkout HEAD~1 -- Raft/",
            "cd build && cmake .. && make -j",
            "```",
            "",
            "---",
            "",
            "## 五、关键文件参考",
            "",
            "| 模块 | 文件路径 | 优化方向 |",
            "|:-----|:---------|:--------|",
            "| Raft 核心 | `Raft/Raft.cpp` | AppendEntries、Pipeline、选举 |",
            "| KV 服务 | `KvServer/KvServer.cpp` | Get/Put 请求处理 |",
            "| 客户端 | `Clerk/clerk.cpp` | 重试、连接管理 |",
            "| 存储引擎 | `Skiplist-CPP/skiplist.h` | 读写性能 |",
            "| 持久化 | `Raft/Persister.cpp` | 异步 IO |",
            "| RPC | `myRPC/User/KrpcChannel.cc` | 连接池 |",
            "",
            "---",
            "",
            f"*报告生成时间: {timestamp.strftime('%Y-%m-%d %H:%M:%S')}*",
            f"*由 SmartBench Raft KV 专业版自动生成*",
        ])

        return "\n".join(lines)

    def _generate_action_items(
        self,
        suggestions: List[Suggestion],
        metrics_analysis: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """生成可执行的操作项"""
        items = []

        # 基于评分生成建议
        if metrics_analysis['overall_score'] < 60:
            items.append({
                "action": "优先解决基础瓶颈",
                "priority": "high",
                "details": "系统评分较低，建议先从低风险优化入手",
            })

        # 基于建议生成操作项
        low_risk = [s for s in suggestions if s.risk_level == RiskLevel.LOW]
        if low_risk:
            items.append({
                "action": f"立即实施 {low_risk[0].title}",
                "priority": "high",
                "details": "低风险优化，可快速验证效果",
            })

        # 高优先级建议
        high_priority = sorted(suggestions, key=lambda x: -x.priority)[:2]
        for s in high_priority:
            items.append({
                "action": s.title,
                "priority": "medium",
                "details": f"优先级 {s.priority}，预期 {s.expected_gain}",
            })

        return items

    def _metrics_to_dict(self, metrics: Metrics) -> Dict[str, Any]:
        """转换指标为字典"""
        return {
            "qps": metrics.qps,
            "avg_latency": metrics.avg_latency,
            "p50_latency": metrics.p50_latency,
            "p99_latency": metrics.p99_latency,
            "error_rate": metrics.error_rate,
        }

    def _suggestion_to_dict(self, s: Suggestion) -> Dict[str, Any]:
        """转换建议为字典"""
        return {
            "title": s.title,
            "description": s.description,
            "priority": s.priority,
            "risk_level": s.risk_level.value if hasattr(s.risk_level, 'value') else str(s.risk_level),
            "expected_gain": s.expected_gain,
            "self_confidence": s.self_confidence,
            "implementation_steps": s.implementation_steps,
        }
