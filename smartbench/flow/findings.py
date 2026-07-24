"""
问题发现 - 证据链完整，并显式携带置信度。

高置信度 finding 需要已知污染源；普通函数参数到危险 sink 的路径
可以作为较低置信度候选，但必须明确说明调用方可控性尚未证明。
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from smartbench.flow.schema import (
    AbstractValue,
    FindingEvidence,
    SourceLocation,
)


@dataclass
class FlowFinding:
    """数据流问题发现 - 完整证据链。

    核心原则：
    - 证据链完整，可验证
    - 置信度与已证明的事实相匹配
    - 修复建议基于确定的 sink 类型
    """

    rule_id: str
    rule_name: str
    severity: str  # "error", "warning", "info"
    message: str
    location: SourceLocation
    evidence: FindingEvidence
    confidence: float = 0.95
    fix_suggestion: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_base_finding(self) -> Any:
        """转换为现有的 Finding 格式。"""
        # 延迟导入以避免循环依赖
        from smartbench.core.rules.base import Finding as BaseFinding
        from smartbench.core.rules.base import Location, Severity

        # 转换 SourceLocation 为 base Location
        base_location = Location(
            file_path=self.location.file_path,
            line_start=self.location.start_row,
            line_end=self.location.end_row,
            column_start=self.location.start_column,
            column_end=self.location.end_column,
        )

        # 转换 severity
        severity_map = {
            "error": Severity.ERROR,
            "warning": Severity.WARNING,
            "info": Severity.INFO,
        }
        base_severity = severity_map.get(self.severity, Severity.ERROR)

        # 在 metadata 中保存完整证据
        metadata = dict(self.metadata)
        metadata["evidence"] = self.evidence.to_dict()
        if self.fix_suggestion:
            metadata["fix_suggestion"] = self.fix_suggestion

        return BaseFinding(
            rule_id=self.rule_id,
            rule_name=self.rule_name,
            severity=base_severity,
            location=base_location,
            message=self.message,
            evidence=[],
            confidence=self.confidence,
            metadata=metadata,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "severity": self.severity,
            "message": self.message,
            "location": self.location.to_dict(),
            "evidence": self.evidence.to_dict(),
            "confidence": self.confidence,
            "fix_suggestion": self.fix_suggestion,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "FlowFinding":
        return cls(
            rule_id=d["rule_id"],
            rule_name=d["rule_name"],
            severity=d["severity"],
            message=d["message"],
            location=SourceLocation.from_dict(d["location"]),
            evidence=FindingEvidence.from_dict(d["evidence"]),
            confidence=d.get("confidence", 0.95),
            fix_suggestion=d.get("fix_suggestion"),
            metadata=d.get("metadata", {}),
        )


def create_sql_injection_finding(
    sink_location: SourceLocation,
    sink_snippet: str,
    tainted_value: AbstractValue,
    source: str,
) -> FlowFinding:
    """创建带证据链的 SQL 注入 finding。"""
    # 确定污染源位置
    source_location = sink_location
    source_snippet = ""
    if tainted_value.taint_trace:
        first_step = tainted_value.taint_trace[0]
        source_location = first_step.location
        source_snippet = first_step.source_snippet

    # 创建证据链
    evidence = FindingEvidence(
        sink_snippet=sink_snippet,
        sink_location=sink_location,
        taint_trace=tainted_value.taint_trace,
        source_snippet=source_snippet,
        source_location=source_location,
    )

    # 修复建议
    fix_suggestion = _generate_sql_fix_suggestion(sink_snippet)

    return FlowFinding(
        rule_id="sql_injection_flow",
        rule_name="SQL Injection (Data Flow)",
        severity="error",
        message="Tainted data flows into SQL query without proper sanitization",
        location=sink_location,
        evidence=evidence,
        confidence=0.95,
        fix_suggestion=fix_suggestion,
    )


def create_command_injection_finding(
    sink_location: SourceLocation,
    sink_snippet: str,
    tainted_value: AbstractValue,
    source: str,
) -> FlowFinding:
    """创建带证据链的命令注入 finding。"""
    source_location = sink_location
    source_snippet = ""
    if tainted_value.taint_trace:
        first_step = tainted_value.taint_trace[0]
        source_location = first_step.location
        source_snippet = first_step.source_snippet

    evidence = FindingEvidence(
        sink_snippet=sink_snippet,
        sink_location=sink_location,
        taint_trace=tainted_value.taint_trace,
        source_snippet=source_snippet,
        source_location=source_location,
    )

    fix_suggestion = _generate_command_fix_suggestion(sink_snippet)

    return FlowFinding(
        rule_id="command_injection_flow",
        rule_name="Command Injection (Data Flow)",
        severity="error",
        message="Tainted data flows into command execution without proper sanitization",
        location=sink_location,
        evidence=evidence,
        confidence=0.95,
        fix_suggestion=fix_suggestion,
    )


def create_path_traversal_finding(
    sink_location: SourceLocation,
    sink_snippet: str,
    tainted_value: AbstractValue,
    source: str,
) -> FlowFinding:
    """创建带证据链的路径遍历 finding。"""
    source_location = sink_location
    source_snippet = ""
    if tainted_value.taint_trace:
        first_step = tainted_value.taint_trace[0]
        source_location = first_step.location
        source_snippet = first_step.source_snippet

    evidence = FindingEvidence(
        sink_snippet=sink_snippet,
        sink_location=sink_location,
        taint_trace=tainted_value.taint_trace,
        source_snippet=source_snippet,
        source_location=source_location,
    )

    fix_suggestion = _generate_path_fix_suggestion(sink_snippet)

    return FlowFinding(
        rule_id="path_traversal_flow",
        rule_name="Path Traversal (Data Flow)",
        severity="error",
        message="Tainted data flows into file path without proper sanitization",
        location=sink_location,
        evidence=evidence,
        confidence=0.95,
        fix_suggestion=fix_suggestion,
    )


def _generate_sql_fix_suggestion(sink_snippet: str) -> str:
    """生成 SQL 修复建议 - 基于常见模式。"""
    # 检测是否有模板字符串
    if "`" in sink_snippet and "${" in sink_snippet:
        return (
            "Use parameterized queries instead of string interpolation:\n\n"
            + "const placeholders = ids.map(() => '?').join(',');\n"
            + "await db.run(`DELETE FROM table WHERE id IN (${placeholders})`, ids);"
        )

    # 检测是否有字符串拼接
    if "+" in sink_snippet:
        return (
            "Use parameterized queries instead of string concatenation:\n\n"
            + "await db.run('SELECT * FROM table WHERE id = ?', [id]);"
        )

    # 默认建议
    return "Use parameterized queries instead of dynamic SQL construction."


def _generate_command_fix_suggestion(sink_snippet: str) -> str:
    """生成命令注入修复建议。"""
    return (
        "Use the array form of command execution to avoid shell parsing:\n\n"
        + "// Instead of: child_process.exec(`ls ${dir}`)\n"
        + "// Use: child_process.execFile('ls', [dir])"
    )


def _generate_path_fix_suggestion(sink_snippet: str) -> str:
    """生成路径遍历修复建议。"""
    return (
        "Use path.resolve and validate the path is within expected directories:\n\n"
        + "const safePath = path.resolve(baseDir, userInput);\n"
        + "if (!safePath.startsWith(baseDir)) { throw new Error('Invalid path'); }"
    )
