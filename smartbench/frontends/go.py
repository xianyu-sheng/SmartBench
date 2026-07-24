"""Go source lowering into language-neutral semantic operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from smartbench.graph.tree_parser import get_parser
from smartbench.ir import (
    EvidenceRef,
    FactKind,
    OperationEdge,
    OperationEdgeKind,
    OperationKind,
    SemanticFact,
    SemanticIR,
    SemanticOperation,
)

_FUNCTION_TYPES = {"function_declaration", "method_declaration"}
_LOOP_TYPES = {"for_statement", "range_clause"}
_ASSIGN_TYPES = {
    "assignment_statement",
    "short_var_declaration",
    "var_declaration",
}


@dataclass
class GoLoweringResult:
    """Result of lowering Go syntax into the common operation model."""

    operations: list[SemanticOperation] = field(default_factory=list)
    edges: list[OperationEdge] = field(default_factory=list)
    facts: list[SemanticFact] = field(default_factory=list)
    files_analyzed: int = 0
    errors: list[str] = field(default_factory=list)


class GoSemanticLowerer:
    """Lower Go CST nodes to a finite set of language-neutral operations."""

    def lower(self, ir: SemanticIR) -> GoLoweringResult:
        result = GoLoweringResult()
        parser = get_parser("go")
        if parser is None:
            result.errors.append("tree-sitter Go parser is unavailable")
            return result

        for file_path, unit in sorted(ir.source_units.items()):
            if unit.language != "go" and not file_path.endswith(".go"):
                continue
            source = ir.read_source(file_path)
            if source is None:
                result.errors.append(f"unable to read Go source: {file_path}")
                continue
            try:
                tree = parser.parse(source.encode("utf-8"))
                lowered = _GoFileLowerer(file_path, source).lower(tree.root_node)
            except Exception as exc:
                result.errors.append(f"{file_path}: {exc}")
                continue
            result.operations.extend(lowered.operations)
            result.edges.extend(lowered.edges)
            result.facts.extend(lowered.facts)
            result.files_analyzed += 1

        return result


class _GoFileLowerer:
    def __init__(self, file_path: str, source: str) -> None:
        self.file_path = file_path
        self.source = source
        self.source_bytes = source.encode("utf-8")
        self.operations: list[SemanticOperation] = []
        self.edges: list[OperationEdge] = []
        self.facts: list[SemanticFact] = []
        self._ordinal = 0

    def lower(self, root: Any) -> GoLoweringResult:
        for node in root.named_children:
            if node.type in _FUNCTION_TYPES:
                self._lower_function(node)
        return GoLoweringResult(
            operations=self.operations,
            edges=self.edges,
            facts=self.facts,
            files_analyzed=1,
        )

    def _lower_function(self, node: Any) -> None:
        name_node = node.child_by_field_name("name")
        name = self._text(name_node) or "<anonymous>"
        function = self._operation(
            OperationKind.FUNCTION,
            node,
            scope_id="",
            target=name,
            value=self._first_line(node),
        )
        function = SemanticOperation(
            id=function.id,
            kind=function.kind,
            language=function.language,
            scope_id=function.id,
            location=function.location,
            target=function.target,
            value=function.value,
            operands=function.operands,
            attributes=function.attributes,
        )
        self.operations[-1] = function

        parameters = node.child_by_field_name("parameters")
        if parameters is not None:
            for declaration in parameters.named_children:
                if declaration.type not in {"parameter_declaration", "variadic_parameter_declaration"}:
                    continue
                identifiers = [
                    self._text(child)
                    for child in declaration.named_children
                    if child.type == "identifier"
                ]
                for identifier in identifiers:
                    parameter = self._operation(
                        OperationKind.PARAMETER,
                        declaration,
                        scope_id=function.id,
                        target=identifier,
                        value=self._text(declaration),
                    )
                    self._edge(function.id, parameter.id, OperationEdgeKind.CONTAINS)

        body = node.child_by_field_name("body")
        if body is None:
            return
        statements = self._statement_nodes(body)
        roots = self._lower_sequence(statements, function.id)
        for operation_id in roots:
            self._edge(function.id, operation_id, OperationEdgeKind.CONTAINS)

    def _lower_sequence(self, statements: list[Any], scope_id: str) -> list[str]:
        roots: list[str] = []
        previous: str | None = None
        for statement in statements:
            operation_id = self._lower_statement(statement, scope_id)
            if operation_id is None:
                continue
            roots.append(operation_id)
            if previous is not None:
                self._edge(previous, operation_id, OperationEdgeKind.NEXT)
            previous = operation_id
        return roots

    def _lower_statement(self, node: Any, scope_id: str) -> str | None:
        node_type = node.type
        if node_type == "if_statement":
            return self._lower_branch(node, scope_id)
        if node_type in _LOOP_TYPES:
            return self._lower_loop(node, scope_id)
        if node_type in _ASSIGN_TYPES:
            return self._lower_assignment(node, scope_id)
        if node_type in {"inc_statement", "dec_statement"}:
            operation = self._operation(
                OperationKind.UPDATE,
                node,
                scope_id,
                target=self._text(node.named_children[0]) if node.named_children else "",
                value=self._text(node),
            )
            return operation.id
        if node_type == "return_statement":
            operation = self._operation(
                OperationKind.RETURN,
                node,
                scope_id,
                value=self._statement_value(node, "return"),
            )
            return operation.id
        if node_type == "continue_statement":
            return self._operation(OperationKind.CONTINUE, node, scope_id).id
        if node_type == "break_statement":
            return self._operation(OperationKind.BREAK, node, scope_id).id
        if node_type == "go_statement":
            return self._call_like(OperationKind.SPAWN, node, scope_id).id
        if node_type == "defer_statement":
            return self._call_like(OperationKind.DEFER, node, scope_id).id
        if node_type == "send_statement":
            children = node.named_children
            target = self._text(children[0]) if children else ""
            value = self._text(children[1]) if len(children) > 1 else ""
            operation = self._operation(
                OperationKind.SEND,
                node,
                scope_id,
                target=target,
                value=value,
                operands=tuple(self._identifiers(node)),
            )
            return operation.id
        if node_type == "select_statement":
            operation = self._operation(OperationKind.SELECT, node, scope_id, value=self._text(node))
            body = node.child_by_field_name("body")
            if body is not None:
                roots = self._lower_sequence(self._statement_nodes(body), scope_id)
                if roots:
                    self._edge(operation.id, roots[0], OperationEdgeKind.BODY)
            return operation.id

        call = self._first_descendant(node, "call_expression")
        if call is not None:
            operation = self._call_like(OperationKind.CALL, call, scope_id, location_node=node)
            return operation.id
        return None

    def _lower_branch(self, node: Any, scope_id: str) -> str:
        condition = node.child_by_field_name("condition")
        if condition is None:
            condition = next(
                (child for child in node.named_children if child.type not in {"block", "else_clause"}),
                None,
            )
        condition_text = self._text(condition)
        branch = self._operation(
            OperationKind.BRANCH,
            node,
            scope_id,
            value=condition_text,
            operands=tuple(self._identifiers(condition)),
            attributes={
                "operators": self._operators(condition),
                "literals": self._literals(condition),
                "calls": self._calls(condition),
            },
        )

        consequence = node.child_by_field_name("consequence")
        if consequence is None:
            consequence = next((child for child in node.named_children if child.type == "block"), None)
        if consequence is not None:
            roots = self._lower_sequence(self._statement_nodes(consequence), scope_id)
            if roots:
                self._edge(branch.id, roots[0], OperationEdgeKind.TRUE_BRANCH)

        alternative = node.child_by_field_name("alternative")
        if alternative is not None:
            roots = self._lower_sequence(self._statement_nodes(alternative), scope_id)
            if roots:
                self._edge(branch.id, roots[0], OperationEdgeKind.FALSE_BRANCH)
        return branch.id

    def _lower_loop(self, node: Any, scope_id: str) -> str:
        body = node.child_by_field_name("body")
        condition = node.child_by_field_name("condition")
        loop = self._operation(
            OperationKind.LOOP,
            node,
            scope_id,
            value=self._text(condition) or self._first_line(node),
            operands=tuple(self._identifiers(condition)),
        )
        if body is not None:
            roots = self._lower_sequence(self._statement_nodes(body), scope_id)
            if roots:
                self._edge(loop.id, roots[0], OperationEdgeKind.BODY)
        return loop.id

    def _lower_assignment(self, node: Any, scope_id: str) -> str:
        children = node.named_children
        target = self._text(children[0]) if children else ""
        value = self._text(children[1]) if len(children) > 1 else self._text(node)
        receives = any(
            descendant.type == "unary_expression" and self._text(descendant).startswith("<-")
            for descendant in self._descendants(node)
        )
        kind = OperationKind.RECEIVE if receives else OperationKind.ASSIGN
        operation = self._operation(
            kind,
            node,
            scope_id,
            target=target,
            value=value,
            operands=tuple(self._identifiers(children[1] if len(children) > 1 else node)),
            attributes={"calls": self._calls(node)},
        )
        return operation.id

    def _call_like(
        self,
        kind: OperationKind,
        node: Any,
        scope_id: str,
        location_node: Any | None = None,
    ) -> SemanticOperation:
        call = node if node.type == "call_expression" else self._first_descendant(node, "call_expression")
        target = ""
        operands: tuple[str, ...] = ()
        if call is not None:
            function = call.child_by_field_name("function")
            target = self._text(function) or (
                self._text(call.named_children[0]) if call.named_children else ""
            )
            arguments = call.child_by_field_name("arguments")
            operands = tuple(self._identifiers(arguments))
        return self._operation(
            kind,
            location_node or node,
            scope_id,
            target=target,
            value=self._text(call or node),
            operands=operands,
        )

    def _operation(
        self,
        kind: OperationKind,
        node: Any,
        scope_id: str,
        target: str = "",
        value: str = "",
        operands: tuple[str, ...] = (),
        attributes: dict[str, Any] | None = None,
    ) -> SemanticOperation:
        self._ordinal += 1
        location = self._location(node)
        operation = SemanticOperation(
            id=SemanticOperation.make_id(
                self.file_path,
                location.line_start,
                kind,
                self._ordinal,
            ),
            kind=kind,
            language="go",
            scope_id=scope_id,
            location=location,
            target=target,
            value=value,
            operands=operands,
            attributes=attributes or {},
        )
        self.operations.append(operation)
        self._add_fact(operation)
        return operation

    def _add_fact(self, operation: SemanticOperation) -> None:
        predicates = {
            OperationKind.CALL: FactKind.CALLS,
            OperationKind.SPAWN: FactKind.CALLS,
            OperationKind.DEFER: FactKind.CALLS,
            OperationKind.ASSIGN: FactKind.WRITES,
            OperationKind.UPDATE: FactKind.WRITES,
            OperationKind.RECEIVE: FactKind.READS,
            OperationKind.BRANCH: FactKind.CONTROLS,
            OperationKind.RETURN: FactKind.STATE_TRANSITION,
            OperationKind.CONTINUE: FactKind.STATE_TRANSITION,
            OperationKind.BREAK: FactKind.STATE_TRANSITION,
            OperationKind.SEND: FactKind.STATE_TRANSITION,
        }
        predicate = predicates.get(operation.kind)
        if predicate is None:
            return
        self.facts.append(
            SemanticFact(
                subject=operation.scope_id or self.file_path,
                predicate=predicate,
                object=operation.target or operation.value or operation.kind.value,
                evidence=(operation.location,),
                attributes={"operation_id": operation.id, "kind": operation.kind.value},
            )
        )

    def _edge(self, source_id: str, target_id: str, kind: OperationEdgeKind) -> None:
        self.edges.append(OperationEdge(source_id=source_id, target_id=target_id, kind=kind))

    def _location(self, node: Any) -> EvidenceRef:
        return EvidenceRef(
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            column_start=node.start_point[1],
            column_end=node.end_point[1],
            snippet=self._text(node),
            source="go_frontend",
        )

    def _statement_nodes(self, node: Any) -> list[Any]:
        if node.type in {"block", "else_clause", "select_statement"}:
            statement_list = next(
                (child for child in node.named_children if child.type in {"statement_list", "expression_case"}),
                None,
            )
            if statement_list is not None:
                return list(statement_list.named_children)
        if node.type == "statement_list":
            return list(node.named_children)
        if node.type == "else_clause":
            return list(node.named_children)
        return list(node.named_children)

    def _statement_value(self, node: Any, keyword: str) -> str:
        value = self._text(node).strip()
        return value[len(keyword):].strip() if value.startswith(keyword) else value

    def _first_line(self, node: Any) -> str:
        return self._text(node).splitlines()[0].strip()

    def _text(self, node: Any | None) -> str:
        if node is None:
            return ""
        return self.source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    def _first_descendant(self, node: Any | None, node_type: str) -> Any | None:
        if node is None:
            return None
        if node.type == node_type:
            return node
        for child in node.named_children:
            found = self._first_descendant(child, node_type)
            if found is not None:
                return found
        return None

    def _descendants(self, node: Any | None):
        if node is None:
            return
        yield node
        for child in node.named_children:
            yield from self._descendants(child)

    def _identifiers(self, node: Any | None) -> list[str]:
        if node is None:
            return []
        return [
            self._text(descendant)
            for descendant in self._descendants(node)
            if descendant.type in {"identifier", "field_identifier"}
        ]

    def _operators(self, node: Any | None) -> list[str]:
        if node is None:
            return []
        source = self._text(node)
        return [operator for operator in ("==", "!=", ">=", "<=", "&&", "||", ">", "<", "!") if operator in source]

    def _literals(self, node: Any | None) -> list[str]:
        if node is None:
            return []
        literal_types = {
            "interpreted_string_literal",
            "raw_string_literal",
            "int_literal",
            "float_literal",
            "true",
            "false",
            "nil",
        }
        return [self._text(descendant) for descendant in self._descendants(node) if descendant.type in literal_types]

    def _calls(self, node: Any | None) -> list[str]:
        if node is None:
            return []
        calls: list[str] = []
        for descendant in self._descendants(node):
            if descendant.type != "call_expression":
                continue
            function = descendant.child_by_field_name("function")
            calls.append(self._text(function) or self._text(descendant.named_children[0]))
        return calls
