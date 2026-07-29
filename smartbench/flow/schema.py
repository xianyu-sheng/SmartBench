"""
核心数据结构 - 100% 确定性，无猜测。

所有位置信息都精确到字节偏移和行列号，
所有数据结构都是不可变的（frozen dataclass）。
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Tuple


class TaintState(Enum):
    """污点状态 - 三值逻辑，无模糊地带。

    核心原则：
    - TAINTED: 100% 确定被污染
    - NOT_TAINTED: 100% 确定未被污染
    - UNKNOWN: 不确定；只能作为明确标注、较低置信度的候选
    """

    TAINTED = "tainted"
    NOT_TAINTED = "not_tainted"
    UNKNOWN = "unknown"

    def combine(self, other: "TaintState") -> "TaintState":
        """组合两个污点状态。

        规则：
        - TAINTED | anything = TAINTED
        - UNKNOWN | NOT_TAINTED = UNKNOWN
        - NOT_TAINTED | NOT_TAINTED = NOT_TAINTED
        """
        if self == TaintState.TAINTED or other == TaintState.TAINTED:
            return TaintState.TAINTED
        if self == TaintState.UNKNOWN or other == TaintState.UNKNOWN:
            return TaintState.UNKNOWN
        return TaintState.NOT_TAINTED


@dataclass(frozen=True)
class SourceLocation:
    """源代码位置 - 100% 确定，可验证。

    同时提供字节偏移和行列号，确保可以准确定位。
    """

    file_path: str
    start_byte: int
    end_byte: int
    start_row: int  # 1-based
    start_column: int  # 0-based
    end_row: int  # 1-based
    end_column: int  # 0-based

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_path": self.file_path,
            "start_byte": self.start_byte,
            "end_byte": self.end_byte,
            "start_row": self.start_row,
            "start_column": self.start_column,
            "end_row": self.end_row,
            "end_column": self.end_column,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SourceLocation":
        return cls(
            file_path=d["file_path"],
            start_byte=d["start_byte"],
            end_byte=d["end_byte"],
            start_row=d["start_row"],
            start_column=d["start_column"],
            end_row=d["end_row"],
            end_column=d["end_column"],
        )

    def get_source_snippet(self, source: str) -> str:
        """Return the exact byte-ranged snippet, including for Unicode text."""
        try:
            payload = source.encode("utf-8", errors="replace")
            return payload[self.start_byte : self.end_byte].decode("utf-8", errors="replace")
        except Exception:
            return ""

    def contains(self, other: "SourceLocation") -> bool:
        """判断另一个位置是否包含在当前位置内。"""
        if self.file_path != other.file_path:
            return False
        return self.start_byte <= other.start_byte and other.end_byte <= self.end_byte


@dataclass(frozen=True)
class TraceStep:
    """污点传播的一个步骤 - 完整记录，可追溯。

    每个步骤都有：
    - 确切的位置
    - 操作描述
    - 源代码片段（证据）
    """

    location: SourceLocation
    operation: str
    source_snippet: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "location": self.location.to_dict(),
            "operation": self.operation,
            "source_snippet": self.source_snippet,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TraceStep":
        return cls(
            location=SourceLocation.from_dict(d["location"]),
            operation=d["operation"],
            source_snippet=d["source_snippet"],
        )


@dataclass(frozen=True)
class AbstractValue:
    """抽象值 - 不追踪真实值，只追踪关键属性。

    核心原则：
    - 不猜测值，只记录确定的信息
    - 污点状态是三值的
    - 完整记录传播路径
    """

    location: SourceLocation
    taint_state: TaintState
    taint_trace: Tuple[TraceStep, ...] = field(default_factory=tuple)
    operations: Tuple[str, ...] = field(default_factory=tuple)
    constant_value: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "location": self.location.to_dict(),
            "taint_state": self.taint_state.value,
            "taint_trace": [step.to_dict() for step in self.taint_trace],
            "operations": list(self.operations),
            "constant_value": self.constant_value,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AbstractValue":
        return cls(
            location=SourceLocation.from_dict(d["location"]),
            taint_state=TaintState(d["taint_state"]),
            taint_trace=tuple(TraceStep.from_dict(s) for s in d.get("taint_trace", [])),
            operations=tuple(d.get("operations", [])),
            constant_value=d.get("constant_value"),
        )

    def with_taint(
        self, new_state: TaintState, new_step: Optional[TraceStep] = None
    ) -> "AbstractValue":
        """创建一个新的抽象值，更新污点状态。"""
        new_trace = list(self.taint_trace)
        if new_step is not None:
            new_trace.append(new_step)
        return AbstractValue(
            location=self.location,
            taint_state=new_state,
            taint_trace=tuple(new_trace),
            operations=self.operations,
            constant_value=self.constant_value,
        )

    def with_operation(self, operation: str) -> "AbstractValue":
        """创建一个新的抽象值，添加操作记录。"""
        return AbstractValue(
            location=self.location,
            taint_state=self.taint_state,
            taint_trace=self.taint_trace,
            operations=self.operations + (operation,),
            constant_value=self.constant_value,
        )


@dataclass(frozen=True)
class FindingEvidence:
    """完整的证据链 - 可验证，可审计。

    包含：
    - Sink 位置和代码片段
    - 完整的污点传播路径
    - 污染源位置和代码片段
    - 调用链（如果有）
    """

    sink_snippet: str
    sink_location: SourceLocation
    taint_trace: Tuple[TraceStep, ...]
    source_snippet: str
    source_location: SourceLocation
    call_chain: Tuple[SourceLocation, ...] = field(default_factory=tuple)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sink_snippet": self.sink_snippet,
            "sink_location": self.sink_location.to_dict(),
            "taint_trace": [step.to_dict() for step in self.taint_trace],
            "source_snippet": self.source_snippet,
            "source_location": self.source_location.to_dict(),
            "call_chain": [loc.to_dict() for loc in self.call_chain],
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "FindingEvidence":
        return cls(
            sink_snippet=d["sink_snippet"],
            sink_location=SourceLocation.from_dict(d["sink_location"]),
            taint_trace=tuple(TraceStep.from_dict(s) for s in d.get("taint_trace", [])),
            source_snippet=d["source_snippet"],
            source_location=SourceLocation.from_dict(d["source_location"]),
            call_chain=tuple(SourceLocation.from_dict(loc) for loc in d.get("call_chain", [])),
        )


class ScopeType(Enum):
    """作用域类型。"""

    MODULE = "module"
    FUNCTION = "function"
    BLOCK = "block"
    CLASS = "class"


@dataclass
class Variable:
    """变量记录 - 确定性。"""

    name: str
    value: AbstractValue
    location: SourceLocation


def location_from_node(file_path: str, node: Any) -> SourceLocation:
    """从 tree-sitter 节点创建 SourceLocation。

    Args:
        file_path: 文件路径
        node: tree-sitter 节点

    Returns:
        确定的 SourceLocation
    """
    return SourceLocation(
        file_path=file_path,
        start_byte=node.start_byte,
        end_byte=node.end_byte,
        start_row=node.start_point[0] + 1,  # tree-sitter uses 0-based rows
        start_column=node.start_point[1],
        end_row=node.end_point[0] + 1,
        end_column=node.end_point[1],
    )
