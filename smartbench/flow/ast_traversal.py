"""
确定性 AST 遍历器 - 100% 基于 tree-sitter，无猜测。

按确定的顺序访问节点（left-to-right），
每个节点都附带确切的位置信息。
"""

from dataclasses import dataclass
from typing import Any, List, Optional

from smartbench.flow.schema import SourceLocation, location_from_node


@dataclass
class AstContext:
    """AST 遍历上下文。"""

    file_path: str
    source: str
    source_bytes: bytes


class AstVisitor:
    """AST 访问器基类。

    子类可以重写特定的访问方法来处理感兴趣的节点。
    """

    def __init__(self, context: AstContext):
        self.context = context
        self._depth = 0

    def enter_node(self, node: Any, location: SourceLocation) -> None:
        """进入节点时调用。"""
        pass

    def leave_node(self, node: Any, location: SourceLocation) -> None:
        """离开节点时调用。"""
        pass

    def visit(self, node: Any, node_type: str, location: SourceLocation) -> None:
        """访问特定类型的节点。"""
        # 尝试调用类型特定的方法
        method_name = f"visit_{node_type}"
        method = getattr(self, method_name, None)
        if method is not None:
            method(node, location)


class AstWalker:
    """确定性 AST 遍历器。

    核心原则：
    - 只使用 tree-sitter 的 AST
    - 按确定的顺序访问子节点（left-to-right）
    - 每个节点都有确切的位置信息
    """

    def __init__(self, context: AstContext):
        self.context = context
        self._visitors: List[AstVisitor] = []

    def add_visitor(self, visitor: AstVisitor) -> None:
        """添加一个访问器。"""
        self._visitors.append(visitor)

    def walk(self, root: Any) -> None:
        """确定性地遍历整个 AST。"""
        self._walk_node(root)

    def _walk_node(self, node: Any) -> None:
        """递归遍历单个节点。"""
        location = location_from_node(self.context.file_path, node)

        # 进入节点
        for visitor in self._visitors:
            visitor._depth += 1
            visitor.enter_node(node, location)
            visitor._depth -= 1

        # 访问特定类型的节点
        node_type = node.type.replace("-", "_")
        for visitor in self._visitors:
            visitor.visit(node, node_type, location)

        # 按确定的顺序访问子节点（left-to-right）
        for child in node.children:
            if not child.is_named:
                continue
            self._walk_node(child)

        # 离开节点
        for visitor in self._visitors:
            visitor._depth += 1
            visitor.leave_node(node, location)
            visitor._depth -= 1


def create_ast_context(file_path: str, source: str) -> AstContext:
    """创建 AST 遍历上下文。"""
    return AstContext(
        file_path=file_path,
        source=source,
        source_bytes=source.encode("utf-8", errors="replace"),
    )


# TypeScript/JavaScript 特定的访问器基类


class TypeScriptAstVisitor(AstVisitor):
    """TypeScript/JavaScript AST 访问器基类。"""

    # 常见的 TypeScript/JavaScript 节点类型
    FUNCTION_DECLARATION = "function_declaration"
    ARROW_FUNCTION = "arrow_function"
    VARIABLE_DECLARATION = "variable_declaration"
    VARIABLE_DECLARATOR = "variable_declarator"
    ASSIGNMENT_EXPRESSION = "assignment_expression"
    CALL_EXPRESSION = "call_expression"
    TEMPLATE_STRING = "template_string"
    TEMPLATE_SUBSTITUTION = "template_substitution"
    IDENTIFIER = "identifier"
    MEMBER_EXPRESSION = "member_expression"
    STRING = "string"
    NUMBER = "number"

    def visit_function_declaration(self, node: Any, location: SourceLocation) -> None:
        pass

    def visit_arrow_function(self, node: Any, location: SourceLocation) -> None:
        pass

    def visit_variable_declaration(self, node: Any, location: SourceLocation) -> None:
        pass

    def visit_variable_declarator(self, node: Any, location: SourceLocation) -> None:
        pass

    def visit_assignment_expression(self, node: Any, location: SourceLocation) -> None:
        pass

    def visit_call_expression(self, node: Any, location: SourceLocation) -> None:
        pass

    def visit_template_string(self, node: Any, location: SourceLocation) -> None:
        pass

    def visit_template_substitution(self, node: Any, location: SourceLocation) -> None:
        pass

    def visit_identifier(self, node: Any, location: SourceLocation) -> None:
        pass

    def visit_member_expression(self, node: Any, location: SourceLocation) -> None:
        pass

    def visit_string(self, node: Any, location: SourceLocation) -> None:
        pass

    def visit_number(self, node: Any, location: SourceLocation) -> None:
        pass


