"""
技术文档生成器

生成 Markdown 格式的优化建议报告。
"""

import json
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional

from smartbench.core.types import (
    Suggestion, 
    Metrics, 
    OptimizationReport,
    RiskLevel
)


class DocumentGenerator:
    """
    技术文档生成器
    
    功能：
    1. 生成 Markdown 格式的优化报告
    2. 保存原始数据
    3. 生成可视化摘要
    
    Example:
        generator = DocumentGenerator(output_dir="./output")
        
        report = generator.generate(
            suggestions=suggestions,
            metrics=metrics,
            target_qps=300,
            system_name="raft_kv"
        )
        
        print(f"报告已生成: {report.report_path}")
    """
    
    def __init__(self, output_dir: str = "./output", data_dir: str = "./data"):
        """
        初始化生成器
        
        Args:
            output_dir: 报告输出目录
            data_dir: 原始数据存储目录
        """
        self.output_dir = Path(output_dir)
        self.data_dir = Path(data_dir)
        
        # 确保目录存在
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
    
    def generate(
        self,
        suggestions: List[Suggestion],
        metrics: Metrics,
        target_qps: float,
        system_name: str,
        system_type: str = "generic",
    ) -> OptimizationReport:
        """
        生成优化报告
        
        Args:
            suggestions: 优化建议列表
            metrics: 当前性能指标
            target_qps: 目标 QPS
            system_name: 系统名称
            system_type: 系统类型
            
        Returns:
            OptimizationReport: 优化报告对象
        """
        timestamp = datetime.now()
        timestamp_str = timestamp.strftime("%Y%m%d_%H%M%S")
        
        # 生成报告内容
        report_content = self._build_markdown(
            suggestions=suggestions,
            metrics=metrics,
            target_qps=target_qps,
            system_name=system_name,
            system_type=system_type,
            timestamp=timestamp,
        )
        
        # 保存报告
        report_filename = f"report_{timestamp_str}.md"
        report_path = self.output_dir / report_filename
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(report_content)
        
        # 保存原始数据
        raw_filename = f"raw_{timestamp_str}.json"
        raw_path = self.data_dir / raw_filename
        self._save_raw_data(
            suggestions=suggestions,
            metrics=metrics,
            target_qps=target_qps,
            system_name=system_name,
            output_path=raw_path,
        )
        
        # 生成总结
        summary = self._generate_summary(suggestions, metrics, target_qps)
        
        return OptimizationReport(
            timestamp=timestamp,
            target_system=system_name,
            current_metrics=metrics,
            target_qps=target_qps,
            suggestions=suggestions,
            summary=summary,
            raw_data_path=str(raw_path),
            report_path=str(report_path),
        )
    
    def _build_markdown(
        self,
        suggestions: List[Suggestion],
        metrics: Metrics,
        target_qps: float,
        system_name: str,
        system_type: str,
        timestamp: datetime,
    ) -> str:
        """
        构建 Markdown 文档
        
        Args:
            suggestions: 建议列表
            metrics: 性能指标
            target_qps: 目标 QPS
            system_name: 系统名称
            system_type: 系统类型
            timestamp: 时间戳
            
        Returns:
            Markdown 格式的文档内容
        """
        qps_gap = target_qps - metrics.qps
        qps_gap_percent = (qps_gap / target_qps * 100) if target_qps > 0 else 0
        
        lines = [
            "# SmartBench 优化建议报告",
            "",
            f"**生成时间**: {timestamp.strftime('%Y-%m-%d %H:%M:%S')}",
            f"**目标系统**: {system_name} ({system_type})",
            f"**目标 QPS**: {target_qps}",
            "",
            "---",
            "",
            "## 性能分析摘要",
            "",
            "| 指标 | 当前值 | 目标值 | 状态 |",
            "|:-----|:-------|:-------|:-----|",
            f"| QPS | {metrics.qps:.1f} | {target_qps} | {'✅ 达标' if metrics.qps >= target_qps else f'❌ 差距 {qps_gap_percent:.1f}%'} |",
            f"| 平均延迟 | {metrics.avg_latency:.1f}ms | - | - |",
            f"| P50 延迟 | {metrics.p50_latency:.1f}ms | - | - |",
            f"| P99 延迟 | {metrics.p99_latency:.1f}ms | - | - |",
            f"| 错误率 | {metrics.error_rate:.2%} | < 1% | {'✅' if metrics.error_rate < 0.01 else '❌'} |",
            "",
            "---",
            "",
        ]
        
        # 如果没有建议
        if not suggestions:
            lines.extend([
                "## 优化建议",
                "",
                "未发现明显的性能瓶颈，建议保持当前状态。",
                "",
            ])
        else:
            lines.extend([
                "## 优化建议",
                "",
                f"共生成 {len(suggestions)} 条优化建议，建议按优先级顺序实施。",
                "",
            ])
            
            for i, suggestion in enumerate(suggestions, 1):
                lines.extend(self._format_suggestion(suggestion, i))
        
        # 实施建议
        lines.extend(self._build_implementation_section(suggestions))
        
        # 页脚
        lines.extend([
            "---",
            "",
            "*本文档由 SmartBench 自动生成，建议结合 Claude Code 等工具进行实现*",
            "",
            f"*生成时间: {timestamp.strftime('%Y-%m-%d %H:%M:%S')}*",
        ])
        
        return "\n".join(lines)
    
    def _format_suggestion(self, suggestion: Suggestion, index: int) -> List[str]:
        """
        格式化单条建议
        
        Args:
            suggestion: 建议对象
            index: 序号
            
        Returns:
            格式化后的行列表
        """
        # 风险等级图标
        risk_icons = {
            RiskLevel.LOW: "🟢",
            RiskLevel.MEDIUM: "🟡",
            RiskLevel.HIGH: "🔴",
        }
        risk_icon = risk_icons.get(suggestion.risk_level, "⚪")
        
        # 优先级图标
        priority_icon = "⭐" * suggestion.priority
        
        lines = [
            f"### 方案 {index}: {suggestion.title}",
            "",
            f"| 属性 | 值 |",
            "|:-----|:---|",
            f"| 优先级 | {priority_icon} {'高' if suggestion.priority >= 4 else '中' if suggestion.priority >= 2 else '低'} |",
            f"| 风险等级 | {risk_icon} {suggestion.risk_level.value} |",
            f"| 预期收益 | {suggestion.expected_gain} |",
            f"| 来源模型 | {suggestion.source_model} |",
            f"| 置信度 | {suggestion.self_confidence:.0%} |",
            "",
            "#### 问题分析",
            "",
            suggestion.description,
            "",
            "#### 伪代码实现",
            "",
        ]
        
        # 伪代码（添加语言标记）
        if "```" in suggestion.pseudocode:
            lines.append(suggestion.pseudocode)
        else:
            lines.extend([
                "```python",
                suggestion.pseudocode,
                "```",
            ])
        
        lines.extend([
            "",
            "#### 实施步骤",
            "",
        ])
        
        for i, step in enumerate(suggestion.implementation_steps, 1):
            lines.append(f"{i}. {step}")
        
        lines.extend(["", "---", ""])
        
        return lines
    
    def _build_implementation_section(self, suggestions: List[Suggestion]) -> List[str]:
        """
        构建实施建议部分
        
        Args:
            suggestions: 建议列表
            
        Returns:
            格式化后的行列表
        """
        lines = [
            "## 实施建议",
            "",
        ]
        
        if not suggestions:
            lines.append("暂无优化建议。")
            return lines
        
        # 按风险分组
        low_risk = [s for s in suggestions if s.risk_level == RiskLevel.LOW]
        medium_risk = [s for s in suggestions if s.risk_level == RiskLevel.MEDIUM]
        high_risk = [s for s in suggestions if s.risk_level == RiskLevel.HIGH]
        
        lines.append("建议按以下顺序实施：")
        lines.append("")
        
        if low_risk:
            lines.append("**1. 低风险优化（可立即实施）**")
            for s in low_risk[:2]:
                lines.append(f"   - {s.title}")
            lines.append("")
        
        if medium_risk:
            lines.append("**2. 中风险优化（需测试验证）**")
            for s in medium_risk[:2]:
                lines.append(f"   - {s.title}")
            lines.append("")
        
        if high_risk:
            lines.append("**3. 高风险优化（需谨慎评估）**")
            for s in high_risk[:2]:
                lines.append(f"   - {s.title}")
            lines.append("")
        
        lines.extend([
            "---",
            "",
            "## 验证步骤",
            "",
            "1. 每次只实施一个优化方案",
            "2. 实施后运行压测验证效果",
            "3. 如果效果不佳，回滚更改",
            "4. 记录每次优化的实际收益",
            "",
        ])
        
        return lines
    
    def _generate_summary(
        self,
        suggestions: List[Suggestion],
        metrics: Metrics,
        target_qps: float,
    ) -> str:
        """
        生成总结摘要
        
        Args:
            suggestions: 建议列表
            metrics: 性能指标
            target_qps: 目标 QPS
            
        Returns:
            总结文本
        """
        if not suggestions:
            return "未发现明显性能瓶颈，建议保持当前状态。"
        
        top = suggestions[0]
        risk_str = "低" if top.risk_level == RiskLevel.LOW else "中" if top.risk_level == RiskLevel.MEDIUM else "高"
        
        return f"建议优先关注「{top.title}」，该方案风险{risk_str}，预期可带来显著性能提升。"
    
    def _save_raw_data(
        self,
        suggestions: List[Suggestion],
        metrics: Metrics,
        target_qps: float,
        system_name: str,
        output_path: Path,
    ):
        """
        保存原始数据
        
        Args:
            suggestions: 建议列表
            metrics: 性能指标
            target_qps: 目标 QPS
            system_name: 系统名称
            output_path: 输出路径
        """
        data = {
            "timestamp": datetime.now().isoformat(),
            "system_name": system_name,
            "target_qps": target_qps,
            "metrics": {
                "qps": metrics.qps,
                "avg_latency": metrics.avg_latency,
                "p50_latency": metrics.p50_latency,
                "p99_latency": metrics.p99_latency,
                "error_rate": metrics.error_rate,
            },
            "suggestions": [s.to_dict() for s in suggestions],
        }
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def generate_summary_markdown(
        self,
        suggestions: List[Suggestion],
        metrics: Metrics,
        target_qps: float,
    ) -> str:
        """
        生成简洁的摘要 Markdown
        
        适用于快速查看。
        
        Args:
            suggestions: 建议列表
            metrics: 性能指标
            target_qps: 目标 QPS
            
        Returns:
            摘要 Markdown
        """
        lines = [
            "## 优化建议摘要",
            "",
            f"**当前 QPS**: {metrics.qps:.1f} → **目标 QPS**: {target_qps}",
            "",
        ]
        
        for i, s in enumerate(suggestions[:3], 1):
            lines.append(f"{i}. **[{s.title}]** ({s.risk_level.value} 风险)")
            lines.append(f"   - {s.description[:80]}...")
            lines.append("")
        
        return "\n".join(lines)
