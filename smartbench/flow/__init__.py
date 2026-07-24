"""
SmartBench 确定性数据流分析模块。

这个模块提供确定性的 AST 路径分析：
- 基于 AST，不使用正则表达式做模式匹配
- 每个结论都有完整的证据链
- 三值逻辑：TAINTED, NOT_TAINTED, UNKNOWN
- 明确输入源和普通函数参数使用不同置信度，不把推测伪装成事实

架构：
- schema.py: 核心数据结构
- ast_traversal.py: AST 遍历器
- scope.py: 作用域追踪
- taint.py: 污点状态机
- taint_simple.py: 函数内数据流传播引擎
- sources.py: 污染源定义
- sinks.py: 危险 Sink 定义
- findings.py: 问题发现
- analyzer.py: 主分析引擎

使用示例：

    from smartbench.flow import DataFlowAnalyzer, AnalysisResult

    analyzer = DataFlowAnalyzer()
    result = analyzer.analyze_file(
        file_path="app.ts",
        source=source_code,
        language="typescript"
    )

    for finding in result.findings:
        print(f"Found: {finding.rule_name}")
        print(f"Evidence: {finding.evidence}")

与现有规则集成：

    from smartbench.flow import DataFlowAnalyzer

    # 在规则的 analyze 方法中
    analyzer = DataFlowAnalyzer(code_graph)
    findings = analyzer.analyze(code_graph)

    # 返回与现有系统兼容的 findings
    return findings
"""

from smartbench.flow.analyzer import AnalysisContext, AnalysisResult, DataFlowAnalyzer
from smartbench.flow.ast_traversal import (
    AstContext,
    AstVisitor,
    AstWalker,
    PythonAstVisitor,
    TypeScriptAstVisitor,
)
from smartbench.flow.findings import FlowFinding
from smartbench.flow.schema import (
    AbstractValue,
    FindingEvidence,
    ScopeType,
    SourceLocation,
    TaintState,
    TraceStep,
)
from smartbench.flow.scope import Scope, ScopeManager
from smartbench.flow.sinks import SinkDefinition, get_sinks_for_language
from smartbench.flow.sources import SourceDefinition, get_sources_for_language
from smartbench.flow.taint import PythonTaintVisitor, TaintTracker, TypeScriptTaintVisitor

__all__ = [
    # Schema
    "AbstractValue",
    "FindingEvidence",
    "ScopeType",
    "SourceLocation",
    "TaintState",
    "TraceStep",
    # AST Traversal
    "AstContext",
    "AstVisitor",
    "AstWalker",
    "TypeScriptAstVisitor",
    "PythonAstVisitor",
    # Scope
    "Scope",
    "ScopeManager",
    # Taint
    "TaintTracker",
    "TypeScriptTaintVisitor",
    "PythonTaintVisitor",
    # Sources/Sinks
    "SourceDefinition",
    "get_sources_for_language",
    "SinkDefinition",
    "get_sinks_for_language",
    # Findings
    "FlowFinding",
    # Analyzer
    "DataFlowAnalyzer",
    "AnalysisResult",
    "AnalysisContext",
]

__version__ = "0.1.0"
