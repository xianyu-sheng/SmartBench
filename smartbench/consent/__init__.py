"""Consent staging and outreach for third-party audit trajectories."""

from smartbench.consent.email import render_request, send_pending
from smartbench.consent.queue import (
    default_queue_path,
    discover_github_repository,
    parse_github_repository,
    stage,
    stage_trajectory,
)

__all__ = [
    "default_queue_path",
    "discover_github_repository",
    "parse_github_repository",
    "render_request",
    "send_pending",
    "stage",
    "stage_trajectory",
]
