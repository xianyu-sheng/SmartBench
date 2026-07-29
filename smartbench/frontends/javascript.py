"""JavaScript/TypeScript lowering into the common SemanticIR operation model.

This frontend intentionally shares one implementation for both grammars.  The
syntax trees differ mainly in type annotations; keeping the lowering here
prevents JS and TS rules from drifting into two language-specific backends.
The result is conservative: dynamic dispatch, exception edges, and precise
type checking remain explicitly partial capabilities.
"""

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

_FUNCTION_TYPES = {
    "function_declaration",
    "generator_function_declaration",
    "method_definition",
    "function_expression",
    "arrow_function",
}
_BRANCH_TYPES = {"if_statement", "switch_statement", "ternary_expression"}
_LOOP_TYPES = {
    "for_statement",
    "for_in_statement",
    "for_of_statement",
    "while_statement",
    "do_statement",
}
_DECLARATION_TYPES = {"lexical_declaration", "variable_declaration"}
_PARAMETER_TYPES = {
    "identifier",
    "required_parameter",
    "optional_parameter",
    "rest_pattern",
    "assignment_pattern",
    "pair_pattern",
}


@dataclass
class JavaScriptLoweringResult:
    """Result of lowering JavaScript or TypeScript source units."""

    operations: list[SemanticOperation] = field(default_factory=list)
    edges: list[OperationEdge] = field(default_factory=list)
    facts: list[SemanticFact] = field(default_factory=list)
    files_analyzed: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _PendingEdge:
    source_id: str
    kind: OperationEdgeKind = OperationEdgeKind.NEXT


@dataclass
class _FlowFragment:
    entry_id: str | None = None
    fallthroughs: list[_PendingEdge] = field(default_factory=list)
    breaks: list[str] = field(default_factory=list)
    continues: list[str] = field(default_factory=list)

    @classmethod
    def simple(cls, operation_id: str) -> "_FlowFragment":
        return cls(entry_id=operation_id, fallthroughs=[_PendingEdge(operation_id)])


class JavaScriptSemanticLowerer:
    """Lower JS/TS source units into language-neutral operations."""

    def lower(self, ir: SemanticIR) -> JavaScriptLoweringResult:
        result = JavaScriptLoweringResult()
        for file_path, unit in sorted(ir.source_units.items()):
            language = unit.language
            if language not in {"javascript", "typescript"}:
                continue
            source = ir.read_source(file_path)
            if source is None:
                result.errors.append(f"unable to read JavaScript source: {file_path}")
                continue
            parser = get_parser(language)
            if parser is None:
                result.errors.append(f"tree-sitter {language} parser is unavailable")
                continue
            try:
                tree = parser.parse(source.encode("utf-8"))
                lowered = _JavaScriptFileLowerer(file_path, source, language).lower(
                    tree.root_node
                )
            except Exception as exc:  # parser failures are reported, never treated as clean
                result.errors.append(f"{file_path}: {exc}")
                continue
            result.operations.extend(lowered.operations)
            result.edges.extend(lowered.edges)
            result.facts.extend(lowered.facts)
            result.files_analyzed += 1
        return result


