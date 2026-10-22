"""Deterministic sharded review: plan, scope, bounded parallel, merge."""

from smartbench.sharding.merge import ShardOutcome, merge_shard_results, scope_evidence_pack
from smartbench.sharding.plan import (
    DEFAULT_MAX_FILES_PER_SHARD,
    DEFAULT_MAX_SHARDS,
    Shard,
    ShardPlan,
    build_shard_plan,
)
from smartbench.sharding.runner import run_sharded_review

__all__ = [
    "DEFAULT_MAX_FILES_PER_SHARD",
    "DEFAULT_MAX_SHARDS",
    "Shard",
    "ShardOutcome",
    "ShardPlan",
    "build_shard_plan",
    "merge_shard_results",
    "run_sharded_review",
    "scope_evidence_pack",
]
