"""Git branch-aware fix checker.

Checks whether findings from a SmartBench quick scan already have
equivalent fixes applied on other branches (dev, develop, next, etc.).
Uses `git show <branch>:<path>` to read other-branch file content
without switching branches, then compares the reported function's
resource-cleanup patterns against the scan branch.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class BranchCheckResult:
    """Result of checking one finding against all local branches."""

    file_path: str
    line_start: int
    line_end: int
    function_name: Optional[str] = None
    already_fixed_on: List[str] = field(default_factory=list)
    checked_branches: List[str] = field(default_factory=list)
    error: Optional[str] = None


# ── Patterns that indicate a resource-cleanup fix ──────────────────────
_FIX_PATTERNS: list[re.Pattern] = [
    re.compile(r"defer\s+\S+\.Close\(\)"),
    re.compile(r"\.Close\(\)"),
    re.compile(r"defer\s+func\(\)\s*\{.*\.Close\(\)"),
    re.compile(r"releaseBuffer\(|backToBufPool\(|PutTCPBuffer\(|PutUDPBuffer\(|ReleaseSlot\(|closeq\(|closeAllClients\("),
]

# Branches to check (in order; first existing match wins for reporting).
_ACTIVE_BRANCHES: list[str] = [
    "dev",
    "develop",
    "next",
    "release",
]


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo)] + list(args),
        capture_output=True,
        text=True,
        timeout=60,
    )


def _list_local_branches(repo: Path) -> set[str]:
    """List local and fetched remote branches.

    A normal clone often has ``origin/dev`` but no local ``dev`` branch.
    Checking only ``git branch`` would therefore reproduce the stunner miss.
    """
    r = _run_git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes")
    if r.returncode != 0:
        return set()
    return {
        b.strip()
        for b in r.stdout.splitlines()
        if b.strip() and not b.endswith("/HEAD")
    }


def _fetch_active_branches(repo: Path, known_branches: set[str]) -> set[str]:
    """Fetch missing active branches with minimal history.

    Shallow clones normally only contain the default branch, so ``dev``
    cannot be compared unless we explicitly fetch its tip.  Each fetch is
    scoped to one well-known active branch and writes only an ``origin/*``
    remote-tracking ref; it never switches, merges, or modifies worktree
    files.
    """
    for branch in _ACTIVE_BRANCHES:
        remote_name = f"origin/{branch}"
        has_branch = any(
            ref == branch
            or ref.startswith(f"{branch}/")
            or ref == remote_name
            or ref.startswith(f"{remote_name}/")
            for ref in known_branches
        )
        if has_branch:
            continue
        r = _run_git(
            repo,
            "fetch",
            "--quiet",
            "--depth=1",
            "origin",
            f"+refs/heads/{branch}:refs/remotes/origin/{branch}",
        )
        if r.returncode == 0:
            known_branches.add(remote_name)
    return known_branches


def _read_file_at_branch(repo: Path, branch: str, path: str) -> Optional[list[str]]:
    """Read `path` from another branch via git-show."""
    r = _run_git(repo, "show", f"{branch}:{path}")
    if r.returncode != 0:
        return None
    return r.stdout.splitlines()


def _extract_function_body(lines: list[str], start: int, end: int) -> str:
    """Extract a window (±5 lines) around the reported location as the
    function body for pattern matching."""
    n = len(lines)
    lo = max(0, start - 5)
    hi = min(n, end + 5)
    return "\n".join(lines[lo:hi])


def _count_fix_patterns(text: str) -> int:
    return sum(1 for p in _FIX_PATTERNS if p.search(text))


def _func_name_from_section(lines: list[str], near_line: int) -> Optional[str]:
    """Try to extract the enclosing function name from nearby lines."""
    func_re = re.compile(r"func\s+(?:\([^)]*\)\s+)?(\w+)\s*\(")
    for i in range(max(0, near_line - 10), min(len(lines), near_line)):
        line = lines[i]
        m = func_re.search(line)
        if m:
            return m.group(1)
    return None


def _extract_named_function(lines: list[str], name: str) -> Optional[str]:
    """Extract a Go function by name, independent of line-number drift."""
    func_re = re.compile(rf"^\s*func\s+(?:\([^)]*\)\s+)?{re.escape(name)}\s*\(")
    start = next((i for i, line in enumerate(lines) if func_re.search(line)), None)
    if start is None:
        return None
    end = next(
        (i for i in range(start + 1, len(lines)) if re.match(r"^\s*func\s+", lines[i])),
        len(lines),
    )
    return "\n".join(lines[start:end])


def _parse_location(location: object) -> tuple[str, int, int]:
    """Normalize structured and LLM-style ``path:line`` locations."""
    if isinstance(location, dict):
        path = str(location.get("file_path", ""))
        start = int(location.get("line_start") or 0)
        end = int(location.get("line_end") or start)
        return path, start, end
    if isinstance(location, str):
        match = re.match(r"^(.*):([0-9]+)(?:-([0-9]+))?$", location.strip())
        if match:
            start = int(match.group(2))
            end = int(match.group(3) or start)
            return match.group(1), start, end
    return "", 0, 0


class GitBranchChecker:
    """Cross-branch fix-existence checker.

    Usage::

        checker = GitBranchChecker(Path("/tmp/my-repo"))
        result = checker.check("pkg/server.go", 45, 58)
        print(result.already_fixed_on)  # e.g. ["dev"]
    """

    def __init__(self, repo_path: Path):
        self.repo = Path(repo_path).resolve()
        if not (self.repo / ".git").exists():
            raise FileNotFoundError(f"Not a git repository: {self.repo}")
        self._local_branches = _fetch_active_branches(
            self.repo, _list_local_branches(self.repo)
        )

    def check(
        self,
        file_path: str,
        line_start: int,
        line_end: int = 0,
    ) -> BranchCheckResult:
        """Check one finding against other active branches."""
        if line_end == 0:
            line_end = line_start
        result = BranchCheckResult(
            file_path=file_path,
            line_start=line_start,
            line_end=line_end,
        )

        # Read scan-branch file (current HEAD) for baseline comparison.
        scan_file = self.repo / file_path
        if not scan_file.exists():
            result.error = f"file not found: {file_path}"
            return result
        main_lines = scan_file.read_text(encoding="utf-8", errors="replace").splitlines()
        result.function_name = _func_name_from_section(main_lines, line_start - 1)

        main_body = (
            _extract_named_function(main_lines, result.function_name)
            if result.function_name
            else None
        ) or _extract_function_body(main_lines, line_start - 1, line_end - 1)
        main_close_count = _count_fix_patterns(main_body)

        # Check active branches.
        for branch in _ACTIVE_BRANCHES:
            # Also check partial matches like "release/v1.2"
            candidates = [
                b for b in self._local_branches
                if b == branch
                or b.startswith(branch + "/")
                or b == f"origin/{branch}"
                or b.startswith(f"origin/{branch}/")
            ]
            for cand in candidates:
                if cand in result.checked_branches:
                    continue
                result.checked_branches.append(cand)

                other_lines = _read_file_at_branch(self.repo, cand, file_path)
                if other_lines is None:
                    continue

                other_body = (
                    _extract_named_function(other_lines, result.function_name)
                    if result.function_name
                    else None
                ) or _extract_function_body(other_lines, line_start - 1, line_end - 1)
                other_close_count = _count_fix_patterns(other_body)

                if other_close_count > main_close_count:
                    result.already_fixed_on.append(cand)
                    break  # only need one branch to confirm

        return result

    def check_findings(
        self,
        findings: list[dict],
    ) -> list[BranchCheckResult]:
        """Batch-check multiple findings (from quick-mode output)."""
        results = []
        for f in findings:
            loc = f.get("location", {})
            path, start, end = _parse_location(loc)
            if not path or start == 0:
                continue
            try:
                r = self.check(path, start, end)
                results.append(r)
            except Exception as exc:
                results.append(BranchCheckResult(
                    file_path=path,
                    line_start=start,
                    line_end=end,
                    error=str(exc),
                ))
        return results

    @staticmethod
    def format_report(
        results: list[BranchCheckResult],
    ) -> tuple[list[BranchCheckResult], list[BranchCheckResult]]:
        """Split results into already-fixed and clean, return as tuple."""
        already = [r for r in results if r.already_fixed_on]
        clean = [r for r in results if not r.already_fixed_on and not r.error]
        return already, clean
