"""Check findings against upstream issue and pull-request history.

Before filing a finding upstream it is worth knowing whether the same problem
was already reported and, more importantly, already discussed and declined as a
trade-off.  This checker performs a bounded, opt-in GitHub search for each
finding and classifies the outcome:

- ``none``: no matching issue or pull request;
- ``mentioned``: an open issue or pull request mentions it;
- ``discussed``: a closed item with explicit intent markers (wontfix, by
  design, trade-off, not planned) — do not re-file without new evidence;
- ``closed``: a closed item without intent markers;
- ``rate_limited`` / ``error``: the lookup could not be completed.

The checker never authenticates implicitly: it uses the environment variable
named by the caller (``GITHUB_TOKEN`` by default) when present and otherwise
runs unauthenticated with a conservative request interval.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import httpx

GITHUB_API = "https://api.github.com"
_UNQUOTED_INTERVAL_SECONDS = 6.0
_AUTHENTICATED_INTERVAL_SECONDS = 1.0
_GENERIC_FILE_STEMS = frozenset(
    {"main", "index", "util", "utils", "base", "common", "core", "app"}
)
_INTENT_MARKERS = re.compile(
    r"(?i)\b(wontfix|won'?t fix|by design|intentional(?:ly)?|trade[- ]?off|"
    r"not planned|out of scope|expected behavior|working as intended)\b"
)
_RULE_TERMS: Mapping[str, tuple[str, ...]] = {
    "retry_without_backoff": ("retry", "backoff"),
}


class UpstreamRateLimitError(RuntimeError):
    """The GitHub search rate limit blocked the lookup."""


@dataclass(frozen=True)
class UpstreamMatch:
    """One matching issue or pull request."""

    url: str
    title: str
    state: str
    is_pull_request: bool
    intent_hint: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "url": self.url,
            "title": self.title,
            "state": self.state,
            "pull_request": self.is_pull_request,
            "intent_hint": self.intent_hint,
        }


@dataclass
class UpstreamCheckResult:
    """Outcome of one finding lookup."""

    rule_id: str
    file_path: str
    line_start: int
    query: str = ""
    queries: list[str] = field(default_factory=list)
    status: str = "none"
    matches: list[UpstreamMatch] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "file_path": self.file_path,
            "line_start": self.line_start,
            "query": self.query,
            "queries": list(self.queries),
            "status": self.status,
            "matches": [match.to_dict() for match in self.matches],
            "error": self.error,
        }


def extract_findings(document: object) -> list[dict[str, object]]:
    """Read findings from a unified or quick report document.

    Unified reports carry ``findings`` with structured locations; quick reports
    store agent suggestions under ``final_suggestions`` with ``file:line``
    location strings.  Unknown shapes yield an empty list instead of failing.
    """
    if not isinstance(document, Mapping):
        return []
    raw = document.get("findings")
    if raw is None:
        analysis = document.get("analysis_report")
        if isinstance(analysis, Mapping):
            raw = analysis.get("findings")
    if raw is None:
        raw = document.get("final_suggestions")
    if not isinstance(raw, list):
        return []
    findings: list[dict[str, object]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        rule_id = str(item.get("rule_id", "") or "")
        location = item.get("location")
        file_path = ""
        line_start = 0
        if isinstance(location, Mapping):
            file_path = str(location.get("file_path", "") or "")
            try:
                line_start = int(location.get("line_start", 0) or 0)
            except (TypeError, ValueError):
                line_start = 0
        elif isinstance(location, str) and ":" in location:
            path, _, number = location.rpartition(":")
            file_path = path
            try:
                line_start = int(number)
            except ValueError:
                line_start = 0
        metadata = item.get("metadata")
        confidence = item.get("confidence")
        try:
            confidence_value = None if confidence is None else float(confidence)
        except (TypeError, ValueError):
            confidence_value = None
        entry = {
            "rule_id": rule_id,
            "file_path": file_path,
            "line_start": line_start,
            "metadata": metadata if isinstance(metadata, Mapping) else {},
        }
        if confidence_value is not None:
            entry["confidence"] = confidence_value
        findings.append(entry)
    return findings


class GitHubUpstreamChecker:
    """Bounded GitHub issue/PR lookup for prepared findings."""

    def __init__(
        self,
        repo: str,
        *,
        token: str = "",
        max_findings: int = 10,
        max_matches: int = 5,
        timeout: float = 20.0,
        min_interval: float | None = None,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if "/" not in repo:
            raise ValueError("repo must be in owner/name form")
        self.repo = repo.strip("/")
        self.token = token.strip()
        self.max_findings = max(1, int(max_findings))
        self.max_matches = max(1, int(max_matches))
        self.min_interval = (
            float(min_interval)
            if min_interval is not None
            else (_AUTHENTICATED_INTERVAL_SECONDS if self.token else _UNQUOTED_INTERVAL_SECONDS)
        )
        self._sleep = sleep
        self._client = client or httpx.Client(timeout=timeout, follow_redirects=False)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def check(self, findings: Sequence[Mapping[str, object]]) -> list[UpstreamCheckResult]:
        results: list[UpstreamCheckResult] = []
        limited = False
        checked = 0
        for finding in findings[: self.max_findings]:
            result = UpstreamCheckResult(
                rule_id=str(finding.get("rule_id", "") or ""),
                file_path=str(finding.get("file_path", "") or ""),
                line_start=int(finding.get("line_start", 0) or 0),
            )
            if limited:
                result.status = "rate_limited"
                result.error = "GitHub search rate limit reached"
                results.append(result)
                continue
            queries = self._build_queries(finding)
            if not queries:
                result.status = "error"
                result.error = "no distinctive terms to search"
                results.append(result)
                continue
            for query in queries:
                if checked:
                    self._sleep(self.min_interval)
                checked += 1
                result.queries.append(query)
                result.query = query
                try:
                    items, remaining = self._search(query)
                except UpstreamRateLimitError as exc:
                    result.status = "rate_limited"
                    result.error = str(exc)
                    limited = True
                    break
                except httpx.HTTPError as exc:
                    result.status = "error"
                    result.error = f"request failed: {exc}"[:200]
                    break
                if items:
                    result.matches = self._classify(items)
                    result.status = self._status(result.matches)
                    break
                if remaining is not None and remaining <= 0:
                    limited = True
                    break
            else:
                result.status = "none"
            results.append(result)
        return results

    def _search(self, query: str) -> tuple[list[Mapping[str, Any]], int | None]:
        headers = {"Accept": "application/vnd.github+json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        response = self._client.get(
            f"{GITHUB_API}/search/issues",
            params={"q": query, "per_page": self.max_matches},
            headers=headers,
        )
        if response.status_code in (403, 429):
            remaining = self._remaining(response)
            if remaining is None or remaining <= 0:
                raise UpstreamRateLimitError("GitHub search rate limit reached")
        response.raise_for_status()
        payload = response.json()
        items = payload.get("items", []) if isinstance(payload, dict) else []
        return [item for item in items if isinstance(item, Mapping)], self._remaining(response)

    @staticmethod
    def _remaining(response: httpx.Response) -> int | None:
        raw = response.headers.get("x-ratelimit-remaining", "")
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    def _build_queries(self, finding: Mapping[str, object]) -> list[str]:
        terms = self._terms(finding)
        if not terms:
            return []
        precise = f"repo:{self.repo} " + " ".join(terms[:3])
        queries = [precise]
        if len(terms) > 1:
            # GitHub search is conjunctive; relax to the most distinctive
            # term when the precise query finds nothing.
            relaxed = f"repo:{self.repo} {terms[0]}"
            if relaxed != precise:
                queries.append(relaxed)
        return queries

    def _terms(self, finding: Mapping[str, object]) -> list[str]:
        terms: list[str] = []
        metadata = finding.get("metadata")
        if isinstance(metadata, Mapping):
            trigger = str(metadata.get("trigger_operation", "") or "")
            leaf = trigger.split(".")[-1].split("::")[-1].strip()
            if len(leaf) >= 3 and leaf.lower() not in {"call", "func"}:
                terms.append(leaf)
        file_path = str(finding.get("file_path", "") or "")
        stem = re.sub(r"[^A-Za-z0-9_]", "", os.path.splitext(os.path.basename(file_path))[0])
        if len(stem) >= 3 and stem.lower() not in _GENERIC_FILE_STEMS:
            terms.append(stem)
        rule_id = str(finding.get("rule_id", "") or "")
        terms.extend(_RULE_TERMS.get(rule_id, ()))
        unique: list[str] = []
        for term in terms:
            if term not in unique:
                unique.append(term)
        return unique

    def _classify(self, items: Sequence[Mapping[str, Any]]) -> list[UpstreamMatch]:
        matches: list[UpstreamMatch] = []
        for item in items[: self.max_matches]:
            title = str(item.get("title", "") or "")
            body = str(item.get("body", "") or "")
            hint = ""
            found = _INTENT_MARKERS.search(f"{title}\n{body}")
            if found:
                hint = found.group(0).lower()
            matches.append(
                UpstreamMatch(
                    url=str(item.get("html_url", "") or ""),
                    title=title[:200],
                    state=str(item.get("state", "") or ""),
                    is_pull_request=isinstance(item.get("pull_request"), Mapping),
                    intent_hint=hint,
                )
            )
        return matches

    @staticmethod
    def _status(matches: Sequence[UpstreamMatch]) -> str:
        if not matches:
            return "none"
        if any(match.state == "open" for match in matches):
            return "mentioned"
        if any(match.intent_hint for match in matches):
            return "discussed"
        return "closed"

    @staticmethod
    def format_report(
        results: Sequence[UpstreamCheckResult],
    ) -> dict[str, object]:
        counts: dict[str, int] = {}
        for result in results:
            counts[result.status] = counts.get(result.status, 0) + 1
        return {
            "results": [result.to_dict() for result in results],
            "summary": {"total": len(results), **counts},
        }


def record_discussed(
    results: Sequence[UpstreamCheckResult],
    memory_path: Path | None = None,
) -> tuple[int, int, str]:
    """Append ``discussed`` upstream results to the user intent memory.

    Repeated entries are skipped. Returns ``(entries_added, entries_total,
    saved_path)``; nothing is written when no entry was added, so a read-only
    check does not create an empty memory file.
    """
    from smartbench.analysis.adjudication import (
        Adjudication,
        MemoryEntry,
        MemoryStore,
    )

    store = MemoryStore.load(memory_path)
    added = 0
    for result in results:
        if result.status != "discussed":
            continue
        match = next(
            (item for item in result.matches if item.intent_hint),
            result.matches[0] if result.matches else None,
        )
        if match is None:
            continue
        label = (
            Adjudication.INTENTIONAL
            if match.intent_hint in {"by design", "intentional", "intentionally", "working as intended"}
            else Adjudication.DESIGN_TRADE_OFF
        )
        before = len(store.entries)
        store = store.with_entry(
            MemoryEntry(
                label=label,
                reason=f"upstream discussed: {match.title}",
                source=match.url,
                path=result.file_path,
                rule_id=result.rule_id,
            )
        )
        added += len(store.entries) - before
    saved = str(store.save(memory_path)) if added else ""
    return added, len(store.entries), saved
