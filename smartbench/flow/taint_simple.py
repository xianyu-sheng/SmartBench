"""Deterministic, intra-function taint analysis for Python and JavaScript.

The analyzer intentionally models a small set of operations. Unsupported
operations remain ``UNKNOWN`` and only produce a finding when the unknown
value can be traced to a function parameter. This keeps the evidence honest:
known request inputs and merely possible external inputs are reported at
different confidence levels.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from smartbench.flow.ast_traversal import (
    AstContext,
    get_child_by_field_name,
    get_named_children,
    get_node_text,
)
from smartbench.flow.schema import (
    AbstractValue,
    TaintState,
    TraceStep,
    location_from_node,
)
from smartbench.flow.taint import TaintTracker

_FUNCTION_TYPES = {
    "arrow_function",
    "function",
    "function_declaration",
    "function_definition",
    "generator_function",
    "generator_function_declaration",
    "method_definition",
}

_TRANSPARENT_METHODS = {
    "concat",
    "filter",
    "flat",
    "flatmap",
    "get",
    "join",
    "map",
    "reduce",
    "replace",
    "slice",
    "split",
    "substring",
    "tolowercase",
    "touppercase",
    "trim",
}

_REQUEST_PARAMETER_NAMES = {"ctx", "context", "req", "request"}


class SimpleTaintAnalyzer:
    """Analyze source-order data flow inside individual functions.

    The public return shape is retained for compatibility with the initial
    data-flow prototype. Each result contains a type, sink location/snippet,
    abstract value, reason, and confidence.
    """

    def __init__(
        self,
        context: AstContext,
        tracker: TaintTracker,
        language: str = "typescript",
    ) -> None:
        self.context = context
        self.tracker = tracker
        self.language = language.lower()
        self._findings: List[Dict[str, Any]] = []
        self._seen_findings: Set[tuple[str, int, int]] = set()

    def analyze_and_find_findings(self, root_node: Any) -> List[Dict[str, Any]]:
        """Return deterministic findings in source order."""
        functions: List[Any] = []
        self._collect_functions(root_node, functions)

        # Analyze module-level statements without descending into functions.
        self._walk_scope(root_node, {}, root_node)

        for function_node in functions:
            environment = self._parameter_environment(function_node)
            body = get_child_by_field_name(function_node, "body")
            if body is not None:
                self._walk_scope(body, environment, body)

        return list(self._findings)

    def _collect_functions(self, node: Any, result: List[Any]) -> None:
        if node.type in _FUNCTION_TYPES:
            result.append(node)
        for child in get_named_children(node):
            self._collect_functions(child, result)

    def _parameter_environment(self, function_node: Any) -> Dict[str, AbstractValue]:
        environment: Dict[str, AbstractValue] = {}
        parameters = get_child_by_field_name(function_node, "parameters")
        if parameters is None:
            return environment

        for parameter in get_named_children(parameters):
            for identifier in self._binding_identifiers(parameter):
                name = self._text(identifier)
                if not name or name in environment:
                    continue
                location = location_from_node(self.context.file_path, identifier)
                if name.lower() in _REQUEST_PARAMETER_NAMES:
                    environment[name] = self.tracker.create_tainted_value(
                        location,
                        f"request parameter: {name}",
                        name,
                    )
                else:
                    environment[name] = self.tracker.create_value(
                        location,
                        TaintState.UNKNOWN,
                        trace=(
                            TraceStep(
                                location=location,
                                operation=f"parameter: {name}",
                                source_snippet=name,
                            ),
                        ),
                    )
        return environment

    def _binding_identifiers(self, node: Any) -> List[Any]:
        """Extract identifiers from a parameter binding, excluding type nodes."""
        if node.type == "identifier":
            return [node]

        pattern = get_child_by_field_name(node, "pattern")
        name = get_child_by_field_name(node, "name")
        target = pattern or name
        if target is not None and target is not node:
            return self._binding_identifiers(target)

        if node.type in {
            "array_pattern",
            "object_pattern",
            "parameters",
            "formal_parameters",
            "required_parameter",
            "optional_parameter",
            "rest_pattern",
            "default_parameter",
        }:
            identifiers: List[Any] = []
            for child in get_named_children(node):
                if child.type in {"type_annotation", "predefined_type"}:
                    continue
                identifiers.extend(self._binding_identifiers(child))
            return identifiers
        return []

    def _walk_scope(
        self,
        node: Any,
        environment: Dict[str, AbstractValue],
        scope_root: Any,
    ) -> None:
        if node is not scope_root and node.type in _FUNCTION_TYPES:
            return

        if node.type == "variable_declarator":
            name_node = get_child_by_field_name(node, "name")
            value_node = get_child_by_field_name(node, "value")
            self._assign_binding(name_node, value_node, environment, "variable declaration")
        elif node.type in {"assignment", "assignment_expression", "augmented_assignment"}:
            left = get_child_by_field_name(node, "left")
            right = get_child_by_field_name(node, "right")
            self._assign_binding(left, right, environment, "assignment")
        elif node.type in {"call", "call_expression"}:
            self._inspect_sink(node, environment)

        for child in get_named_children(node):
            self._walk_scope(child, environment, scope_root)

    def _assign_binding(
        self,
        target: Any,
        expression: Any,
        environment: Dict[str, AbstractValue],
        operation: str,
    ) -> None:
        if target is None or expression is None:
            return
        identifiers = self._binding_identifiers(target)
        if len(identifiers) != 1:
            return

        value = self._evaluate(expression, environment)
        if value is None:
            return

        identifier = identifiers[0]
        name = self._text(identifier)
        if not name:
            return
        location = location_from_node(self.context.file_path, target)
        environment[name] = self.tracker.propagate_taint(
            value,
            location,
            f"{operation}: {name}",
            self._text(expression),
        )

    def _evaluate(
        self,
        node: Any,
        environment: Dict[str, AbstractValue],
    ) -> Optional[AbstractValue]:
        if node is None:
            return None

        node_type = node.type
        location = location_from_node(self.context.file_path, node)

        if node_type == "identifier":
            name = self._text(node)
            value = environment.get(name)
            if value is None:
                return self.tracker.create_value(location, TaintState.UNKNOWN)
            return self.tracker.propagate_taint(
                value,
                location,
                f"reference: {name}",
                name,
            )

        if node_type in {"member_expression", "attribute", "subscript"}:
            text = self._text(node)
            if self._is_known_source(text):
                return self.tracker.create_tainted_value(
                    location,
                    f"input source: {text}",
                    text,
                )
            object_node = get_child_by_field_name(node, "object") or get_child_by_field_name(
                node, "value"
            )
            object_value = self._evaluate(object_node, environment)
            if object_value is None:
                return self.tracker.create_value(location, TaintState.UNKNOWN)
            return self.tracker.propagate_taint(
                object_value,
                location,
                "member access",
                text,
            )

        if node_type in {"call", "call_expression"}:
            function_node = get_child_by_field_name(node, "function")
            function_text = self._text(function_node)
            lower_function = function_text.lower()
            if lower_function in {"input", "builtins.input"}:
                return self.tracker.create_tainted_value(
                    location,
                    "input source: input()",
                    self._text(node),
                )

            if function_node is not None and function_node.type in {
                "attribute",
                "member_expression",
            }:
                method = self._member_name(function_node).lower()
                if method in _TRANSPARENT_METHODS:
                    if method == "map":
                        mapped_value = self._constant_map_value(node, environment)
                        if mapped_value is not None:
                            return mapped_value
                    receiver = get_child_by_field_name(function_node, "object")
                    receiver_value = self._evaluate(receiver, environment)
                    if receiver_value is not None:
                        return self.tracker.propagate_taint(
                            receiver_value,
                            location,
                            f"call preserving input: {method}",
                            self._text(node),
                        )
            return self.tracker.create_value(location, TaintState.UNKNOWN)

        if node_type in {"await", "await_expression", "parenthesized_expression"}:
            argument = get_child_by_field_name(node, "argument")
            if argument is None:
                named = get_named_children(node)
                argument = named[0] if named else None
            return self._evaluate(argument, environment)

        if node_type in {"string", "template_string"}:
            substitutions = [
                child
                for child in get_named_children(node)
                if child.type in {"interpolation", "template_substitution"}
            ]
            if not substitutions:
                return self.tracker.create_value(
                    location,
                    TaintState.NOT_TAINTED,
                    constant_value=self._text(node),
                )
            values: List[AbstractValue] = []
            for substitution in substitutions:
                expression = get_child_by_field_name(substitution, "expression")
                if expression is None:
                    named = get_named_children(substitution)
                    expression = named[0] if named else None
                value = self._evaluate(expression, environment)
                if value is not None:
                    values.append(value)
            return self.tracker.combine_values(
                values,
                location,
                "string interpolation",
                self._text(node),
            )

        if node_type in {
            "array",
            "binary_expression",
            "binary_operator",
            "dictionary",
            "list",
            "object",
            "ternary_expression",
            "tuple",
        }:
            values = [
                value
                for child in get_named_children(node)
                if (value := self._evaluate(child, environment)) is not None
            ]
            return self.tracker.combine_values(
                values,
                location,
                f"{node_type} composition",
                self._text(node),
            )

        if node_type in {
            "false",
            "float",
            "integer",
            "none",
            "null",
            "number",
            "true",
            "undefined",
        }:
            return self.tracker.create_value(
                location,
                TaintState.NOT_TAINTED,
                constant_value=self._text(node),
            )

        return self.tracker.create_value(location, TaintState.UNKNOWN)

    def _constant_map_value(
        self,
        call_node: Any,
        environment: Dict[str, AbstractValue],
    ) -> Optional[AbstractValue]:
        """Return a safe value when a map callback produces only a constant."""
        arguments = get_child_by_field_name(call_node, "arguments")
        if arguments is None:
            return None
        named_arguments = get_named_children(arguments)
        if not named_arguments or named_arguments[0].type not in _FUNCTION_TYPES:
            return None

        callback_body = get_child_by_field_name(named_arguments[0], "body")
        if callback_body is None:
            return None
        callback_value = self._evaluate(callback_body, {})
        if callback_value is None or callback_value.taint_state != TaintState.NOT_TAINTED:
            return None

        location = location_from_node(self.context.file_path, call_node)
        return self.tracker.create_value(
            location,
            TaintState.NOT_TAINTED,
            constant_value=callback_value.constant_value,
        )

    def _inspect_sink(self, node: Any, environment: Dict[str, AbstractValue]) -> None:
        function_node = get_child_by_field_name(node, "function")
        arguments_node = get_child_by_field_name(node, "arguments")
        if function_node is None or arguments_node is None:
            return

        sink_type = self._sink_type(self._text(function_node))
        if sink_type is None:
            return

        arguments = get_named_children(arguments_node)
        if not arguments:
            return
        value = self._evaluate(arguments[0], environment)
        if value is None or value.taint_state == TaintState.NOT_TAINTED:
            return

        parameter_origin = self._has_parameter_origin(value)
        if value.taint_state == TaintState.TAINTED:
            confidence = 0.95
            reason = "confirmed input source reaches dangerous sink"
        elif parameter_origin:
            confidence = 0.75
            reason = "function parameter reaches dangerous sink; caller control is unproven"
        else:
            return

        location = location_from_node(self.context.file_path, node)
        key = (sink_type, location.start_byte, location.end_byte)
        if key in self._seen_findings:
            return
        self._seen_findings.add(key)
        self._findings.append(
            {
                "type": sink_type,
                "location": location,
                "snippet": self._text(node),
                "value": value,
                "reason": reason,
                "confidence": confidence,
            }
        )

    def _sink_type(self, function_text: str) -> Optional[str]:
        normalized = function_text.replace(" ", "").lower()
        method = normalized.rsplit(".", 1)[-1]
        receiver = normalized.rsplit(".", 1)[0] if "." in normalized else ""

        sql_receivers = (
            "client",
            "conn",
            "connection",
            "cursor",
            "database",
            "db",
            "knex",
            "pool",
            "prisma",
            "sequelize",
            "sql",
            "sqlite",
        )
        if method in {"all", "execute", "executemany", "query", "raw", "run"} and any(
            hint in receiver for hint in sql_receivers
        ):
            return "sql_injection"

        command_sinks = {
            "child_process.exec",
            "child_process.execsync",
            "child_process.spawn",
            "child_process.spawnsync",
            "os.popen",
            "os.system",
            "subprocess.call",
            "subprocess.check_call",
            "subprocess.check_output",
            "subprocess.popen",
            "subprocess.run",
        }
        if normalized in command_sinks:
            return "command_injection"

        path_sinks = {
            "fs.createreadstream",
            "fs.createwritestream",
            "fs.open",
            "fs.readfile",
            "fs.readfilesync",
            "fs.writefile",
            "fs.writefilesync",
            "open",
            "pathlib.path",
        }
        if normalized in path_sinks:
            return "path_traversal"
        return None

    def _is_known_source(self, text: str) -> bool:
        normalized = text.replace(" ", "").lower()
        prefixes = (
            "ctx.body",
            "ctx.params",
            "ctx.query",
            "ctx.request",
            "process.argv",
            "req.body",
            "req.params",
            "req.query",
            "request.args",
            "request.body",
            "request.form",
            "request.get",
            "request.params",
            "request.post",
            "request.query",
            "sys.argv",
        )
        return any(
            normalized == prefix or normalized.startswith(f"{prefix}.") for prefix in prefixes
        )

    def _has_parameter_origin(self, value: AbstractValue) -> bool:
        return any(step.operation.startswith("parameter: ") for step in value.taint_trace)

    def _member_name(self, node: Any) -> str:
        property_node = get_child_by_field_name(node, "property") or get_child_by_field_name(
            node, "attribute"
        )
        return self._text(property_node)

    def _text(self, node: Any) -> str:
        if node is None:
            return ""
        return get_node_text(node, self.context.source_bytes)