# Python 特定的访问器基类


class PythonAstVisitor(AstVisitor):
    """Python AST 访问器基类。"""

    # 常见的 Python 节点类型
    FUNCTION_DEFINITION = "function_definition"
    ASSIGNMENT = "assignment"
    EXPRESSION_STATEMENT = "expression_statement"
    CALL = "call"
    IDENTIFIER = "identifier"
    STRING = "string"
    INTEGER = "integer"
    DICTIONARY = "dictionary"
    LIST = "list"
    TUPLE = "tuple"
    ATTRIBUTE = "attribute"
    SUBSCRIPT = "subscript"
    BINARY_OPERATOR = "binary_operator"
    AWAIT = "await"
    RETURN_STATEMENT = "return_statement"
    IF_STATEMENT = "if_statement"
    FOR_STATEMENT = "for_statement"
    WHILE_STATEMENT = "while_statement"
    WITH_STATEMENT = "with_statement"
    TRY_STATEMENT = "try_statement"
    RAISE_STATEMENT = "raise_statement"
    IMPORT_STATEMENT = "import_statement"
    IMPORT_FROM_STATEMENT = "import_from_statement"
    CLASS_DEFINITION = "class_definition"

    def visit_function_definition(self, node: Any, location: SourceLocation) -> None:
        pass

    def visit_assignment(self, node: Any, location: SourceLocation) -> None:
        pass

    def visit_expression_statement(self, node: Any, location: SourceLocation) -> None:
        pass

    def visit_call(self, node: Any, location: SourceLocation) -> None:
        pass

    def visit_identifier(self, node: Any, location: SourceLocation) -> None:
        pass

    def visit_string(self, node: Any, location: SourceLocation) -> None:
        pass

    def visit_integer(self, node: Any, location: SourceLocation) -> None:
        pass

    def visit_dictionary(self, node: Any, location: SourceLocation) -> None:
        pass

    def visit_list(self, node: Any, location: SourceLocation) -> None:
        pass

    def visit_tuple(self, node: Any, location: SourceLocation) -> None:
        pass

    def visit_attribute(self, node: Any, location: SourceLocation) -> None:
        pass

    def visit_subscript(self, node: Any, location: SourceLocation) -> None:
        pass

    def visit_binary_operator(self, node: Any, location: SourceLocation) -> None:
        pass

    def visit_await(self, node: Any, location: SourceLocation) -> None:
        pass

    def visit_return_statement(self, node: Any, location: SourceLocation) -> None:
        pass

    def visit_if_statement(self, node: Any, location: SourceLocation) -> None:
        pass

    def visit_for_statement(self, node: Any, location: SourceLocation) -> None:
        pass

    def visit_while_statement(self, node: Any, location: SourceLocation) -> None:
        pass

    def visit_with_statement(self, node: Any, location: SourceLocation) -> None:
        pass

    def visit_try_statement(self, node: Any, location: SourceLocation) -> None:
        pass

    def visit_raise_statement(self, node: Any, location: SourceLocation) -> None:
        pass

    def visit_import_statement(self, node: Any, location: SourceLocation) -> None:
        pass

    def visit_import_from_statement(self, node: Any, location: SourceLocation) -> None:
        pass

    def visit_class_definition(self, node: Any, location: SourceLocation) -> None:
        pass


# 工具函数


def get_node_text(node: Any, source_bytes: bytes) -> str:
    """从 tree-sitter 节点获取文本内容。"""
    try:
        return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
    except Exception:
        return ""


def get_child_by_field_name(node: Any, field_name: str) -> Optional[Any]:
    """通过字段名获取子节点。"""
    return node.child_by_field_name(field_name)


def get_children_by_type(node: Any, node_type: str) -> List[Any]:
    """获取指定类型的所有子节点。"""
    return [child for child in node.children if child.type == node_type]


def get_named_children(node: Any) -> List[Any]:
    """获取所有命名子节点。"""
    return [child for child in node.children if child.is_named]
