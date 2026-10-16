"""Counterfactual retry-loop analysis over SemanticIR.

A loop that checks an error result and jumps straight back to the loop head
(the ``continue`` / ``LOOP_BACK`` shape) retries the failing operation
immediately.  When that operation can fail for a persistent reason — file
descriptor exhaustion, disk full, a listener that has already failed — the
retry turns into a busy loop that keeps a core busy and floods logs with the
same error, instead of failing or yielding.

The analyzer proves the structural part deterministically over normalized
operations and control flow: an error outcome of an error-checked call reaches
a ``LOOP_BACK`` edge to the enclosing loop without passing a mitigation
(backoff/sleep/ticker call, or a bounded-retry guard).  Whether the operation
can actually fail persistently, and whether the project prefers fail-stop over
retry, are adjudicated later and are never assumed here.

Language frontends are expected to lower loops, error checks, ``continue`` and
calls into the shared operation kinds; the analyzer never imports a parser.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from smartbench.analysis.control_flow import ControlFlowGraph
from smartbench.ir import (
    EvidenceRef,
    FactKind,
    OperationEdgeKind,
    OperationKind,
    SemanticFact,
    SemanticIR,
    SemanticOperation,
)

# Operations that can plausibly fail for a persistent reason.  Matching is by
# exact leaf identifier so that e.g. ``stateMachine.next`` is not mistaken for
# a ``stat`` call.  This is a conservative scope filter, not a claim about a
# specific caller: the finding is only emitted when the deterministic
# control-flow shape is present.
_PERSISTENT_RISK_OPERATIONS = frozenset({
    "accept",
    "accepttcp",
    "acceptunix",
    "listen",
    "dial",
    "dialcontext",
    "dialtimeout",
    "connect",
    "open",
    "openfile",
    "openat",
    "fopen",
    "create",
    "createfile",
    "createtemp",
    "mkdir",
    "mkdirall",
    "read",
    "readall",
    "readfile",
    "readdir",
    "readat",
    "recv",
    "receive",
    "readmessage",
    "write",
    "writefile",
    "writeat",
    "writestring",
    "send",
    "sendmessage",
    "query",
    "queryrow",
    "querycontext",
    "exec",
    "execcontext",
    "stat",
    "lstat",
    "fstat",
    "statfs",
    "walk",
    "walkdir",
    "remove",
    "removeall",
    "unlink",
    "rename",
    "lock",
    "trylock",
    "flush",
    "sync",
})

# Calls that introduce a pause/backoff, or branch conditions that bound the
# number of retries.  A loop with one of these on the error path is mitigated.
_MITIGATION_CALLS = re.compile(
    r"(?i)(sleep|after|ticker|newticker|timer|newtimer|throttle|backoff|"
    r"jitter|delay|pause|wait|yield|ratelimit|rate_limit)"
)
_LIMIT_GUARD_NAMES = re.compile(
    r"(?i)\b(attempts?|retr(?:y|ies)|backoff|count|limit|max|budget|deadline|timeout)\b"
)

_NIL_LITERALS = ("nil", "none", "null")

# Result bindings that carry the failure outcome.  Success values must never be
# mistaken for errors (for example the ``content`` result of ReadFile).
_ERROR_NAMES = re.compile(r"(?i)^(err|error|errno)$")
_COMPARISON_OPERATORS = ("==", "!=", "<=", ">=", "<", ">")

# Calls that only record the failure; they do not make the retry anything other
# than immediate.  Every other call/spawn/send on the path is real work and
# means the error path does not retry immediately.
_PASSTHROUGH_CALLS = re.compile(
    r"(?i)(log|print|sprintf|errorf|warn|debug|info|trace|slog|fmt\.)"
)


def _operation_leaf(target: str) -> str:
    """Lower-cased last identifier segment of a call target."""
    cleaned = target or ""
    for separator in ("::", "->"):
        cleaned = cleaned.split(separator)[-1]
    cleaned = cleaned.split(".")[-1]
    cleaned = re.sub(r"[^A-Za-z0-9_]", "", cleaned)
    return cleaned.lower()


def _is_unbounded_loop(loop: SemanticOperation) -> bool:
    """Whether the loop was lowered as an unbounded loop."""
    value = loop.attributes.get("infinite")
    return value is True or str(value).lower() == "true"


@dataclass(frozen=True)
class RetryLoopViolation:
    """One unmitigated error retry shape inside a loop."""

    loop: SemanticOperation
    call: SemanticOperation
    branch: SemanticOperation
    loop_back: SemanticOperation
    error_variable: str

    @property
    def evidence(self) -> tuple[EvidenceRef, ...]:
        return (self.loop.location, self.call.location, self.branch.location, self.loop_back.location)

    def to_fact(self) -> SemanticFact:
        return SemanticFact(
            subject=self.call.target or self.call.value,
            predicate=FactKind.CONTROLS,
            object="retry_without_backoff",
            evidence=self.evidence,
        )


@dataclass
class RetryLoopResult:
    """Deterministic analysis result."""

    violations: list[RetryLoopViolation] = field(default_factory=list)
    loops_evaluated: int = 0
    error_branches_evaluated: int = 0
    mitigated_paths: int = 0
    retrieval_trace: tuple[str, ...] = ()


class RetryLoopAnalyzer:
    """Find error retry loops without mitigation over SemanticIR."""

    def analyze(
        self,
        ir: SemanticIR,
        languages: Iterable[str] | None = None,
    ) -> RetryLoopResult:
        language_filter = {language.lower() for language in languages or ()}
        cfg = ControlFlowGraph.from_ir(ir, language_filter)
        by_scope: dict[str, list[SemanticOperation]] = {}
        for operation in ir.operations:
            if language_filter and operation.language.lower() not in language_filter:
                continue
            if not operation.scope_id:
                continue
            by_scope.setdefault(operation.scope_id, []).append(operation)

        result = RetryLoopResult()
        seen_violations: set[tuple[str, str, str]] = set()
        for scope_id, operations in sorted(by_scope.items()):
            loops = sorted(
                (operation for operation in operations if operation.kind == OperationKind.LOOP),
                key=lambda operation: (operation.location.line_start or 0, operation.id),
            )
            branches = [
                operation for operation in operations if operation.kind == OperationKind.BRANCH
            ]
            for loop in loops:
                if not _is_unbounded_loop(loop):
                    continue
                result.loops_evaluated += 1
                body_ids = self._loop_body(loop, cfg)
                assigned_names = self._assigned_names(operations, body_ids)
                calls = sorted(
                    (
                        operation
                        for operation in operations
                        if operation.id in body_ids
                        and operation.kind == OperationKind.CALL
                    ),
                    key=lambda operation: (operation.location.line_start or 0, operation.id),
                )
                for call in calls:
                    if _operation_leaf(call.target or call.value) not in _PERSISTENT_RISK_OPERATIONS:
                        continue
                    result_names = self._result_names(call)
                    if not result_names:
                        continue
                    for branch in branches:
                        if branch.id not in body_ids:
                            continue
                        error_variable, error_outcome = self._error_outcome(branch, result_names)
                        if not error_variable or error_outcome is None:
                            continue
                        result.error_branches_evaluated += 1
                        start_ids = [
                            arc.target_id
                            for arc in cfg.successors(branch.id, {error_outcome})
                        ]
                        witness = self._unmitigated_loopback(
                            start_ids,
                            loop.id,
                            cfg,
                            body_ids,
                            assigned_names,
                            error_variable,
                        )
                        if witness is None:
                            result.mitigated_paths += 1
                            continue
                        violation_key = (loop.id, call.id, branch.id)
                        if violation_key in seen_violations:
                            continue
                        seen_violations.add(violation_key)
                        result.violations.append(
                            RetryLoopViolation(
                                loop=loop,
                                call=call,
                                branch=branch,
                                loop_back=witness,
                                error_variable=error_variable,
                            )
                        )
        result.retrieval_trace = (
            f"retry-loops:{result.loops_evaluated}",
            f"retry-error-branches:{result.error_branches_evaluated}",
            f"retry-mitigated:{result.mitigated_paths}",
        )
        return result

    def _loop_body(self, loop: SemanticOperation, cfg: ControlFlowGraph) -> set[str]:
        """Operations owned by the loop body in the same scope."""
        body: set[str] = set()
        queue = [loop.id]
        while queue:
            current = queue.pop()
            for arc in cfg.successors(current):
                if arc.kind == OperationEdgeKind.LOOP_EXIT:
                    continue
                target = cfg.operations.get(arc.target_id)
                if target is None or target.scope_id != loop.scope_id:
                    continue
                if arc.target_id in body:
                    continue
                body.add(arc.target_id)
                queue.append(arc.target_id)
        # Operand producers (for example the call that feeds an assignment) are
        # attached through NEXT edges without an incoming control-flow edge, so
        # they are not reachable from the loop head.  They still execute inside
        # the loop body; pull in the direct predecessors of body operations.
        for node_id in list(body):
            for arc in cfg.predecessors(node_id):
                producer = cfg.operations.get(arc.source_id)
                if producer is not None and producer.scope_id == loop.scope_id:
                    body.add(producer.id)
        return body

    @staticmethod
    def _result_names(call: SemanticOperation) -> tuple[str, ...]:
        targets = call.attributes.get("result_targets")
        if not isinstance(targets, (list, tuple)):
            return ()
        return tuple(
            str(target)
            for target in targets
            if str(target) and _ERROR_NAMES.match(str(target))
        )

    def _error_outcome(
        self,
        branch: SemanticOperation,
        result_names: tuple[str, ...],
    ) -> tuple[str, OperationEdgeKind | None]:
        condition = (branch.value or "").strip()
        if not condition:
            return "", None
        lowered = condition.lower()
        words = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", condition))
        has_nil_literal = any(literal in words for literal in _NIL_LITERALS)
        for name in result_names:
            if name not in words:
                continue
            if "(" in condition:
                # Classified handling (errors.Is, retryable(err), ...) is not a
                # generic error gate; keep the analyzer conservative.
                return "", None
            if has_nil_literal and ("!=" in condition or " is not " in lowered):
                return name, OperationEdgeKind.TRUE_BRANCH
            if has_nil_literal and ("==" in condition or " is " in lowered):
                return name, OperationEdgeKind.FALSE_BRANCH
            if not has_nil_literal and not any(
                operator in condition for operator in _COMPARISON_OPERATORS
            ):
                # Truthiness check of the error value itself (`if err:`).
                return name, OperationEdgeKind.TRUE_BRANCH
        return "", None

    @staticmethod
    def _assigned_names(
        operations: list[SemanticOperation],
        body_ids: set[str],
    ) -> set[str]:
        """Names assigned inside the loop body (progress-variable candidates)."""
        names: set[str] = set()
        for operation in operations:
            if operation.id not in body_ids:
                continue
            if operation.kind not in (OperationKind.ASSIGN, OperationKind.UPDATE):
                continue
            for name in re.split(r"[,\s]+", operation.target or ""):
                if name.strip():
                    names.add(name.strip())
            bindings = operation.attributes.get("bindings")
            if isinstance(bindings, (list, tuple)):
                for binding in bindings:
                    if isinstance(binding, dict) and binding.get("target"):
                        names.add(str(binding["target"]))
        return names

    def _unmitigated_loopback(
        self,
        start_ids: list[str],
        loop_id: str,
        cfg: ControlFlowGraph,
        body_ids: set[str],
        guard_names: set[str],
        error_variable: str,
    ) -> SemanticOperation | None:
        """Return a loop-back witness for an immediate, unmitigated retry."""
        if not start_ids:
            return None
        visited: set[tuple[str, bool, bool]] = set()
        queue: list[tuple[str, bool, bool]] = [
            (start_id, False, False) for start_id in start_ids
        ]
        while queue:
            node_id, mitigated, blocked = queue.pop()
            state = (node_id, mitigated, blocked)
            if state in visited:
                continue
            visited.add(state)
            node = cfg.operations.get(node_id)
            if node is None or node.id not in body_ids:
                continue
            for arc in cfg.successors(node.id):
                if arc.kind == OperationEdgeKind.LOOP_BACK:
                    if arc.target_id != loop_id:
                        continue
                    if not mitigated and not blocked:
                        return node
                    continue
                target = cfg.operations.get(arc.target_id)
                if target is None:
                    continue
                next_mitigated = mitigated or self._is_mitigation(
                    target,
                    guard_names,
                    error_variable,
                )
                next_blocked = blocked or self._blocks_immediate_retry(target)
                queue.append((arc.target_id, next_mitigated, next_blocked))
        return None

    @staticmethod
    def _blocks_immediate_retry(operation: SemanticOperation) -> bool:
        """Whether the operation does real work before the loop retries."""
        if operation.kind not in (
            OperationKind.CALL,
            OperationKind.SPAWN,
            OperationKind.SEND,
            OperationKind.EMIT,
        ):
            return False
        return not _PASSTHROUGH_CALLS.search(operation.target or operation.value)

    @staticmethod
    def _is_mitigation(
        operation: SemanticOperation,
        guard_names: set[str] = frozenset(),
        error_variable: str = "",
    ) -> bool:
        if operation.kind == OperationKind.BRANCH:
            condition = operation.value or ""
            if _LIMIT_GUARD_NAMES.search(condition):
                return True
            # A comparison that references a variable updated inside the loop
            # bounds the retry (for example walking up parent directories).
            identifiers = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", condition))
            if (identifiers & guard_names) - {error_variable}:
                return True
            return False
        if operation.kind in (OperationKind.CALL, OperationKind.SPAWN):
            return bool(_MITIGATION_CALLS.search(operation.target or operation.value))
        return False
