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
        return cls(
            entry_id=operation_id,
            fallthroughs=[_PendingEdge(operation_id)],
        )


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
        self.package_name = ""
        self._emitted_calls: set[tuple[int, int]] = set()
        self._call_bindings: dict[tuple[int, int], list[str]] = {}
        self._call_hosts: dict[tuple[int, int], str] = {}

    def lower(self, root: Any) -> GoLoweringResult:
        package_clause = next(
            (node for node in root.named_children if node.type == "package_clause"),
            None,
        )
        if package_clause is not None and package_clause.named_children:
            self.package_name = self._text(package_clause.named_children[-1])
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
        receiver = node.child_by_field_name("receiver")
        receiver_type_node = self._first_descendant(receiver, "type_identifier")
        receiver_type = self._text(receiver_type_node)
        return_types = self._return_types(node.child_by_field_name("result"))
        qualified_parts = [self.package_name, receiver_type, name]
        qualified_name = ".".join(part for part in qualified_parts if part)
        function = self._operation(
            OperationKind.FUNCTION,
            node,
            scope_id="",
            target=name,
            value=self._first_line(node),
            attributes={
                "symbol_name": name,
                "qualified_name": qualified_name or name,
                "namespace": self.package_name,
                "receiver_type": receiver_type,
                "return_types": return_types,
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

        self._emit_parameters(receiver, function, start=-1, receiver=True)
        self._emit_parameters(
            node.child_by_field_name("parameters"),
            function,
            start=0,
            receiver=False,
        )

        body = node.child_by_field_name("body")
        if body is None:
            return
        fragment = self._lower_sequence(self._statement_nodes(body), function.id)
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
                # Preserve unreachable operations for retrieval, but do not
                # fabricate CFG edges or loop exits from dead statements.
                continue
            for pending in sequence.fallthroughs:
                self._edge(pending.source_id, fragment.entry_id, pending.kind)
            sequence.fallthroughs = list(fragment.fallthroughs)
            sequence.breaks.extend(fragment.breaks)
            sequence.continues.extend(fragment.continues)
        return sequence

    def _lower_statement(self, node: Any, scope_id: str) -> _FlowFragment:
        node_type = node.type
        if node_type == "if_statement":
            return self._lower_branch(node, scope_id)
        if node_type == "expression_switch_statement":
            return self._lower_switch(node, scope_id)
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
            return _FlowFragment.simple(operation.id)
        if node_type == "return_statement":
            values = [self._text(child) for child in node.named_children]
            operation = self._operation(
                OperationKind.RETURN,
                node,
                scope_id,
                value=self._statement_value(node, "return"),
                attributes={"values": values},
            )
            self._register_call_hosts(node, operation.id)
            return _FlowFragment(entry_id=operation.id)
        if node_type == "continue_statement":
            operation = self._operation(OperationKind.CONTINUE, node, scope_id)
            return _FlowFragment(entry_id=operation.id, continues=[operation.id])
        if node_type == "break_statement":
            operation = self._operation(OperationKind.BREAK, node, scope_id)
            return _FlowFragment(entry_id=operation.id, breaks=[operation.id])
        if node_type == "go_statement":
            operation = self._call_like(OperationKind.SPAWN, node, scope_id)
            return _FlowFragment.simple(operation.id)
        if node_type == "defer_statement":
            operation = self._call_like(OperationKind.DEFER, node, scope_id)
            return _FlowFragment.simple(operation.id)
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
                attributes={"channel": target},
            )
            return _FlowFragment.simple(operation.id)
        if node_type == "select_statement":
            operation = self._operation(
                OperationKind.SELECT, node, scope_id, value=self._text(node)
            )
            body = node.child_by_field_name("body")
            if body is not None:
                fragment = self._lower_sequence(self._statement_nodes(body), scope_id)
                if fragment.entry_id is not None:
                    self._edge(operation.id, fragment.entry_id, OperationEdgeKind.BODY)
                    return _FlowFragment(
                        entry_id=operation.id,
                        fallthroughs=fragment.fallthroughs,
                        breaks=fragment.breaks,
                        continues=fragment.continues,
                    )
            return _FlowFragment.simple(operation.id)

        call = self._first_descendant(node, "call_expression")
        if call is not None:
            operation = self._call_like(OperationKind.CALL, call, scope_id, location_node=node)
            return _FlowFragment.simple(operation.id)
        return _FlowFragment()

    def _lower_branch(self, node: Any, scope_id: str) -> _FlowFragment:
        initializer = node.child_by_field_name("initializer")
        initializer_fragment = (
            self._lower_statement(initializer, scope_id)
            if initializer is not None
            else _FlowFragment()
        )
        condition = node.child_by_field_name("condition")
        if condition is None:
            condition = next(
                (
                    child
                    for child in node.named_children
                    if child.type not in {"block", "else_clause"}
                ),
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
        self._register_call_hosts(condition, branch.id)

        consequence = node.child_by_field_name("consequence")
        if consequence is None:
            consequence = next(
                (child for child in node.named_children if child.type == "block"), None
            )
        true_fragment = _FlowFragment()
        if consequence is not None:
            true_fragment = self._lower_sequence(
                self._statement_nodes(consequence),
                scope_id,
            )
        if true_fragment.entry_id is not None:
            self._edge(branch.id, true_fragment.entry_id, OperationEdgeKind.TRUE_BRANCH)
        else:
            true_fragment.fallthroughs.append(
                _PendingEdge(branch.id, OperationEdgeKind.TRUE_BRANCH)
            )

        alternative = node.child_by_field_name("alternative")
        false_fragment = _FlowFragment()
        if alternative is not None:
            false_fragment = self._lower_sequence(
                self._statement_nodes(alternative),
                scope_id,
            )
        if false_fragment.entry_id is not None:
            self._edge(branch.id, false_fragment.entry_id, OperationEdgeKind.FALSE_BRANCH)
        else:
            false_fragment.fallthroughs.append(
                _PendingEdge(branch.id, OperationEdgeKind.FALSE_BRANCH)
            )
        if initializer_fragment.entry_id is not None:
            for pending in initializer_fragment.fallthroughs:
                self._edge(pending.source_id, branch.id, pending.kind)

        return _FlowFragment(
            entry_id=initializer_fragment.entry_id or branch.id,
            fallthroughs=[
                *true_fragment.fallthroughs,
                *false_fragment.fallthroughs,
            ],
            breaks=[
                *initializer_fragment.breaks,
                *true_fragment.breaks,
                *false_fragment.breaks,
            ],
            continues=[
                *initializer_fragment.continues,
                *true_fragment.continues,
                *false_fragment.continues,
            ],
        )

    def _lower_switch(self, node: Any, scope_id: str) -> _FlowFragment:
        """Lower an expression switch while keeping case bodies in the CFG."""
        initializer = node.child_by_field_name("initializer")
        initializer_fragment = (
            self._lower_statement(initializer, scope_id)
            if initializer is not None
            else _FlowFragment()
        )
        value = node.child_by_field_name("value")
        cases = [
            child
            for child in node.named_children
            if child.type in {"expression_case", "default_case"}
        ]
        switch = self._operation(
            OperationKind.BRANCH,
            node,
            scope_id,
            value=self._text(value),
            operands=tuple(self._identifiers(value)),
            attributes={
                "switch": True,
                "cases": [
                    self._text(
                        next(
                            (
                                child
                                for child in case.named_children
                                if child.type != "statement_list"
                            ),
                            None,
                        )
                    )
                    for case in cases
                ],
                "calls": self._calls(value),
            },
        )
        self._register_call_hosts(value, switch.id)
        if initializer_fragment.entry_id is not None:
            for pending in initializer_fragment.fallthroughs:
                self._edge(pending.source_id, switch.id, pending.kind)

        lowered_cases: list[tuple[_FlowFragment, bool, OperationEdgeKind]] = []
        has_default = False
        for case in cases:
            statement_list = next(
                (
                    child
                    for child in case.named_children
                    if child.type == "statement_list"
                ),
                None,
            )
            statements = list(statement_list.named_children) if statement_list else []
            fragment = self._lower_sequence(statements, scope_id)
            is_default = case.type == "default_case"
            has_default = has_default or is_default
            edge_kind = (
                OperationEdgeKind.FALSE_BRANCH
                if is_default
                else OperationEdgeKind.TRUE_BRANCH
            )
            if fragment.entry_id is not None:
                self._edge(switch.id, fragment.entry_id, edge_kind)
            falls_through = bool(
                statements and statements[-1].type == "fallthrough_statement"
            )
            lowered_cases.append((fragment, falls_through, edge_kind))

        fallthroughs: list[_PendingEdge] = []
        continues = list(initializer_fragment.continues)
        for index, (fragment, falls_through, edge_kind) in enumerate(lowered_cases):
            continues.extend(fragment.continues)
            fallthroughs.extend(
                _PendingEdge(operation_id) for operation_id in fragment.breaks
            )
            if falls_through and index + 1 < len(lowered_cases):
                next_fragment = lowered_cases[index + 1][0]
                if next_fragment.entry_id is not None:
                    for pending in fragment.fallthroughs:
                        self._edge(pending.source_id, next_fragment.entry_id, pending.kind)
                    continue
            if fragment.entry_id is None:
                fallthroughs.append(_PendingEdge(switch.id, edge_kind))
            else:
                fallthroughs.extend(fragment.fallthroughs)
        if not has_default:
            fallthroughs.append(
                _PendingEdge(switch.id, OperationEdgeKind.FALSE_BRANCH)
            )

        return _FlowFragment(
            entry_id=initializer_fragment.entry_id or switch.id,
            fallthroughs=fallthroughs,
            continues=continues,
        )

    def _lower_loop(self, node: Any, scope_id: str) -> _FlowFragment:
        body = node.child_by_field_name("body")
        condition = node.child_by_field_name("condition")
        range_clause = self._first_descendant(node, "range_clause")
        infinite = condition is None and range_clause is None
        loop = self._operation(
            OperationKind.LOOP,
            node,
            scope_id,
            value=self._text(condition) or self._first_line(node),
            operands=tuple(self._identifiers(condition)),
            attributes={"infinite": infinite},
        )
        self._register_call_hosts(condition or range_clause, loop.id)
        body_fragment = _FlowFragment()
        if body is not None:
            body_fragment = self._lower_sequence(self._statement_nodes(body), scope_id)
        if body_fragment.entry_id is not None:
            self._edge(loop.id, body_fragment.entry_id, OperationEdgeKind.BODY)
            for pending in body_fragment.fallthroughs:
                self._edge(pending.source_id, loop.id, OperationEdgeKind.LOOP_BACK)
        else:
            self._edge(loop.id, loop.id, OperationEdgeKind.LOOP_BACK)
        for operation_id in body_fragment.continues:
            self._edge(operation_id, loop.id, OperationEdgeKind.LOOP_BACK)

        exits = [
            _PendingEdge(operation_id, OperationEdgeKind.LOOP_EXIT)
            for operation_id in body_fragment.breaks
        ]
        if not infinite:
            exits.append(_PendingEdge(loop.id, OperationEdgeKind.FALSE_BRANCH))
        return _FlowFragment(entry_id=loop.id, fallthroughs=exits)

    def _lower_assignment(self, node: Any, scope_id: str) -> _FlowFragment:
        target_node, value_node, declared_type = self._assignment_parts(node)
        target = self._text(target_node)
        value = self._text(value_node) or self._text(node)
        targets = self._binding_targets(target_node)
        inferred_type = self._composite_type(value_node)
        bindings = [
            {
                "target": name,
                "declared_type": declared_type if len(targets) == 1 else "",
                "inferred_type": inferred_type if len(targets) == 1 else "",
            }
            for name in targets
        ]
        for descendant in self._descendants(value_node):
            if descendant.type == "call_expression":
                self._call_bindings[(descendant.start_byte, descendant.end_byte)] = targets
        receives = any(
            descendant.type == "unary_expression" and self._text(descendant).startswith("<-")
            for descendant in self._descendants(node)
        )
        kind = OperationKind.RECEIVE if receives else OperationKind.ASSIGN
        receive_expression = next(
            (
                descendant
                for descendant in self._descendants(node)
                if descendant.type == "unary_expression" and self._text(descendant).startswith("<-")
            ),
            None,
        )
        channel = self._text(receive_expression).removeprefix("<-").strip()
        operation = self._operation(
            kind,
            node,
            scope_id,
            target=target,
            value=value,
            operands=tuple(self._identifiers(value_node or node)),
            attributes={
                "calls": self._calls(node),
                "bindings": bindings,
                **({"channel": channel} if channel else {}),
            },
        )
        self._register_call_hosts(value_node, operation.id)
        return _FlowFragment.simple(operation.id)

    def _call_like(
        self,
        kind: OperationKind,
        node: Any,
        scope_id: str,
        location_node: Any | None = None,
    ) -> SemanticOperation:
        call = (
            node
            if node.type == "call_expression"
            else self._first_descendant(node, "call_expression")
        )
        target = ""
        operands: tuple[str, ...] = ()
        attributes: dict[str, Any] = {
            "arguments": [],
            "argument_names": [],
            "receiver": "",
            "result_targets": [],
        }
        if call is not None:
            key = (call.start_byte, call.end_byte)
            self._emitted_calls.add(key)
            function = call.child_by_field_name("function")
            target = self._text(function) or (
                self._text(call.named_children[0]) if call.named_children else ""
            )
            arguments = call.child_by_field_name("arguments")
            operands = tuple(self._identifiers(arguments))
            argument_values = (
                [self._text(child) for child in arguments.named_children] if arguments else []
            )
            receiver = function.child_by_field_name("operand") if function is not None else None
            attributes = {
                "arguments": argument_values,
                "argument_names": [""] * len(argument_values),
                "receiver": self._text(receiver),
                "result_targets": self._call_bindings.get(key, []),
                "host_operation": self._call_hosts.get(key, ""),
            }
        return self._operation(
            kind,
            location_node or node,
            scope_id,
            target=target,
            value=self._text(call or node),
            operands=operands,
            attributes=attributes,
        )

    def _lower_remaining_calls(self, function_node: Any, scope_id: str) -> None:
        for descendant in self._descendants(function_node):
            if descendant.type != "call_expression":
                continue
            key = (descendant.start_byte, descendant.end_byte)
            if key in self._emitted_calls:
                continue
            call = self._call_like(OperationKind.CALL, descendant, scope_id)
            host_operation = self._call_hosts.get(key)
            if host_operation and host_operation != call.id:
                self._edge(call.id, host_operation, OperationEdgeKind.NEXT)

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

    def _emit_parameters(
        self,
        container: Any | None,
        function: SemanticOperation,
        *,
        start: int,
        receiver: bool,
    ) -> int:
        position = start
        if container is None:
            return position
        for declaration in container.named_children:
            if declaration.type not in {
                "parameter_declaration",
                "variadic_parameter_declaration",
            }:
                continue
            type_node = declaration.child_by_field_name("type")
            declared_type = self._text(type_node)
            identifiers = [
                self._text(child)
                for child in declaration.named_children
                if child.type == "identifier"
            ]
            names = identifiers or [f"$arg{max(position, 0)}"]
            for name in names:
                parameter = self._operation(
                    OperationKind.PARAMETER,
                    declaration,
                    scope_id=function.id,
                    target=name,
                    value=declared_type,
                    attributes={
                        "position": position,
                        "declared_type": declared_type,
                        "parameter_kind": (
                            "receiver"
                            if receiver
                            else (
                                "variadic"
                                if declaration.type == "variadic_parameter_declaration"
                                else "positional"
                            )
                        ),
                        "receiver": receiver,
                    },
                )
                self._edge(function.id, parameter.id, OperationEdgeKind.CONTAINS)
                position += 1
        return position

    def _return_types(self, result: Any | None) -> list[str]:
        if result is None:
            return []
        if result.type != "parameter_list":
            return [self._text(result)]
        types: list[str] = []
        for declaration in result.named_children:
            if declaration.type not in {
                "parameter_declaration",
                "variadic_parameter_declaration",
            }:
                continue
            type_text = self._text(declaration.child_by_field_name("type"))
            names = [child for child in declaration.named_children if child.type == "identifier"]
            types.extend([type_text] * max(1, len(names)))
        return types

    def _register_call_hosts(self, node: Any | None, host_operation: str) -> None:
        if node is None:
            return
        for descendant in self._descendants(node):
            if descendant.type == "call_expression":
                self._call_hosts[(descendant.start_byte, descendant.end_byte)] = host_operation

    def _assignment_parts(self, node: Any) -> tuple[Any | None, Any | None, str]:
        if node.type == "var_declaration":
            spec = next(
                (child for child in node.named_children if child.type == "var_spec"),
                None,
            )
            if spec is None:
                return None, None, ""
            return (
                spec.child_by_field_name("name"),
                spec.child_by_field_name("value"),
                self._text(spec.child_by_field_name("type")),
            )
        return (
            node.child_by_field_name("left"),
            node.child_by_field_name("right"),
            "",
        )

    def _binding_targets(self, node: Any | None) -> list[str]:
        if node is None:
            return []
        if node.type == "identifier":
            return [self._text(node)]
        return [self._text(child) for child in node.named_children if child.type == "identifier"]

    def _composite_type(self, node: Any | None) -> str:
        composite = self._first_descendant(node, "composite_literal")
        if composite is None:
            return ""
        return self._text(composite.child_by_field_name("type"))

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
                (
                    child
                    for child in node.named_children
                    if child.type in {"statement_list", "expression_case"}
                ),
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
        return value[len(keyword) :].strip() if value.startswith(keyword) else value

    def _first_line(self, node: Any) -> str:
        return self._text(node).splitlines()[0].strip()

    def _text(self, node: Any | None) -> str:
        if node is None:
            return ""
        return self.source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

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
        return [
            operator
            for operator in ("==", "!=", ">=", "<=", "&&", "||", ">", "<", "!")
            if operator in source
        ]

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
        return [
            self._text(descendant)
            for descendant in self._descendants(node)
            if descendant.type in literal_types
        ]

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
