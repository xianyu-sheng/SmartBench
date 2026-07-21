"""Prompt factory — dynamic, context-aware prompt generation."""

from smartbench.prompts.templates import (
    SYSTEM_ANALYSIS_TEMPLATE,
    DIAGNOSTIC_STRATEGY_TEMPLATE,
    PROPOSER_TEMPLATE,
    CRITIQUE_TEMPLATE,
    JUDGE_TEMPLATE,
)

__all__ = ["PromptFactory", "SYSTEM_ANALYSIS_TEMPLATE", "DIAGNOSTIC_STRATEGY_TEMPLATE",
           "PROPOSER_TEMPLATE", "CRITIQUE_TEMPLATE", "JUDGE_TEMPLATE"]
