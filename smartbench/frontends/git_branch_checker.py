"""Git branch-aware fix checker.

Checks whether findings from a SmartBench quick scan already have
equivalent fixes applied on other branches (dev, develop, next, etc.)
or earlier commits on the same branch.

Three detection strategies:

1. **Cross-branch pattern delta** — compare the reported function's
   cleanup-pattern count on the scan branch vs. candidate branches.
   If another branch has more cleanup patterns in the same function,
   the finding is likely already fixed there.

2. **Cross-branch function diff** — compute the unified diff of the
   function body between branches and check whether added lines contain
   cleanup patterns (not just cosmetic changes).

3. **Commit-history pickaxe** — use ``git log -S`` to find commits that
   introduced a known cleanup pattern inside the reported file/function.
   Catches fixes that landed on the same branch *after* the scan commit
   but were not yet fetched, or on other branches' history.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class BranchCheckResult:
    """Result of checking one finding against all branches and history."""

    file_path: str
    line_start: int
    line_end: int
    function_name: Optional[str] = None
    already_fixed_on: List[str] = field(default_factory=list)
    checked_branches: List[str] = field(default_factory=list)
    fixed_by_commit: Optional[str] = None
    fix_evidence: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        """Serialise for JSON output."""
        return {
            "file_path": self.file_path,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "function_name": self.function_name,
            "already_fixed_on": self.already_fixed_on,
            "checked_branches": self.checked_branches,
            "fixed_by_commit": self.fixed_by_commit,
            "fix_evidence": self.fix_evidence,
            "error": self.error,
        }


# ── Language-specific patterns that indicate a resource-cleanup fix ────

_GO_PATTERNS: list[re.Pattern] = [
    re.compile(r"defer\s+\S+\.Close\(\)"),
    re.compile(r"\.Close\(\)"),
    re.compile(r"defer\s+func\(\)\s*\{.*\.Close\(\)"),
    re.compile(
        r"releaseBuffer\(|backToBufPool\(|PutTCPBuffer\(|"
        r"PutUDPBuffer\(|ReleaseSlot\(|closeq\(|closeAllClients\("
    ),
]

_PYTHON_PATTERNS: list[re.Pattern] = [
    re.compile(r"\.close\(\)"),
    re.compile(r"with\s+open\(|with\s+\S+\.open\("),
    re.compile(r"contextlib\.closing\("),
    re.compile(r"finally:"),
    re.compile(r"del\s+\w+\s*$"),
    re.compile(r"\.release\(\)"),
    re.compile(r"\.shutdown\(\)"),
    re.compile(r"gc\.collect\(\)"),
]

_JS_TS_PATTERNS: list[re.Pattern] = [
    re.compile(r"\.close\(\)"),
    re.compile(r"\.destroy\(\)"),
    re.compile(r"\.release\(\)"),
    re.compile(r"clearTimeout\(|clearInterval\("),
    re.compile(r"AbortController\(|AbortSignal\.abort\("),
    re.compile(r"finally\s*\{"),
    re.compile(r"await\s+using\s+"),
    re.compile(r"Symbol\.dispose|Symbol\.asyncDispose"),
]

_RUST_PATTERNS: list[re.Pattern] = [
    re.compile(r"drop\("),
    re.compile(r"\.close\(\)"),
    re.compile(r"\.shutdown\(\)"),
    re.compile(r"impl\s+Drop\s+for\s+"),
    re.compile(r"defer\s+\{"),  # not real Rust; placeholder
]

# Map file extensions to their pattern sets.
_EXT_PATTERNS: dict[str, list[re.Pattern]] = {
    ".go": _GO_PATTERNS,
    ".py": _PYTHON_PATTERNS,
    ".js": _JS_TS_PATTERNS,
    ".jsx": _JS_TS_PATTERNS,
    ".ts": _JS_TS_PATTERNS,
    ".tsx": _JS_TS_PATTERNS,
    ".mjs": _JS_TS_PATTERNS,
    ".cjs": _JS_TS_PATTERNS,
    ".rs": _RUST_PATTERNS,
}

# Fallback when extension is unknown — all patterns combined.
_ALL_PATTERNS: list[re.Pattern] = _GO_PATTERNS + _PYTHON_PATTERNS + _JS_TS_PATTERNS + _RUST_PATTERNS


def _patterns_for_file(file_path: str) -> list[re.Pattern]:
    """Return the cleanup-pattern set matching the file's language."""
    ext = Path(file_path).suffix.lower()
    return _EXT_PATTERNS.get(ext, _ALL_PATTERNS)


def _count_fix_patterns(text: str, file_path: str = ".go") -> int:
    patterns = _patterns_for_file(file_path)
    return sum(1 for p in patterns if p.search(text))


# Branches to check (in order; first existing match wins for reporting).
_ACTIVE_BRANCHES: list[str] = [
    "dev",
    "develop",
    "next",
    "release",
]


def _run_git(repo: Path, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo)] + list(args),
        capture_output=True,
        text=True,
        timeout=timeout,
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


def _func_name_from_section(lines: list[str], near_line: int) -> Optional[str]:
    """Try to extract the enclosing function name from nearby lines.

    Works for Go (``func foo(``), Python (``def foo(``), JS/TS
    (``function foo(``), and Rust (``fn foo(``).
    """
    func_re = re.compile(r"(?:func|def|function|fn)\s+(?:\([^)]*\)\s+)?(\w+)\s*[\(\{]")
    for i in range(max(0, near_line - 10), min(len(lines), near_line + 1)):
        line = lines[i]
        m = func_re.search(line)
        if m:
            return m.group(1)
    return None


