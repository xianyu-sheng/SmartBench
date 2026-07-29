"""Versioned configuration for language-neutral state-machine rules."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from smartbench.analysis.state_machine import (
    InvariantKind,
    OperationSelector,
    StateInvariant,
    StateScope,
)
from smartbench.ir import OperationKind
from smartbench.path_safety import read_text_bounded

STATE_RULE_SCHEMA_VERSION = "smartbench.state-rules/v1"


class StateRuleConfigError(ValueError):
    """Raised when a declarative state-rule file violates the schema."""


@dataclass(frozen=True)
class StateRuleDefinition:
    """Validated rule metadata plus its language-neutral invariant."""

    rule_id: str
    name: str
    description: str
    severity: str
    confidence: float
    languages: frozenset[str]
    invariant: StateInvariant


def load_state_rule_file(file_path: Path) -> list[StateRuleDefinition]:
    """Load one bounded YAML rule document using the versioned schema."""
    source = read_text_bounded(file_path, 1024 * 1024)
    if source is None:
        raise StateRuleConfigError(f"cannot read state-rule file: {file_path}")
    try:
        document = yaml.safe_load(source)
    except yaml.YAMLError as exc:
        raise StateRuleConfigError(f"invalid YAML in {file_path}: {exc}") from exc

    root = _mapping(document, "document")
    _reject_unknown(root, {"version", "rules"}, "document")
    version = root.get("version")
    if version != STATE_RULE_SCHEMA_VERSION:
        raise StateRuleConfigError(
            f"unsupported state-rule version {version!r}; expected {STATE_RULE_SCHEMA_VERSION!r}"
        )
    raw_rules = root.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise StateRuleConfigError("document.rules must be a non-empty list")

    definitions = [_parse_rule(item, index) for index, item in enumerate(raw_rules)]
    seen: set[str] = set()
    for definition in definitions:
        if definition.rule_id in seen:
            raise StateRuleConfigError(f"duplicate rule id: {definition.rule_id}")
        seen.add(definition.rule_id)
    return definitions


def _parse_rule(value: Any, index: int) -> StateRuleDefinition:
    path = f"rules[{index}]"
    rule = _mapping(value, path)
    _reject_unknown(
        rule,
        {
            "id",
            "name",
            "description",
            "severity",
            "confidence",
            "languages",
            "message",
            "invariant",
        },
        path,
    )
    rule_id = _non_empty_string(rule.get("id"), f"{path}.id")
    name = _non_empty_string(rule.get("name", rule_id), f"{path}.name")
    description = _string(rule.get("description", ""), f"{path}.description")
    message = _non_empty_string(
        rule.get("message", description or name),
        f"{path}.message",
    )
    severity = _non_empty_string(rule.get("severity", "warning"), f"{path}.severity")
    if severity not in {"error", "warning", "info"}:
        raise StateRuleConfigError(f"{path}.severity must be error, warning, or info")
    confidence_value = rule.get("confidence", 1.0)
    if isinstance(confidence_value, bool) or not isinstance(confidence_value, (int, float)):
        raise StateRuleConfigError(f"{path}.confidence must be a number")
    confidence = float(confidence_value)
    if not 0.0 <= confidence <= 1.0:
        raise StateRuleConfigError(f"{path}.confidence must be between 0 and 1")
    languages = frozenset(
        item.lower() for item in _string_list(rule.get("languages", []), f"{path}.languages")
    )

    invariant_data = _mapping(rule.get("invariant"), f"{path}.invariant")
    _reject_unknown(
        invariant_data,
        {"kind", "event", "guard", "action", "exits", "scope", "max_call_depth"},
        f"{path}.invariant",
    )
    try:
        kind = InvariantKind(
            _non_empty_string(invariant_data.get("kind"), f"{path}.invariant.kind")
        )
    except ValueError as exc:
        allowed = ", ".join(item.value for item in InvariantKind)
        raise StateRuleConfigError(f"{path}.invariant.kind must be one of: {allowed}") from exc

    guard = None
    if invariant_data.get("guard") is not None:
        guard = _parse_selector(invariant_data["guard"], f"{path}.invariant.guard")
    exits = OperationSelector.of(OperationKind.RETURN, OperationKind.BREAK)
    if invariant_data.get("exits") is not None:
        exits = _parse_selector(invariant_data["exits"], f"{path}.invariant.exits")

    scope_value = invariant_data.get("scope", StateScope.INTRAPROCEDURAL.value)
    try:
        scope = StateScope(_non_empty_string(scope_value, f"{path}.invariant.scope"))
    except ValueError as exc:
        allowed = ", ".join(item.value for item in StateScope)
        raise StateRuleConfigError(f"{path}.invariant.scope must be one of: {allowed}") from exc
    max_call_depth = invariant_data.get("max_call_depth", 4)
    if isinstance(max_call_depth, bool) or not isinstance(max_call_depth, int):
        raise StateRuleConfigError(f"{path}.invariant.max_call_depth must be an integer")
    if not 0 <= max_call_depth <= 16:
        raise StateRuleConfigError(f"{path}.invariant.max_call_depth must be between 0 and 16")

    try:
        invariant = StateInvariant(
            invariant_id=rule_id,
            kind=kind,
            event=_parse_selector(invariant_data.get("event"), f"{path}.invariant.event"),
            guard=guard,
            action=_parse_selector(invariant_data.get("action"), f"{path}.invariant.action"),
            exits=exits,
            message=message,
            scope=scope,
            max_call_depth=max_call_depth,
        )
    except ValueError as exc:
        raise StateRuleConfigError(f"{path}.invariant: {exc}") from exc

    return StateRuleDefinition(
        rule_id=rule_id,
        name=name,
        description=description,
        severity=severity,
        confidence=confidence,
        languages=languages,
        invariant=invariant,
    )


def _parse_selector(value: Any, path: str) -> OperationSelector:
    selector = _mapping(value, path)
    _reject_unknown(
        selector,
        {"kinds", "contains_all", "contains_any", "attributes"},
        path,
    )
    raw_kinds = _string_list(selector.get("kinds", []), f"{path}.kinds")
    try:
        kinds = [OperationKind(item) for item in raw_kinds]
    except ValueError as exc:
        allowed = ", ".join(item.value for item in OperationKind)
        raise StateRuleConfigError(
            f"{path}.kinds contains an unknown kind; allowed: {allowed}"
        ) from exc
    contains_all = _string_list(selector.get("contains_all", []), f"{path}.contains_all")
    contains_any = _string_list(selector.get("contains_any", []), f"{path}.contains_any")
    attributes = selector.get("attributes", {})
    if not isinstance(attributes, Mapping):
        raise StateRuleConfigError(f"{path}.attributes must be a mapping")
    if not kinds and not contains_all and not contains_any and not attributes:
        raise StateRuleConfigError(f"{path} must contain at least one matching criterion")
    return OperationSelector.of(
        *kinds,
        contains_all=contains_all,
        contains_any=contains_any,
        attributes=dict(attributes),
    )


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise StateRuleConfigError(f"{path} must be a mapping")
    if not all(isinstance(key, str) for key in value):
        raise StateRuleConfigError(f"{path} keys must be strings")
    return value


def _reject_unknown(value: Mapping[str, Any], allowed: set[str], path: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise StateRuleConfigError(f"{path} contains unknown fields: {', '.join(unknown)}")


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str):
        raise StateRuleConfigError(f"{path} must be a string")
    return value


def _non_empty_string(value: Any, path: str) -> str:
    result = _string(value, path).strip()
    if not result:
        raise StateRuleConfigError(f"{path} must not be empty")
    return result


def _string_list(value: Any, path: str) -> list[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = value
    else:
        raise StateRuleConfigError(f"{path} must be a string or list of strings")
    if not all(isinstance(item, str) and item.strip() for item in values):
        raise StateRuleConfigError(f"{path} must contain only non-empty strings")
    return [item.strip() for item in values]
