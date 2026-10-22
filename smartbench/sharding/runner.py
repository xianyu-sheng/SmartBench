"""Bounded parallel execution of per-shard debates."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, List, Optional

from smartbench.engine.debate import DebateResult
from smartbench.sharding.merge import ShardOutcome
from smartbench.sharding.plan import Shard, ShardPlan

ShardRunner = Callable[[Shard], Optional[DebateResult]]


def run_sharded_review(
    plan: ShardPlan,
    run_shard: ShardRunner,
    *,
    max_workers: int = 2,
    deadline: Optional[float] = None,
) -> List[ShardOutcome]:
    """Run shard debates with a bounded pool; outcomes are sorted by shard id.

    ``deadline`` is a ``time.monotonic()`` value. Shards not yet dispatched at
    the deadline are marked ``skipped_budget``; running shards are allowed to
    finish (cancelling LLM calls mid-flight is not safe).
    """
    workers = max(1, min(int(max_workers), len(plan.shards) or 1))
    outcomes: List[ShardOutcome] = []
    if workers == 1:
        for shard in plan.shards:
            if _past_deadline(deadline):
                outcomes.append(ShardOutcome(shard.shard_id, "skipped_budget"))
                continue
            outcomes.append(_run_one(shard, run_shard))
        return _sorted(outcomes)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {}
        for shard in plan.shards:
            if _past_deadline(deadline):
                outcomes.append(ShardOutcome(shard.shard_id, "skipped_budget"))
                continue
            futures[pool.submit(run_shard, shard)] = shard
        for future in as_completed(futures):
            shard = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                outcomes.append(
                    ShardOutcome(shard.shard_id, "error", error=_bounded(str(exc)))
                )
                continue
            if result is None:
                outcomes.append(
                    ShardOutcome(
                        shard.shard_id, "error", error="shard returned no debate result"
                    )
                )
            else:
                outcomes.append(ShardOutcome(shard.shard_id, "completed", result))
    return _sorted(outcomes)


def _run_one(shard: Shard, run_shard: ShardRunner) -> ShardOutcome:
    try:
        result = run_shard(shard)
    except Exception as exc:
        return ShardOutcome(shard.shard_id, "error", error=_bounded(str(exc)))
    if result is None:
        return ShardOutcome(shard.shard_id, "error", error="shard returned no debate result")
    return ShardOutcome(shard.shard_id, "completed", result)


def _sorted(outcomes: List[ShardOutcome]) -> List[ShardOutcome]:
    return sorted(outcomes, key=lambda outcome: outcome.shard_id)


def _past_deadline(deadline: Optional[float]) -> bool:
    return deadline is not None and time.monotonic() > deadline


def _bounded(message: str) -> str:
    return message[:300]