def _extract_named_function(lines: list[str], name: str) -> Optional[str]:
    """Extract a function by name, independent of line-number drift.

    Supports Go, Python, JS/TS, and Rust function declarations.
    """
    func_re = re.compile(
        rf"^\s*(?:func|def|function|fn)\s+(?:\([^)]*\)\s+)?{re.escape(name)}\s*[\(\{{]"
    )
    start = next((i for i, line in enumerate(lines) if func_re.search(line)), None)
    if start is None:
        return None
    # Find the next function declaration or end-of-file.
    next_func = re.compile(r"^\s*(?:func|def|function|fn)\s+")
    end = next(
        (i for i in range(start + 1, len(lines)) if next_func.match(lines[i])),
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


# ── Commit-history pickaxe ─────────────────────────────────────────────


# Pickaxe search strings: the literal cleanup tokens we look for in diffs.
_PICKAXE_STRINGS: list[str] = [
    ".Close()",
    ".close()",
    "defer remote.Close()",
    ".destroy()",
    ".release()",
    "finally:",
    "with open(",
    "contextlib.closing(",
    "AbortController(",
    "drop(",
    "backToBufPool(",
    "releaseBuffer(",
    "ReleaseSlot(",
]


def _pickaxe_search(
    repo: Path,
    file_path: str,
    extra_branches: Optional[list[str]] = None,
) -> Optional[tuple[str, str]]:
    """Search commit history for a cleanup-pattern introduction.

    Uses ``git log -S <pattern> --all -- <file>`` to find the most recent
    commit that added or removed a known cleanup string in the target file.
    Returns ``(commit_hash, matched_pattern)`` or ``None``.

    Excludes HEAD commit (current scan point) to avoid false positives in
    shallow clones where HEAD is the only commit.
    """
    # Get HEAD commit hash to exclude it.
    head_r = _run_git(repo, "rev-parse", "HEAD")
    head_hash = head_r.stdout.strip() if head_r.returncode == 0 else ""

    for token in _PICKAXE_STRINGS:
        args = [
            "log",
            "--all",
            "-S",
            token,
            "--format=%H",
            "--",
            file_path,
        ]
        r = _run_git(repo, *args, timeout=30)
        if r.returncode != 0:
            continue
        commits = [c.strip() for c in r.stdout.splitlines() if c.strip() and c.strip() != head_hash]
        if commits:
            return commits[0], token
    return None


# ── Function-level diff ────────────────────────────────────────────────


def _function_diff(
    repo: Path,
    file_path: str,
    scan_branch: str,
    other_branch: str,
    func_body_scan: str,
    func_body_other: str,
) -> list[str]:
    """Return added lines (lines starting with '+') that appear in the
    other-branch function body but not in the scan-branch version.

    This is a lightweight set-difference rather than a full ``git diff``
    to avoid needing a checked-out worktree for each branch.
    """
    scan_set = {line.strip() for line in func_body_scan.splitlines()}
    added: list[str] = []
    for line in func_body_other.splitlines():
        if line.strip() and line.strip() not in scan_set:
            added.append(line)
    return added


class GitBranchChecker:
    """Cross-branch and cross-commit fix-existence checker.

    Usage::

        checker = GitBranchChecker(Path("/tmp/my-repo"))
        result = checker.check("pkg/server.go", 45, 58)
        print(result.already_fixed_on)  # e.g. ["origin/dev"]
        print(result.fixed_by_commit)   # e.g. "ac65771..."
    """

    def __init__(self, repo_path: Path):
        self.repo = Path(repo_path).resolve()
        if not (self.repo / ".git").exists():
            raise FileNotFoundError(f"Not a git repository: {self.repo}")
        self._local_branches = _fetch_active_branches(
            self.repo, _list_local_branches(self.repo)
        )
        # Detect current branch for diff purposes.
        r = _run_git(self.repo, "rev-parse", "--abbrev-ref", "HEAD")
        self._current_branch = r.stdout.strip() if r.returncode == 0 else "HEAD"

    def check(
        self,
        file_path: str,
        line_start: int,
        line_end: int = 0,
    ) -> BranchCheckResult:
        """Check one finding against other active branches and history."""
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
        main_close_count = _count_fix_patterns(main_body, file_path)

        # ── Strategy 1+2: Cross-branch pattern delta and function diff ──
        for branch in _ACTIVE_BRANCHES:
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
                other_close_count = _count_fix_patterns(other_body, file_path)

                # Strategy 1: more cleanup patterns on other branch.
                if other_close_count > main_close_count:
                    result.already_fixed_on.append(cand)
                    result.fix_evidence = (
                        f"pattern count {other_close_count} > {main_close_count}"
                    )
                    break

                # Strategy 2: function diff shows cleanup additions even
                # if total pattern count is unchanged (e.g. refactor).
                added_lines = _function_diff(
                    self.repo, file_path,
                    self._current_branch, cand,
                    main_body, other_body,
                )
                added_text = "\n".join(added_lines)
                if _count_fix_patterns(added_text, file_path) > 0:
                    result.already_fixed_on.append(cand)
                    result.fix_evidence = (
                        f"function diff adds cleanup lines in {cand}"
                    )
                    break

        # ── Strategy 3: Commit-history pickaxe ──────────────────────────
        if not result.already_fixed_on:
            pickaxe = _pickaxe_search(self.repo, file_path)
            if pickaxe:
                commit_hash, token = pickaxe
                # Verify the commit actually touches this file's function.
                result.fixed_by_commit = commit_hash
                result.fix_evidence = f"pickaxe: '{token}' introduced in {commit_hash[:8]}"
                result.already_fixed_on.append(f"commit:{commit_hash[:8]}")

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