class _JavaScriptFileLowerer:
    def __init__(self, file_path: str, source: str, language: str) -> None:
        self.file_path = file_path
        self.source = source
        self.source_bytes = source.encode("utf-8")
        self.language = language
        self.operations: list[SemanticOperation] = []
        self.edges: list[OperationEdge] = []
        self.facts: list[SemanticFact] = []
        self._ordinal = 0
        self._emitted_calls: set[tuple[int, int]] = set()
        self._call_bindings: dict[tuple[int, int], list[str]] = {}
        self._call_hosts: dict[tuple[int, int], str] = {}

    def lower(self, root: Any) -> JavaScriptLoweringResult:
        self._lower_container(root, namespace="")
        return JavaScriptLoweringResult(
            operations=self.operations,
            edges=self.edges,
            facts=self.facts,
            files_analyzed=1,
        )

    def _lower_container(self, node: Any, namespace: str) -> None:
        if node.type in _FUNCTION_TYPES:
            self._lower_function(node, namespace)
            return
        if node.type in {"class_declaration", "class"}:
            name = self._text(node.child_by_field_name("name"))
            body = node.child_by_field_name("body")
            if body is not None:
                self._lower_container(body, ".".join(p for p in (namespace, name) if p))
            return
        for child in node.named_children:
            if child.type == "export_statement":
                declaration = child.child_by_field_name("declaration")
                if declaration is not None:
                    self._lower_container(declaration, namespace)
                continue
            if child.type in {"class_declaration", "class"}:
                name = self._text(child.child_by_field_name("name"))
                body = child.child_by_field_name("body")
                if body is not None:
                    self._lower_container(body, ".".join(p for p in (namespace, name) if p))
                continue
            if child.type in _FUNCTION_TYPES:
                self._lower_function(child, namespace)
            elif child.type in _DECLARATION_TYPES:
                self._lower_arrow_declarations(child, namespace)
            else:
                # Functions can be nested in blocks or namespaces.  Recurse
                # only for containers, not arbitrary expressions, to avoid
                # emitting a function twice.
                if child.type in {"statement_block", "program", "class_body", "module"}:
                    self._lower_container(child, namespace)

    def _lower_arrow_declarations(self, node: Any, namespace: str) -> None:
        for declarator in node.named_children:
            if declarator.type != "variable_declarator":
                continue
            value = declarator.child_by_field_name("value")
            if value is not None and value.type in _FUNCTION_TYPES:
                self._lower_function(
                    value,
                    namespace,
                    self._text(declarator.child_by_field_name("name")),
                )

    def _lower_function(
        self,
        node: Any,
        namespace: str,
        assigned_name: str = "",
    ) -> None:
        name = self._text(node.child_by_field_name("name")) or assigned_name
        if not name and node.type == "method_definition":
            name = self._text(node.child_by_field_name("name"))
        name = name or "<anonymous>"
        qualified_name = ".".join(part for part in (namespace, name) if part)
        function = self._operation(
            OperationKind.FUNCTION,
            node,
            scope_id="",
            target=name,
            value=self._first_line(node),
            attributes={
                "symbol_name": name,
                "qualified_name": qualified_name or name,
                "namespace": namespace,
                "receiver_type": namespace.rsplit(".", 1)[-1] if namespace else "",
                "return_types": self._return_types(node),
                "async": self._first_line(node).startswith("async "),
            },
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
        self._emit_parameters(node.child_by_field_name("parameters"), function)

        body = node.child_by_field_name("body")
        if body is None:
            return
        if body.type == "statement_block":
            fragment = self._lower_sequence(list(body.named_children), function.id)
        else:
            returned = self._operation(
                OperationKind.RETURN,
                body,
                function.id,
                value=self._text(body),
                operands=tuple(self._identifiers(body)),
                attributes={"values": [self._text(body)]},
            )
            self._register_call_hosts(body, returned.id)
            fragment = _FlowFragment.simple(returned.id)
        if fragment.entry_id is not None:
            self._edge(function.id, fragment.entry_id, OperationEdgeKind.CONTAINS)
        self._lower_remaining_calls(node, function.id)

    def _lower_sequence(self, statements: list[Any], scope_id: str) -> _FlowFragment:
        sequence = _FlowFragment()
        for statement in statements:
            fragment = self._lower_statement(statement, scope_id)
            if fragment.entry_id is None:
                continue
            if sequence.entry_id is None:
                sequence.entry_id = fragment.entry_id
                sequence.fallthroughs = list(fragment.fallthroughs)
                sequence.breaks.extend(fragment.breaks)
                sequence.continues.extend(fragment.continues)
                continue
            if not sequence.fallthroughs:
                continue
            for pending in sequence.fallthroughs:
                self._edge(pending.source_id, fragment.entry_id, pending.kind)
            sequence.fallthroughs = list(fragment.fallthroughs)
            sequence.breaks.extend(fragment.breaks)
            sequence.continues.extend(fragment.continues)
        return sequence

    def _lower_statement(self, node: Any, scope_id: str) -> _FlowFragment:
        node_type = node.type
        if node_type in _BRANCH_TYPES:
            return self._lower_branch(node, scope_id)
        if node_type in _LOOP_TYPES:
            return self._lower_loop(node, scope_id)
        if node_type in _DECLARATION_TYPES:
            return self._lower_declaration(node, scope_id)
        if node_type == "return_statement":
            value = node.child_by_field_name("argument")
            if value is None and node.named_children:
                value = node.named_children[0]
            value_text = self._text(value) or self._text(node)
            operation = self._operation(
                OperationKind.RETURN,
                node,
                scope_id,
                value=value_text,
                operands=tuple(self._identifiers(value)),
                attributes={"values": [value_text] if value_text else []},
            )
            self._register_call_hosts(value, operation.id)
            return _FlowFragment(entry_id=operation.id)
        if node_type == "continue_statement":
            operation = self._operation(OperationKind.CONTINUE, node, scope_id)
            return _FlowFragment(entry_id=operation.id, continues=[operation.id])
        if node_type == "break_statement":
            operation = self._operation(OperationKind.BREAK, node, scope_id)
            return _FlowFragment(entry_id=operation.id, breaks=[operation.id])
        if node_type in {"expression_statement", "expression_clause"}:
            expression = node.named_children[0] if node.named_children else node
            if expression.type == "update_expression":
                operation = self._operation(
                    OperationKind.UPDATE,
                    expression,
                    scope_id,
                    target=self._text(expression.child_by_field_name("argument")),
                    value=self._text(expression),
                    operands=tuple(self._identifiers(expression)),
                )
                return _FlowFragment.simple(operation.id)
            if expression.type in {"assignment_expression", "augmented_assignment_expression"}:
                operation = self._assignment(expression, scope_id)
                return _FlowFragment.simple(operation.id)
            call = self._first_descendant(expression, "call_expression")
            if call is not None:
                return _FlowFragment.simple(self._call_like(call, scope_id).id)
        call = self._first_descendant(node, "call_expression")
        if call is not None:
            return _FlowFragment.simple(self._call_like(call, scope_id, location_node=node).id)
        return _FlowFragment()

    def _lower_declaration(self, node: Any, scope_id: str) -> _FlowFragment:
        sequence = _FlowFragment()
        for declarator in node.named_children:
            if declarator.type != "variable_declarator":
                continue
            target_node = declarator.child_by_field_name("name")
            value_node = declarator.child_by_field_name("value")
            targets = self._binding_targets(target_node)
            for call in self._descendants(value_node):
                if call.type == "call_expression":
                    self._call_bindings[(call.start_byte, call.end_byte)] = targets
            operation = self._operation(
                OperationKind.ASSIGN,
                declarator,
                scope_id,
                target=self._text(target_node),
                value=self._text(value_node),
                operands=tuple(self._identifiers(value_node)),
                attributes={
                    "bindings": [
                        {
                            "target": target,
                            "declared_type": self._declared_type(declarator),
                            "inferred_type": "",
                        }
                        for target in targets
                    ],
                    "calls": self._calls(value_node),
                },
            )
            self._register_call_hosts(value_node, operation.id)
            fragment = _FlowFragment.simple(operation.id)
            if sequence.entry_id is None:
                sequence = fragment
            else:
                for pending in sequence.fallthroughs:
                    self._edge(pending.source_id, fragment.entry_id, pending.kind)
                sequence.fallthroughs = fragment.fallthroughs
        return sequence

    def _assignment(self, node: Any, scope_id: str) -> SemanticOperation:
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        targets = self._binding_targets(left)
        for descendant in self._descendants(right):
            if descendant.type == "call_expression":
                self._call_bindings[(descendant.start_byte, descendant.end_byte)] = targets
        operation = self._operation(
            OperationKind.ASSIGN,
            node,
            scope_id,
            target=self._text(left),
            value=self._text(right),
            operands=tuple(self._identifiers(right)),
            attributes={
                "bindings": [
                    {"target": target, "declared_type": "", "inferred_type": ""}
                    for target in targets
                ],
                "calls": self._calls(right),
            },
        )
        self._register_call_hosts(right, operation.id)
        return operation

    def _lower_branch(self, node: Any, scope_id: str) -> _FlowFragment:
        condition = node.child_by_field_name("condition")
        if condition is None and node.type == "ternary_expression":
            condition = node.child_by_field_name("condition")
        if condition is None:
            condition = node
        branch = self._operation(
            OperationKind.BRANCH,
            node,
            scope_id,
            value=self._text(condition),
            operands=tuple(self._identifiers(condition)),
            attributes={
                "operators": self._operators(condition),
                "literals": self._literals(condition),
                "calls": self._calls(condition),
            },
        )
        self._register_call_hosts(condition, branch.id)
        consequence = node.child_by_field_name("consequence")
        if consequence is None and node.type == "ternary_expression":
            consequence = node.child_by_field_name("consequence")
        alternative = node.child_by_field_name("alternative")
        true_fragment = self._nested_fragment(consequence, scope_id)
        false_fragment = self._nested_fragment(alternative, scope_id)
        if true_fragment.entry_id:
            self._edge(branch.id, true_fragment.entry_id, OperationEdgeKind.TRUE_BRANCH)
        else:
            true_fragment.fallthroughs.append(_PendingEdge(branch.id, OperationEdgeKind.TRUE_BRANCH))
        if false_fragment.entry_id:
            self._edge(branch.id, false_fragment.entry_id, OperationEdgeKind.FALSE_BRANCH)
        else:
            false_fragment.fallthroughs.append(_PendingEdge(branch.id, OperationEdgeKind.FALSE_BRANCH))
        return _FlowFragment(
            entry_id=branch.id,
            fallthroughs=[*true_fragment.fallthroughs, *false_fragment.fallthroughs],
            breaks=[*true_fragment.breaks, *false_fragment.breaks],
            continues=[*true_fragment.continues, *false_fragment.continues],
        )

    def _lower_loop(self, node: Any, scope_id: str) -> _FlowFragment:
        condition = node.child_by_field_name("condition")
        if condition is None:
            condition = node.child_by_field_name("right") or node.child_by_field_name("left")
        loop = self._operation(
            OperationKind.LOOP,
            node,
            scope_id,
            value=self._text(condition) or self._first_line(node),
            operands=tuple(self._identifiers(condition)),
            attributes={"infinite": condition is None, "calls": self._calls(condition)},
        )
        self._register_call_hosts(condition, loop.id)
        body = node.child_by_field_name("body")
        body_fragment = self._nested_fragment(body, scope_id)
        if body_fragment.entry_id:
            self._edge(loop.id, body_fragment.entry_id, OperationEdgeKind.BODY)
            for pending in body_fragment.fallthroughs:
                self._edge(pending.source_id, loop.id, OperationEdgeKind.LOOP_BACK)
        else:
            self._edge(loop.id, loop.id, OperationEdgeKind.LOOP_BACK)
        for operation_id in body_fragment.continues:
            self._edge(operation_id, loop.id, OperationEdgeKind.LOOP_BACK)
        exits = [_PendingEdge(op, OperationEdgeKind.LOOP_EXIT) for op in body_fragment.breaks]
        if condition is not None:
            exits.append(_PendingEdge(loop.id, OperationEdgeKind.FALSE_BRANCH))
        return _FlowFragment(entry_id=loop.id, fallthroughs=exits)

    def _nested_fragment(self, node: Any | None, scope_id: str) -> _FlowFragment:
        if node is None:
            return _FlowFragment()
        if node.type in {"statement_block", "switch_body", "else_clause", "case_clause"}:
            return self._lower_sequence(list(node.named_children), scope_id)
        return self._lower_statement(node, scope_id)

    def _call_like(
        self,
        call: Any,
        scope_id: str,
        location_node: Any | None = None,
    ) -> SemanticOperation:
        key = (call.start_byte, call.end_byte)
        self._emitted_calls.add(key)
        function = call.child_by_field_name("function")
        target = self._text(function)
        arguments = call.child_by_field_name("arguments")
        values = [self._text(child) for child in arguments.named_children] if arguments else []
        receiver = function.child_by_field_name("object") if function is not None else None
        return self._operation(
            OperationKind.CALL,
            location_node or call,
            scope_id,
            target=target,
            value=self._text(call),
            operands=tuple(self._identifiers(arguments)),
            attributes={
                "arguments": values,
                "argument_names": [""] * len(values),
                "receiver": self._text(receiver),
                "result_targets": self._call_bindings.get(key, []),
                "host_operation": self._call_hosts.get(key, ""),
            },
        )

    def _lower_remaining_calls(self, function_node: Any, scope_id: str) -> None:
        for descendant in self._descendants(function_node):
            if descendant.type != "call_expression":
                continue
            key = (descendant.start_byte, descendant.end_byte)
            if key not in self._emitted_calls:
                self._call_like(descendant, scope_id)

    def _emit_parameters(self, container: Any | None, function: SemanticOperation) -> None:
        if container is None:
            return
        position = 0
        for parameter in container.named_children:
            if parameter.type not in _PARAMETER_TYPES:
                continue
            pattern = parameter.child_by_field_name("pattern") or parameter
            name = self._text(pattern)
            declared_type = self._type_text(parameter.child_by_field_name("type"))
            operation = self._operation(
                OperationKind.PARAMETER,
                parameter,
                function.id,
                target=name,
                value=declared_type,
                attributes={
                    "position": position,
                    "declared_type": declared_type,
                    "parameter_kind": "variadic" if parameter.type == "rest_pattern" else "positional",
                    "receiver": False,
                },
            )
            self._edge(function.id, operation.id, OperationEdgeKind.CONTAINS)
            position += 1

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
            id=SemanticOperation.make_id(self.file_path, location.line_start, kind, self._ordinal),
            kind=kind,
            language=self.language,
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
            OperationKind.ASSIGN: FactKind.WRITES,
            OperationKind.UPDATE: FactKind.WRITES,
            OperationKind.BRANCH: FactKind.CONTROLS,
            OperationKind.LOOP: FactKind.CONTROLS,
            OperationKind.RETURN: FactKind.STATE_TRANSITION,
            OperationKind.CONTINUE: FactKind.STATE_TRANSITION,
            OperationKind.BREAK: FactKind.STATE_TRANSITION,
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
            source="javascript_frontend",
        )

    def _return_types(self, node: Any) -> list[str]:
        result = node.child_by_field_name("return_type")
        return [self._type_text(result)] if result is not None else []

    def _declared_type(self, node: Any | None) -> str:
        if node is None:
            return ""
        type_node = node.child_by_field_name("type")
        return self._type_text(type_node)

    def _type_text(self, node: Any | None) -> str:
        return self._text(node).removeprefix(":").strip()

    def _binding_targets(self, node: Any | None) -> list[str]:
        if node is None:
            return []
        if node.type in {"identifier", "shorthand_property_identifier_pattern"}:
            return [self._text(node)]
        return [self._text(child) for child in node.named_children if child.type == "identifier"]

    def _register_call_hosts(self, node: Any | None, host_operation: str) -> None:
        for descendant in self._descendants(node):
            if descendant.type == "call_expression":
                self._call_hosts[(descendant.start_byte, descendant.end_byte)] = host_operation

    def _identifiers(self, node: Any | None) -> list[str]:
        if node is None:
            return []
        return [self._text(descendant) for descendant in self._descendants(node) if descendant.type == "identifier"]

    def _calls(self, node: Any | None) -> list[str]:
        return [self._text(descendant) for descendant in self._descendants(node) if descendant.type == "call_expression"]

    def _operators(self, node: Any | None) -> list[str]:
        if node is None:
            return []
        return [self._text(child) for child in node.children if child.type in {"!", "&&", "||", "==", "===", "!=", "!==", ">", "<", ">=", "<="}]

    def _literals(self, node: Any | None) -> list[str]:
        if node is None:
            return []
        literal_types = {"true", "false", "null", "undefined", "number", "string", "template_string"}
        return [self._text(descendant) for descendant in self._descendants(node) if descendant.type in literal_types]

    def _first_line(self, node: Any) -> str:
        return self._text(node).splitlines()[0].strip()

    def _text(self, node: Any | None) -> str:
        if node is None:
            return ""
        return self.source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

    def _descendants(self, node: Any | None) -> list[Any]:
        if node is None:
            return []
        result: list[Any] = [node]
        stack = list(node.named_children)
        while stack:
            current = stack.pop(0)
            result.append(current)
            stack[0:0] = list(current.named_children)
        return result

    def _first_descendant(self, node: Any | None, node_type: str) -> Any | None:
        if node is None:
            return None
        if node.type == node_type:
            return node
        return next((child for child in self._descendants(node) if child.type == node_type), None)
