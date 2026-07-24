"""Python AST lowering into language-neutral semantic operations."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import PurePosixPath

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


@dataclass
class PythonLoweringResult:
    """Result of lowering Python syntax into the common operation model."""

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


class PythonSemanticLowerer:
    """Lower Python AST nodes to the same finite operations used by Go."""

    def lower(self, ir: SemanticIR) -> PythonLoweringResult:
        result = PythonLoweringResult()
        for file_path, unit in sorted(ir.source_units.items()):
            if unit.language != "python" and not file_path.endswith(".py"):
                continue
            source = ir.read_source(file_path)
            if source is None:
                result.errors.append(f"unable to read Python source: {file_path}")
                continue
            try:
                tree = ast.parse(source, filename=file_path)
                lowered = _PythonFileLowerer(file_path, source).lower(tree)
            except (SyntaxError, ValueError) as exc:
                result.errors.append(f"{file_path}: {exc}")
                continue
            result.operations.extend(lowered.operations)
            result.edges.extend(lowered.edges)
            result.facts.extend(lowered.facts)
            result.files_analyzed += 1
        return result


class _PythonFileLowerer:
    def __init__(self, file_path: str, source: str) -> None:
        self.file_path = file_path
        self.source = source
        self.lines = source.splitlines()
        self.operations: list[SemanticOperation] = []
        self.edges: list[OperationEdge] = []
        self.facts: list[SemanticFact] = []
        self._ordinal = 0
        self.parents: dict[ast.AST, ast.AST] = {}
        self._emitted_calls: set[int] = set()
        self.constructor_types: set[str] = set()
        module_path = PurePosixPath(file_path.replace("\\", "/")).with_suffix("")
        module_parts = list(module_path.parts)
        if module_parts and module_parts[-1] == "__init__":
            module_parts.pop()
        self.module_name = ".".join(module_parts)

    def lower(self, root: ast.Module) -> PythonLoweringResult:
        self.parents = {
            child: parent for parent in ast.walk(root) for child in ast.iter_child_nodes(parent)
        }
        self.constructor_types = self._module_constructor_types(root)
        functions = [
            node
            for node in ast.walk(root)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        functions.sort(key=lambda node: (node.lineno, node.col_offset, node.name))
        for function in functions:
            self._lower_function(
                function,
                self._qualified_function_name(function, self.parents),
            )
        return PythonLoweringResult(
            operations=self.operations,
            edges=self.edges,
            facts=self.facts,
            files_analyzed=1,
        )

    def _lower_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        lexical_name: str,
    ) -> None:
        receiver_type = self._owner_class_name(node)
        return_annotation = self._unparse(node.returns)
        qualified_name = ".".join(part for part in (self.module_name, lexical_name) if part)
        function = self._operation(
            OperationKind.FUNCTION,
            node,
            scope_id="",
            target=node.name,
            value=self._first_line(node),
            attributes={
                "async": isinstance(node, ast.AsyncFunctionDef),
                "symbol_name": node.name,
                "qualified_name": qualified_name or node.name,
                "namespace": self.module_name,
                "lexical_name": lexical_name,
                "receiver_type": receiver_type,
                "return_types": [return_annotation] if return_annotation else [],
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

        for position, (argument, parameter_kind) in enumerate(self._parameter_specs(node)):
            is_receiver = bool(receiver_type and position == 0 and argument.arg in {"self", "cls"})
            declared_type = self._unparse(argument.annotation)
            if is_receiver and not declared_type:
                declared_type = receiver_type
            parameter = self._operation(
                OperationKind.PARAMETER,
                argument,
                function.id,
                target=argument.arg,
                value=declared_type,
                attributes={
                    "position": position,
                    "declared_type": declared_type,
                    "parameter_kind": parameter_kind,
                    "receiver": is_receiver,
                },
            )
            self._edge(function.id, parameter.id, OperationEdgeKind.CONTAINS)

        fragment = self._lower_sequence(node.body, function.id)
        if fragment.entry_id is not None:
            self._edge(function.id, fragment.entry_id, OperationEdgeKind.CONTAINS)
        self._lower_remaining_calls(node, function.id)

    @staticmethod
    def _qualified_function_name(
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        parents: dict[ast.AST, ast.AST],
    ) -> str:
        owners: list[str] = []
        parent = parents.get(node)
        while parent is not None:
            if isinstance(parent, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                owners.append(parent.name)
            parent = parents.get(parent)
        return ".".join([*reversed(owners), node.name])

    def _lower_sequence(self, statements: list[ast.stmt], scope_id: str) -> _FlowFragment:
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

    def _lower_statement(self, node: ast.stmt, scope_id: str) -> _FlowFragment:
        if isinstance(node, ast.If):
            return self._lower_branch(node, scope_id)
        if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            return self._lower_loop(node, scope_id)
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            return self._lower_assignment(node, scope_id)
        if isinstance(node, ast.AugAssign):
            operation = self._operation(
                OperationKind.UPDATE,
                node,
                scope_id,
                target=self._unparse(node.target),
                value=self._unparse(node),
                operands=tuple(self._identifiers(node.value)),
                attributes={"operator": self._operator(node.op)},
            )
            return _FlowFragment.simple(operation.id)
        if isinstance(node, ast.Return):
            values = (
                [self._unparse(item) for item in node.value.elts]
                if isinstance(node.value, ast.Tuple)
                else ([self._unparse(node.value)] if node.value is not None else [])
            )
            operation = self._operation(
                OperationKind.RETURN,
                node,
                scope_id,
                value=self._unparse(node.value),
                operands=tuple(self._identifiers(node.value)),
                attributes={"values": values},
            )
            return _FlowFragment(entry_id=operation.id)
        if isinstance(node, ast.Continue):
            operation = self._operation(OperationKind.CONTINUE, node, scope_id)
            return _FlowFragment(entry_id=operation.id, continues=[operation.id])
        if isinstance(node, ast.Break):
            operation = self._operation(OperationKind.BREAK, node, scope_id)
            return _FlowFragment(entry_id=operation.id, breaks=[operation.id])
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            return _FlowFragment.simple(
                self._lower_call(node.value, scope_id, location_node=node).id
            )
        return _FlowFragment()

    def _lower_branch(self, node: ast.If, scope_id: str) -> _FlowFragment:
        branch = self._operation(
            OperationKind.BRANCH,
            node,
            scope_id,
            value=self._unparse(node.test),
            operands=tuple(self._identifiers(node.test)),
            attributes={
                "operators": self._operators(node.test),
                "literals": self._literals(node.test),
                "calls": self._calls(node.test),
            },
        )
        consequence = self._lower_sequence(node.body, scope_id)
        if consequence.entry_id is not None:
            self._edge(branch.id, consequence.entry_id, OperationEdgeKind.TRUE_BRANCH)
        else:
            consequence.fallthroughs.append(_PendingEdge(branch.id, OperationEdgeKind.TRUE_BRANCH))
        alternative = self._lower_sequence(node.orelse, scope_id)
        if alternative.entry_id is not None:
            self._edge(branch.id, alternative.entry_id, OperationEdgeKind.FALSE_BRANCH)
        else:
            alternative.fallthroughs.append(_PendingEdge(branch.id, OperationEdgeKind.FALSE_BRANCH))
        return _FlowFragment(
            entry_id=branch.id,
            fallthroughs=[*consequence.fallthroughs, *alternative.fallthroughs],
            breaks=[*consequence.breaks, *alternative.breaks],
            continues=[*consequence.continues, *alternative.continues],
        )

    def _lower_loop(
        self,
        node: ast.For | ast.AsyncFor | ast.While,
        scope_id: str,
    ) -> _FlowFragment:
        if isinstance(node, ast.While):
            value_node: ast.AST | None = node.test
        else:
            value_node = node.iter
        infinite = (
            isinstance(node, ast.While)
            and isinstance(node.test, ast.Constant)
            and node.test.value is True
        )
        loop = self._operation(
            OperationKind.LOOP,
            node,
            scope_id,
            value=self._unparse(value_node),
            operands=tuple(self._identifiers(value_node)),
            attributes={"infinite": infinite},
        )
        body = self._lower_sequence(node.body, scope_id)
        if body.entry_id is not None:
            self._edge(loop.id, body.entry_id, OperationEdgeKind.BODY)
            for pending in body.fallthroughs:
                self._edge(pending.source_id, loop.id, OperationEdgeKind.LOOP_BACK)
        else:
            self._edge(loop.id, loop.id, OperationEdgeKind.LOOP_BACK)
        for operation_id in body.continues:
            self._edge(operation_id, loop.id, OperationEdgeKind.LOOP_BACK)

        exits = [
            _PendingEdge(operation_id, OperationEdgeKind.LOOP_EXIT) for operation_id in body.breaks
        ]
        if not infinite:
            exits.append(_PendingEdge(loop.id, OperationEdgeKind.FALSE_BRANCH))
        return _FlowFragment(entry_id=loop.id, fallthroughs=exits)

    def _lower_assignment(
        self,
        node: ast.Assign | ast.AnnAssign,
        scope_id: str,
    ) -> _FlowFragment:
        if isinstance(node, ast.Assign):
            target = ", ".join(self._unparse(item) for item in node.targets)
            targets = [name for item in node.targets for name in self._assignment_targets(item)]
            value_node = node.value
            declared_type = ""
        else:
            target = self._unparse(node.target)
            targets = self._assignment_targets(node.target)
            value_node = node.value
            declared_type = self._unparse(node.annotation)
        inferred_type = self._constructor_type(value_node)
        bindings = [
            {
                "target": name,
                "declared_type": declared_type if len(targets) == 1 else "",
                "inferred_type": inferred_type if len(targets) == 1 else "",
            }
            for name in targets
        ]
        operation = self._operation(
            OperationKind.ASSIGN,
            node,
            scope_id,
            target=target,
            value=self._unparse(value_node),
            operands=tuple(self._identifiers(value_node)),
            attributes={
                "calls": self._calls(value_node),
                "bindings": bindings,
            },
        )
        return _FlowFragment.simple(operation.id)

    def _lower_call(
        self,
        node: ast.Call,
        scope_id: str,
        location_node: ast.AST | None = None,
    ) -> SemanticOperation:
        self._emitted_calls.add(id(node))
        operands: list[str] = []
        for argument in [*node.args, *(keyword.value for keyword in node.keywords)]:
            operands.extend(self._identifiers(argument))
        arguments = [self._unparse(argument) for argument in node.args]
        arguments.extend(self._unparse(keyword.value) for keyword in node.keywords)
        argument_names = [""] * len(node.args)
        argument_names.extend(keyword.arg or "**" for keyword in node.keywords)
        target = self._unparse(node.func)
        return self._operation(
            OperationKind.CALL,
            location_node or node,
            scope_id,
            target=target,
            value=self._unparse(node),
            operands=tuple(operands),
            attributes={
                "arguments": arguments,
                "argument_names": argument_names,
                "receiver": target.rsplit(".", 1)[0] if "." in target else "",
                "result_targets": self._call_result_targets(node),
            },
        )

    def _lower_remaining_calls(
        self,
        function_node: ast.FunctionDef | ast.AsyncFunctionDef,
        scope_id: str,
    ) -> None:
        for descendant in ast.walk(function_node):
            if not isinstance(descendant, ast.Call) or id(descendant) in self._emitted_calls:
                continue
            parent = self.parents.get(descendant)
            while parent is not None and not isinstance(
                parent,
                (ast.FunctionDef, ast.AsyncFunctionDef),
            ):
                parent = self.parents.get(parent)
            if parent is function_node:
                self._lower_call(descendant, scope_id)

    def _operation(
        self,
        kind: OperationKind,
        node: ast.AST,
        scope_id: str,
        target: str = "",
        value: str = "",
        operands: tuple[str, ...] = (),
        attributes: dict | None = None,
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
            language="python",
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

    def _location(self, node: ast.AST) -> EvidenceRef:
        line_start = max(1, getattr(node, "lineno", 1))
        line_end = max(line_start, getattr(node, "end_lineno", line_start) or line_start)
        return EvidenceRef(
            file_path=self.file_path,
            line_start=line_start,
            line_end=line_end,
            column_start=getattr(node, "col_offset", None),
            column_end=getattr(node, "end_col_offset", None),
            snippet=ast.get_source_segment(self.source, node) or self._line(line_start),
            source="python_frontend",
        )

    def _first_line(self, node: ast.AST) -> str:
        return (ast.get_source_segment(self.source, node) or self._line(node.lineno)).splitlines()[
            0
        ]

    def _line(self, line_number: int) -> str:
        index = line_number - 1
        return self.lines[index] if 0 <= index < len(self.lines) else ""

    def _owner_class_name(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> str:
        parent = self.parents.get(node)
        while parent is not None:
            if isinstance(parent, ast.ClassDef):
                return parent.name
            if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return ""
            parent = self.parents.get(parent)
        return ""

    @staticmethod
    def _parameter_specs(
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> list[tuple[ast.arg, str]]:
        specs = [
            *((argument, "positional_only") for argument in node.args.posonlyargs),
            *((argument, "positional") for argument in node.args.args),
        ]
        if node.args.vararg is not None:
            specs.append((node.args.vararg, "vararg"))
        specs.extend((argument, "keyword_only") for argument in node.args.kwonlyargs)
        if node.args.kwarg is not None:
            specs.append((node.args.kwarg, "kwarg"))
        return specs

    def _call_result_targets(self, node: ast.Call) -> list[str]:
        parent = self.parents.get(node)
        while parent is not None and not isinstance(parent, ast.stmt):
            parent = self.parents.get(parent)
        if isinstance(parent, ast.Assign):
            return [name for target in parent.targets for name in self._assignment_targets(target)]
        if isinstance(parent, ast.AnnAssign):
            return self._assignment_targets(parent.target)
        return []

    @staticmethod
    def _assignment_targets(node: ast.AST) -> list[str]:
        if isinstance(node, ast.Name):
            return [node.id]
        if isinstance(node, (ast.Tuple, ast.List)):
            return [
                name for item in node.elts for name in _PythonFileLowerer._assignment_targets(item)
            ]
        return []

    def _constructor_type(self, node: ast.AST | None) -> str:
        if not isinstance(node, ast.Call):
            return ""
        target = self._unparse(node.func)
        return target if target in self.constructor_types else ""

    @staticmethod
    def _module_constructor_types(root: ast.Module) -> set[str]:
        """Return class names that have no competing module-level binding."""
        bindings: dict[str, list[str]] = {}
        for statement in root.body:
            if isinstance(statement, ast.ClassDef):
                bindings.setdefault(statement.name, []).append("class")
            elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                bindings.setdefault(statement.name, []).append("function")
            elif isinstance(statement, (ast.Assign, ast.AnnAssign)):
                targets = (
                    statement.targets if isinstance(statement, ast.Assign) else [statement.target]
                )
                for target in targets:
                    for name in _PythonFileLowerer._assignment_targets(target):
                        bindings.setdefault(name, []).append("assignment")
            elif isinstance(statement, (ast.Import, ast.ImportFrom)):
                for alias in statement.names:
                    name = alias.asname or alias.name.split(".", 1)[0]
                    bindings.setdefault(name, []).append("import")
        return {name for name, kinds in bindings.items() if kinds == ["class"]}

    @staticmethod
    def _unparse(node: ast.AST | None) -> str:
        return ast.unparse(node) if node is not None else ""

    @staticmethod
    def _identifiers(node: ast.AST | None) -> list[str]:
        if node is None:
            return []
        identifiers: list[str] = []
        for descendant in ast.walk(node):
            if isinstance(descendant, ast.Name):
                identifiers.append(descendant.id)
            elif isinstance(descendant, ast.Attribute):
                identifiers.append(ast.unparse(descendant))
        return identifiers

    def _calls(self, node: ast.AST | None) -> list[str]:
        if node is None:
            return []
        return [
            self._unparse(descendant.func)
            for descendant in ast.walk(node)
            if isinstance(descendant, ast.Call)
        ]

    def _operators(self, node: ast.AST) -> list[str]:
        return [
            self._operator(descendant)
            for descendant in ast.walk(node)
            if isinstance(descendant, (ast.operator, ast.unaryop, ast.boolop, ast.cmpop))
        ]

    @staticmethod
    def _operator(node: ast.AST) -> str:
        operators = {
            ast.Eq: "==",
            ast.NotEq: "!=",
            ast.Gt: ">",
            ast.GtE: ">=",
            ast.Lt: "<",
            ast.LtE: "<=",
            ast.And: "and",
            ast.Or: "or",
            ast.Not: "not",
            ast.Add: "+",
            ast.Sub: "-",
            ast.Mult: "*",
            ast.Div: "/",
            ast.Mod: "%",
        }
        return operators.get(type(node), type(node).__name__.lower())

    @staticmethod
    def _literals(node: ast.AST) -> list[str]:
        return [
            repr(descendant.value)
            for descendant in ast.walk(node)
            if isinstance(descendant, ast.Constant)
        ]
