"""
污染源定义 - 确定性识别。

只基于 AST 节点类型和结构，不使用正则。
"""

from dataclasses import dataclass
from typing import List, Set


@dataclass(frozen=True)
class SourceDefinition:
    """污染源定义。"""

    name: str
    description: str
    language: str
    pattern_type: str  # "member_access", "parameter_name", etc.


# TypeScript/JavaScript 污染源定义


TYPESCRIPT_SOURCES: List[SourceDefinition] = [
    SourceDefinition(
        name="req.body",
        description="Express request body",
        language="typescript",
        pattern_type="member_access",
    ),
    SourceDefinition(
        name="req.query",
        description="Express request query",
        language="typescript",
        pattern_type="member_access",
    ),
    SourceDefinition(
        name="req.params",
        description="Express request params",
        language="typescript",
        pattern_type="member_access",
    ),
    SourceDefinition(
        name="request.body",
        description="Request body",
        language="typescript",
        pattern_type="member_access",
    ),
    SourceDefinition(
        name="request.query",
        description="Request query",
        language="typescript",
        pattern_type="member_access",
    ),
    SourceDefinition(
        name="request.params",
        description="Request params",
        language="typescript",
        pattern_type="member_access",
    ),
    SourceDefinition(
        name="ctx.request",
        description="Koa context request",
        language="typescript",
        pattern_type="member_access",
    ),
]

# Python 污染源定义

PYTHON_SOURCES: List[SourceDefinition] = [
    SourceDefinition(
        name="request.POST",
        description="Django request POST",
        language="python",
        pattern_type="attribute",
    ),
    SourceDefinition(
        name="request.GET",
        description="Django request GET",
        language="python",
        pattern_type="attribute",
    ),
    SourceDefinition(
        name="request.args",
        description="Flask request args",
        language="python",
        pattern_type="attribute",
    ),
    SourceDefinition(
        name="request.form",
        description="Flask request form",
        language="python",
        pattern_type="attribute",
    ),
]


def get_sources_for_language(language: str) -> List[SourceDefinition]:
    """获取指定语言的污染源定义。"""
    if language.lower() in ("typescript", "javascript", "ts", "js"):
        return TYPESCRIPT_SOURCES
    if language.lower() in ("python", "py"):
        return PYTHON_SOURCES
    return []


# 常见的参数名模式，可能表示用户输入

TYPICAL_INPUT_PARAMETER_NAMES: Set[str] = {
    "req",
    "request",
    "ctx",
    "context",
    "input",
    "user_input",
    "query",
    "params",
    "args",
    "data",
    "body",
    "payload",
}
