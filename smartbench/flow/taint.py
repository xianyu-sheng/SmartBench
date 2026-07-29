"""
污点状态机 - 100% 确定性传播规则。

三值逻辑：TAINTED, NOT_TAINTED, UNKNOWN
完整记录传播路径。
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from smartbench.flow.ast_traversal import (
    AstContext,
    PythonAstVisitor,
    TypeScriptAstVisitor,
    get_child_by_field_name,
    get_named_children,
    get_node_text,
)
from smartbench.flow.schema import (
    AbstractValue,
    SourceLocation,
    TaintState,
    TraceStep,
    location_from_node,
)
from smartbench.flow.scope import ScopeManager, ScopeType


@dataclass
class TaintStateSnapshot:
    """污点状态快照 - 用于调试和验证。"""

    scope_count: int
    variable_count: int
    tainted_count: int
    unknown_count: int
    not_tainted_count: int


class TaintTracker:
    """污点追踪器 - 确定性传播。

    核心原则：
    - 三值逻辑，不猜测
    - TAINTED 传播路径完整记录
    - UNKNOWN 不用于报警
    """

    def __init__(self, context: AstContext):
        self.context = context
        self.scope_manager = ScopeManager()
        self._snapshots: List[TaintStateSnapshot] = []

    def create_value(
        self,
        location: SourceLocation,
        taint_state: TaintState = TaintState.NOT_TAINTED,
        trace: Tuple[TraceStep, ...] = (),
        constant_value: Optional[str] = None,
    ) -> AbstractValue:
        """创建一个新的抽象值。"""
        return AbstractValue(
            location=location,
            taint_state=taint_state,
            taint_trace=trace,
            operations=(),
            constant_value=constant_value,
        )

    def create_tainted_value(
        self,
        location: SourceLocation,
        source_description: str,
        source_snippet: str,
    ) -> AbstractValue:
        """创建一个被污染的值，记录污染源。"""
        trace_step = TraceStep(
            location=location,
            operation=source_description,
            source_snippet=source_snippet,
        )
        return AbstractValue(
            location=location,
            taint_state=TaintState.TAINTED,
            taint_trace=(trace_step,),
            operations=(),
            constant_value=None,
        )

    def propagate_taint(
        self,
        source_value: AbstractValue,
        new_location: SourceLocation,
        operation: str,
        source_snippet: str,
    ) -> AbstractValue:
        """传播污点到新位置。"""
        new_trace_step = TraceStep(
            location=new_location,
            operation=operation,
            source_snippet=source_snippet,
        )
        new_trace = source_value.taint_trace + (new_trace_step,)
        return AbstractValue(
            location=new_location,
            taint_state=source_value.taint_state,
            taint_trace=new_trace,
            operations=source_value.operations + (operation,),
            constant_value=source_value.constant_value,
        )

    def combine_values(
        self,
        values: List[AbstractValue],
        new_location: SourceLocation,
        operation: str,
        source_snippet: str,
    ) -> AbstractValue:
        """组合多个值的污点状态。"""
        if not values:
            return self.create_value(new_location, TaintState.NOT_TAINTED)

        # 组合所有值的污点状态
        combined_state = TaintState.NOT_TAINTED
        for value in values:
            combined_state = combined_state.combine(value.taint_state)

        # 组合所有 trace（去重，保留顺序）
        combined_trace: List[TraceStep] = []
        seen_locations = set()
        for value in values:
            for step in value.taint_trace:
                location_key = (
                    step.location.file_path,
                    step.location.start_byte,
                    step.location.end_byte,
                )
                if location_key not in seen_locations:
                    seen_locations.add(location_key)
                    combined_trace.append(step)

        # 添加当前步骤
        new_step = TraceStep(
            location=new_location,
            operation=operation,
            source_snippet=source_snippet,
        )
        combined_trace.append(new_step)

        # 组合 operations
        combined_ops: List[str] = []
        for value in values:
            combined_ops.extend(value.operations)
        combined_ops.append(operation)

        return AbstractValue(
            location=new_location,
            taint_state=combined_state,
            taint_trace=tuple(combined_trace),
            operations=tuple(combined_ops),
            constant_value=None,
        )

    def snapshot(self) -> TaintStateSnapshot:
        """获取当前状态快照。"""
        all_vars: Dict[str, AbstractValue] = {}
        for scope in self.scope_manager.get_all_scopes():
            all_vars.update(scope.variables)

        tainted = 0
        unknown = 0
        not_tainted = 0
        for var in all_vars.values():
            if var.taint_state == TaintState.TAINTED:
                tainted += 1
            elif var.taint_state == TaintState.UNKNOWN:
                unknown += 1
            else:
                not_tainted += 1

        snapshot = TaintStateSnapshot(
            scope_count=len(self.scope_manager.get_all_scopes()),
            variable_count=len(all_vars),
            tainted_count=tainted,
            unknown_count=unknown,
            not_tainted_count=not_tainted,
        )
        self._snapshots.append(snapshot)
        return snapshot

    def get_snapshots(self) -> List[TaintStateSnapshot]:
        """获取所有快照。"""
        return list(self._snapshots)


# TypeScript/JavaScript 污点传播访问器


class TypeScriptTaintVisitor(TypeScriptAstVisitor):
    """TypeScript/JavaScript 污点传播访问器。"""

    def __init__(self, context: AstContext, tracker: TaintTracker):
        super().__init__(context)
        self.tracker = tracker
        self._current_function: Optional[str] = None
        self._expression_values: Dict[int, AbstractValue] = {}  # node.start_byte -> value
        self._debug: bool = False

    def _log(self, msg: str) -> None:
        """调试日志。"""
        if self._debug:
            print(f"[TaintVisitor] {msg}")

    def enter_node(self, node: Any, location: SourceLocation) -> None:
        """进入节点时调用 - 处理作用域。"""
        node_type = node.type
        self._log(f"enter {node_type} @ {location.start_row}")

        if node_type in ("function_declaration", "arrow_function", "method_definition"):
            name_node = get_child_by_field_name(node, "name")
            name = get_node_text(name_node, self.context.source_bytes) if name_node else None
            scope = self.tracker.scope_manager.enter_scope(
                ScopeType.FUNCTION,
                location,
                name or f"function_{location.start_row}",
            )
            self._current_function = name

            # 将函数参数标记为 UNKNOWN（除非是典型的请求对象）
            params_node = get_child_by_field_name(node, "parameters")
            if params_node:
                for param in get_named_children(params_node):
                    if param.type in ("required_parameter", "identifier"):
                        param_name = get_node_text(param, self.context.source_bytes)
                        if param_name:
                            # 识别典型的请求对象并标记为污染
                            if param_name in ("req", "request", "ctx", "context"):
                                param_value = self.tracker.create_tainted_value(
                                    location_from_node(self.context.file_path, param),
                                    f"parameter: {param_name}",
                                    param_name,
                                )
                                scope.set(param_name, param_value)
                                self._log(f"Param {param_name} marked as tainted")
                            else:
                                # 其他参数标记为 UNKNOWN
                                param_value = self.tracker.create_value(
                                    location_from_node(self.context.file_path, param),
                                    TaintState.UNKNOWN,
                                )
                                scope.set(param_name, param_value)

        elif node_type in (
            "if_statement",
            "for_statement",
            "while_statement",
            "block",
            "statement_block",
        ):
            self.tracker.scope_manager.enter_scope(ScopeType.BLOCK, location)

        elif node_type == "program":
            self.tracker.scope_manager.enter_scope(ScopeType.MODULE, location, "module")

    def leave_node(self, node: Any, location: SourceLocation) -> None:
        """离开节点时调用 - 处理作用域。"""
        node_type = node.type

        if node_type in (
            "function_declaration",
            "arrow_function",
            "if_statement",
            "for_statement",
            "while_statement",
            "block",
            "program",
        ):
            self.tracker.scope_manager.leave_scope()

    def visit_lexical_declaration(self, node: Any, location: SourceLocation) -> None:
        """处理词法声明（const/let/var）。"""
        # 遍历子节点找 variable_declarator
        for child in get_named_children(node):
            if child.type == "variable_declarator":
                self.visit_variable_declarator(
                    child, location_from_node(self.context.file_path, child)
                )

    def visit_variable_declarator(self, node: Any, location: SourceLocation) -> None:
        """处理变量声明。"""
        name_node = get_child_by_field_name(node, "name")
        value_node = get_child_by_field_name(node, "value")

        if name_node is None:
            return

        name = get_node_text(name_node, self.context.source_bytes)
        if not name:
            return

        self._log(f"Declaring variable: {name}")

        # 获取值的污点状态
        value = self._evaluate_expression(value_node) if value_node else None

        if value is None:
            # 没有初始值，认为是 NOT_TAINTED（不猜测）
            value = self.tracker.create_value(location, TaintState.NOT_TAINTED)

        self._log(f"  {name} = {value.taint_state}")

        # 设置变量
        self.tracker.scope_manager.set(name, value)

        # 记录表达式值
        self._expression_values[node.start_byte] = value

    def visit_assignment_expression(self, node: Any, location: SourceLocation) -> None:
        """处理赋值表达式。"""
        left_node = get_child_by_field_name(node, "left")
        right_node = get_child_by_field_name(node, "right")

        if left_node is None or right_node is None:
            return

        # 计算右侧值
        right_value = self._evaluate_expression(right_node)
        if right_value is None:
            return

        # 左侧可以是标识符或成员表达式
        left_name = self._extract_left_name(left_node)
        if left_name:
            # 传播污点
            snippet = get_node_text(node, self.context.source_bytes)
            propagated = self.tracker.propagate_taint(
                right_value,
                location,
                f"assignment: {left_name} = ...",
                snippet,
            )
            self.tracker.scope_manager.set(left_name, propagated)
            self._expression_values[node.start_byte] = propagated

    def visit_call_expression(self, node: Any, location: SourceLocation) -> None:
        """处理函数调用。"""
        # 暂存这个调用的位置，用于后续 sink 检测
        self._expression_values[node.start_byte] = self.tracker.create_value(
            location,
            TaintState.UNKNOWN,
        )

    def visit_template_string(self, node: Any, location: SourceLocation) -> None:
        """处理模板字符串 - 关键的污点传播点。"""
        values: List[AbstractValue] = []

        # 直接遍历所有子节点，查找替换部分
        # 注意：需要检查所有 children 而不只是 named_children
        for child in node.children:
            if child.type == "template_substitution":
                expr_node = get_child_by_field_name(child, "expression")
                if expr_node:
                    expr_value = self._evaluate_expression(expr_node)
                    if expr_value:
                        values.append(expr_value)

        self._log(f"Template string with {len(values)} substitutions")

        if values:
            snippet = get_node_text(node, self.context.source_bytes)
            combined = self.tracker.combine_values(
                values,
                location,
                "template_string_interpolation",
                snippet,
            )
            self._expression_values[node.start_byte] = combined
            self._log(f"  Combined state: {combined.taint_state}")
        else:
            # 没有插值的模板字符串，是 NOT_TAINTED
            self._expression_values[node.start_byte] = self.tracker.create_value(
                location,
                TaintState.NOT_TAINTED,
                constant_value=get_node_text(node, self.context.source_bytes),
            )

    def visit_identifier(self, node: Any, location: SourceLocation) -> None:
        """处理标识符引用。"""
        name = get_node_text(node, self.context.source_bytes)
        if not name:
            return

        # 查找变量
        value = self.tracker.scope_manager.get(name)
        if value is not None:
            # 传播引用
            snippet = name
            propagated = self.tracker.propagate_taint(
                value,
                location,
                f"reference: {name}",
                snippet,
            )
            self._expression_values[node.start_byte] = propagated
            self._log(f"Identifier {name}: {value.taint_state}")
        else:
            self._log(f"Identifier {name}: not found")

    def visit_string(self, node: Any, location: SourceLocation) -> None:
        """处理字符串字面量。"""
        value = self.tracker.create_value(
            location,
            TaintState.NOT_TAINTED,
            constant_value=get_node_text(node, self.context.source_bytes),
        )
        self._expression_values[node.start_byte] = value

    def visit_number(self, node: Any, location: SourceLocation) -> None:
        """处理数字字面量。"""
        value = self.tracker.create_value(
            location,
            TaintState.NOT_TAINTED,
            constant_value=get_node_text(node, self.context.source_bytes),
        )
        self._expression_values[node.start_byte] = value

    def _evaluate_expression(self, node: Any) -> Optional[AbstractValue]:
        """计算表达式的值（确定性）。"""
        if node is None:
            return None

        # 先看是否已经计算过
        if node.start_byte in self._expression_values:
            return self._expression_values[node.start_byte]

        node_type = node.type
        self._log(f"Evaluating: {node_type}")

        # 标识符
        if node_type == "identifier":
            self.visit_identifier(node, location_from_node(self.context.file_path, node))
            return self._expression_values.get(node.start_byte)

        # 模板字符串
        if node_type == "template_string":
            self.visit_template_string(node, location_from_node(self.context.file_path, node))
            return self._expression_values.get(node.start_byte)

        # 字符串字面量
        if node_type == "string":
            self.visit_string(node, location_from_node(self.context.file_path, node))
            return self._expression_values.get(node.start_byte)

        # 数字字面量
        if node_type == "number":
            self.visit_number(node, location_from_node(self.context.file_path, node))
            return self._expression_values.get(node.start_byte)

        # 成员表达式
        if node_type == "member_expression":
            return self._evaluate_member_expression(node)

        # 调用表达式 - 返回 UNKNOWN（除非是简单的我们能识别的）
        if node_type == "call_expression":
            # 先处理，保存到 expression_values
            value = self.tracker.create_value(
                location_from_node(self.context.file_path, node),
                TaintState.UNKNOWN,
            )
            self._expression_values[node.start_byte] = value
            return value

        # await 表达式 - 传播内部表达式的值
        if node_type == "await_expression":
            arg_node = get_child_by_field_name(node, "argument")
            if arg_node:
                return self._evaluate_expression(arg_node)
            return None

        # parenthesized_expression - 去掉括号直接计算内部
        if node_type == "parenthesized_expression":
            for child in get_named_children(node):
                return self._evaluate_expression(child)
            return None

        # 二进制表达式 - 组合子节点
        if node_type == "binary_expression":
            left_node = get_child_by_field_name(node, "left")
            right_node = get_child_by_field_name(node, "right")
            left_value = self._evaluate_expression(left_node)
            right_value = self._evaluate_expression(right_node)
            values = [v for v in [left_value, right_value] if v is not None]
            if values:
                location = location_from_node(self.context.file_path, node)
                snippet = get_node_text(node, self.context.source_bytes)
                combined = self.tracker.combine_values(
                    values, location, "binary_expression", snippet
                )
                self._expression_values[node.start_byte] = combined
                return combined
            return None

        # 其他情况，返回 UNKNOWN（不猜测）
        value = self.tracker.create_value(
            location_from_node(self.context.file_path, node),
            TaintState.UNKNOWN,
        )
        self._expression_values[node.start_byte] = value
        return value

    def _evaluate_member_expression(self, node: Any) -> Optional[AbstractValue]:
        """计算成员表达式 - 确定性。"""
        # 对于像 req.body, request.query 这样的模式，
        # 我们识别这些模式为污染源
        object_node = get_child_by_field_name(node, "object")
        property_node = get_child_by_field_name(node, "property")

        if object_node is None or property_node is None:
            return None

        object_text = get_node_text(object_node, self.context.source_bytes)
        property_text = get_node_text(property_node, self.context.source_bytes)

        self._log(f"Member: {object_text}.{property_text}")

        # 识别常见的污染源模式
        source_patterns = [
            ("req", ["body", "query", "params"]),
            ("request", ["body", "query", "params"]),
            ("ctx", ["request", "query", "params", "body"]),
            ("context", ["request", "query", "params", "body"]),
        ]

        # 检查是否像 req.query 这样的直接污染源
        for prefix, props in source_patterns:
            if object_text == prefix and property_text in props:
                location = location_from_node(self.context.file_path, node)
                snippet = get_node_text(node, self.context.source_bytes)
                value = self.tracker.create_tainted_value(
                    location,
                    f"source: {object_text}.{property_text}",
                    snippet,
                )
                self._log(f"  Tainted source: {object_text}.{property_text}")
                self._expression_values[node.start_byte] = value
                return value

        # 检查 object 本身是否是一个成员表达式（如 req.query.userId）
        # 先计算 object 部分
        object_value = self._evaluate_expression(object_node)
        if object_value is not None:
            location = location_from_node(self.context.file_path, node)
            snippet = get_node_text(node, self.context.source_bytes)
            propagated = self.tracker.propagate_taint(
                object_value,
                location,
                f"member_access: {object_text}.{property_text}",
                snippet,
            )
            self._log(f"  Propagated: {object_value.taint_state} -> {propagated.taint_state}")
            self._expression_values[node.start_byte] = propagated
            return propagated

        return None

    def _extract_left_name(self, node: Any) -> Optional[str]:
        """从左值中提取变量名 - 确定性。"""
        if node.type == "identifier":
            return get_node_text(node, self.context.source_bytes)

        # 对于像 a.b.c 这样的成员表达式，我们只处理顶级标识符
        # 更复杂的情况标记为 UNKNOWN
        return None

    def get_expression_value(self, node: Any) -> Optional[AbstractValue]:
        """获取表达式的值。"""
        return self._expression_values.get(node.start_byte)


# Python 污点传播访问器


class PythonTaintVisitor(PythonAstVisitor):
    """Python 污点传播访问器 - 简化版本。"""

    def __init__(self, context: AstContext, tracker: TaintTracker):
        super().__init__(context)
        self.tracker = tracker
        self._expression_values: Dict[int, AbstractValue] = {}

    def enter_node(self, node: Any, location: SourceLocation) -> None:
        node_type = node.type
        if node_type == "function_definition":
            name_node = get_child_by_field_name(node, "name")
            name = get_node_text(name_node, self.context.source_bytes) if name_node else None
            self.tracker.scope_manager.enter_scope(
                ScopeType.FUNCTION,
                location,
                name,
            )
        elif node_type in ("if_statement", "for_statement", "while_statement", "with_statement"):
            self.tracker.scope_manager.enter_scope(ScopeType.BLOCK, location)
        elif node_type == "module":
            self.tracker.scope_manager.enter_scope(ScopeType.MODULE, location, "module")

    def leave_node(self, node: Any, location: SourceLocation) -> None:
        node_type = node.type
        if node_type in (
            "function_definition",
            "if_statement",
            "for_statement",
            "while_statement",
            "with_statement",
            "module",
        ):
            self.tracker.scope_manager.leave_scope()

    def visit_assignment(self, node: Any, location: SourceLocation) -> None:
        left_node = get_child_by_field_name(node, "left")
        right_node = get_child_by_field_name(node, "right")

        if left_node is None or right_node is None:
            return

        right_value = self._evaluate_expression(right_node)
        if right_value is None:
            return

        if left_node.type == "identifier":
            name = get_node_text(left_node, self.context.source_bytes)
            if name:
                snippet = get_node_text(node, self.context.source_bytes)
                propagated = self.tracker.propagate_taint(
                    right_value,
                    location,
                    f"assignment: {name} = ...",
                    snippet,
                )
                self.tracker.scope_manager.set(name, propagated)
                self._expression_values[node.start_byte] = propagated

    def visit_identifier(self, node: Any, location: SourceLocation) -> None:
        name = get_node_text(node, self.context.source_bytes)
        if not name:
            return

        value = self.tracker.scope_manager.get(name)
        if value is not None:
            snippet = name
            propagated = self.tracker.propagate_taint(
                value,
                location,
                f"reference: {name}",
                snippet,
            )
            self._expression_values[node.start_byte] = propagated

    def visit_string(self, node: Any, location: SourceLocation) -> None:
        value = self.tracker.create_value(
            location,
            TaintState.NOT_TAINTED,
            constant_value=get_node_text(node, self.context.source_bytes),
        )
        self._expression_values[node.start_byte] = value

    def visit_integer(self, node: Any, location: SourceLocation) -> None:
        value = self.tracker.create_value(
            location,
            TaintState.NOT_TAINTED,
            constant_value=get_node_text(node, self.context.source_bytes),
        )
        self._expression_values[node.start_byte] = value

    def _evaluate_expression(self, node: Any) -> Optional[AbstractValue]:
        if node is None:
            return None

        if node.start_byte in self._expression_values:
            return self._expression_values[node.start_byte]

        if node.type == "identifier":
            self.visit_identifier(node, location_from_node(self.context.file_path, node))
            return self._expression_values.get(node.start_byte)

        if node.type == "string":
            self.visit_string(node, location_from_node(self.context.file_path, node))
            return self._expression_values.get(node.start_byte)

        if node.type == "integer":
            self.visit_integer(node, location_from_node(self.context.file_path, node))
            return self._expression_values.get(node.start_byte)

        if node.type == "attribute":
            return self._evaluate_attribute(node)

        if node.type == "binary_operator":
            left_node = get_child_by_field_name(node, "left")
            right_node = get_child_by_field_name(node, "right")
            left_value = self._evaluate_expression(left_node)
            right_value = self._evaluate_expression(right_node)
            values = [v for v in [left_value, right_value] if v is not None]
            if values:
                location = location_from_node(self.context.file_path, node)
                snippet = get_node_text(node, self.context.source_bytes)
                return self.tracker.combine_values(values, location, "binary_operator", snippet)

        return self.tracker.create_value(
            location_from_node(self.context.file_path, node),
            TaintState.UNKNOWN,
        )

    def _evaluate_attribute(self, node: Any) -> Optional[AbstractValue]:
        object_node = get_child_by_field_name(node, "object")
        attribute_node = get_child_by_field_name(node, "attribute")

        if object_node is None or attribute_node is None:
            return None

        object_text = get_node_text(object_node, self.context.source_bytes)
        attribute_text = get_node_text(attribute_node, self.context.source_bytes)

        source_patterns = [
            ("request", ["POST", "GET", "args", "form", "data", "json"]),
            ("req", ["POST", "GET", "args", "form", "data", "json"]),
        ]

        for prefix, attrs in source_patterns:
            if object_text == prefix and attribute_text in attrs:
                location = location_from_node(self.context.file_path, node)
                snippet = get_node_text(node, self.context.source_bytes)
                return self.tracker.create_tainted_value(
                    location,
                    f"source: {object_text}.{attribute_text}",
                    snippet,
                )

        object_value = self._evaluate_expression(object_node)
        if object_value is not None:
            location = location_from_node(self.context.file_path, node)
            snippet = get_node_text(node, self.context.source_bytes)
            return self.tracker.propagate_taint(
                object_value,
                location,
                f"attribute_access: {object_text}.{attribute_text}",
                snippet,
            )

        return None

    def get_expression_value(self, node: Any) -> Optional[AbstractValue]:
        return self._expression_values.get(node.start_byte)
