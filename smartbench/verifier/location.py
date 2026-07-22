"""
LocationVerifier — checks if a claimed file:line exists on disk.

Handles LLM-hallucinated paths through fuzzy resolution:
  1. Exact match
  2. Filename-only match (e.g., "dataLoader.js" → "frontend/src/utils/dataLoader.ts")
  3. Path-suffix match (e.g., "utils/dataLoader" → "src/utils/dataLoader.tsx")
  4. Levenshtein distance on filename stem (for near-miss typos)
"""

import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from smartbench.path_safety import is_project_file, resolve_project_file
from smartbench.verifier import VerificationResult, VerificationStatus

logger = logging.getLogger(__name__)


class LocationVerifier:
    """
    Verifies that claimed file paths and line numbers actually exist.
    """

    def __init__(self, project_path: str):
        """
        Args:
            project_path: Root directory of the project
        """
        self.project_path = Path(project_path).resolve()
        self._file_cache: Dict[str, List[str]] = {}  # path -> lines
        self._file_index: Optional[Dict[str, List[str]]] = None

    # ── Public API ──────────────────────────────────────────────────────

    def verify(self, file_path: str,
               line: Optional[int] = None,
               function_name: Optional[str] = None,
               content_hint: Optional[str] = None) -> VerificationResult:
        """
        Check if file:line exists.

        Args:
            file_path: Claimed relative file path
            line: Claimed line number (1-based)
            function_name: Claimed function name (for graph cross-reference)
            content_hint: Optional expected content snippet

        Returns:
            VerificationResult with resolution details
        """
        if not isinstance(file_path, str):
            file_path = ""
        if isinstance(line, str) and line.strip().isdigit():
            line = int(line.strip())
        if not isinstance(function_name, str):
            function_name = None

        result = VerificationResult(
            status=VerificationStatus.UNVERIFIABLE,
            claim=f"{file_path}:{line}" if line else file_path,
            claimed_file=file_path,
            claimed_line=line,
        )

        # Try exact match first
        full_path = resolve_project_file(self.project_path, file_path)
        if full_path is not None:
            result.resolved_file = str(
                full_path.relative_to(self.project_path)
            ).replace("\\", "/")
            return self._verify_line(result, full_path, line, function_name)

        # Fuzzy resolution
        resolved = self._fuzzy_resolve(file_path)
        if resolved:
            result.resolved_file = resolved
            result.status = VerificationStatus.PARTIAL
            result.detail = (
                f"文件路径已修正: '{file_path}' -> '{resolved}'"
            )
            full_path = resolve_project_file(self.project_path, resolved)
            if full_path is not None:
                return self._verify_line(result, full_path, line, function_name)

        # Not found
        result.status = VerificationStatus.HALLUCINATED
        result.confidence = 0.0
        result.detail = f"文件不存在: '{file_path}'（项目内未找到同名或相似文件）"
        return result

    def verify_multiple(self, claims: List[Dict]) -> List[VerificationResult]:
        """
        Verify multiple claims at once.

        Args:
            claims: List of dicts with 'file_path', 'line', 'function_name', 'content_hint'

        Returns:
            List of VerificationResult
        """
        results = []
        for claim in claims:
            result = self.verify(
                file_path=claim.get("file_path", ""),
                line=claim.get("line"),
                function_name=claim.get("function_name"),
                content_hint=claim.get("content_hint"),
            )
            results.append(result)
        return results

    # ── Fuzzy path resolution ───────────────────────────────────────────

    def _fuzzy_resolve(self, claimed: str) -> Optional[str]:
        """
        Fuzzy path resolution for LLM-hallucinated paths.

        Tries multiple strategies in order of reliability:
          1. Exact filename match (any location)
          2. Suffix-aware filename match (correct file extension)
          3. Path segment overlap + filename match
          4. Levenshtein distance on filename
        """
        if not claimed or not claimed.strip():
            return None

        # Build file index lazily
        if self._file_index is None:
            self._build_file_index()

        claimed_name = Path(claimed).name
        claimed_suffix = Path(claimed).suffix.lower()
        claimed_stem = Path(claimed).stem.lower()

        # Strategy 1: Exact filename match
        exact_paths = self._file_index.get(claimed_name.lower(), [])
        if len(exact_paths) == 1:
            return exact_paths[0]
        if len(exact_paths) > 1:
            claimed_parts = {part.lower() for part in Path(claimed).parts[:-1]}
            ranked = sorted(
                (
                    len(
                        claimed_parts
                        & {part.lower() for part in Path(path).parts[:-1]}
                    ),
                    path,
                )
                for path in exact_paths
            )
            if claimed_parts and ranked[-1][0] > ranked[-2][0]:
                return ranked[-1][1]
            return None

        # Strategy 2: Stem match with correct extension
        candidates: List[Tuple[str, float]] = []
        for fname, paths in self._file_index.items():
            fstem = Path(fname).stem.lower()
            fsuffix = Path(fname).suffix.lower()
            for fpath in paths:
                score = 0.0
                if fstem == claimed_stem:
                    score += 0.8
                    if fsuffix == claimed_suffix:
                        score += 0.2
                elif claimed_stem in fstem or fstem in claimed_stem:
                    score += 0.5
                    if fsuffix == claimed_suffix:
                        score += 0.2
                elif self._levenshtein(fstem, claimed_stem) <= 3:
                    score += 0.3

                claimed_parts = Path(claimed).parts
                path_parts = Path(fpath).parts
                overlap = len(
                    {part.lower() for part in claimed_parts}
                    & {part.lower() for part in path_parts}
                )
                score += overlap * 0.1

                if score > 0.3:
                    candidates.append((fpath, score))

        if candidates:
            candidates.sort(key=lambda x: -x[1])
            return candidates[0][0]

        return None

    def _build_file_index(self):
        """Build a dictionary mapping lowercase filename → relative path."""
        self._file_index = {}
        excluded = {'.git', 'node_modules', '__pycache__', 'target',
                     'build', '.venv', 'venv', 'dist', '.smartbench'}

        indexed = 0
        for current, dirnames, filenames in os.walk(
            self.project_path, topdown=True, followlinks=False
        ):
            current_path = Path(current)
            dirnames[:] = [
                dirname
                for dirname in sorted(dirnames)
                if dirname not in excluded
                and not (current_path / dirname).is_symlink()
            ]
            for filename in sorted(filenames):
                path = current_path / filename
                if not is_project_file(self.project_path, path):
                    continue
                try:
                    rel = str(path.relative_to(self.project_path)).replace(
                        '\\', '/'
                    )
                except ValueError:
                    continue
                self._file_index.setdefault(path.name.lower(), []).append(rel)
                indexed += 1
                if indexed >= 20_000:
                    return

    # ── Line verification ───────────────────────────────────────────────

    def _verify_line(self, result: VerificationResult,
                     full_path: Path,
                     line: Optional[int],
                     function_name: Optional[str]) -> VerificationResult:
        """
        Verify line number and optionally locate function at that line.

        IMPORTANT: Does NOT upgrade PARTIAL → VERIFIED if the original
        match was fuzzy (i.e., path was corrected). Keeps PARTIAL status
        but adds confidence.
        """
        was_fuzzy = (result.status == VerificationStatus.PARTIAL)

        if line is None:
            if not was_fuzzy:
                result.status = VerificationStatus.VERIFIED
            result.confidence = 0.7 if was_fuzzy else 0.9
            result.detail = f"文件存在: {result.resolved_file}"
            return result

        try:
            lines = self._read_file(full_path)
        except Exception as e:
            result.detail = f"无法读取文件: {e}"
            return result

        if line is not None and (
            not isinstance(line, int) or isinstance(line, bool)
        ):
            result.confidence = 0.0
            result.detail = f"行号格式无效: {line!r}"
            return result

        if 1 <= line <= len(lines):
            result.resolved_line = line
            ctx_start = max(0, line - 4)
            ctx_end = min(len(lines), line + 3)
            result.actual_content = '\n'.join(lines[ctx_start:ctx_end])

            if function_name:
                fun_found = self._find_function_at(lines, line, function_name)
                if fun_found:
                    result.actual_function = fun_found
                    if not was_fuzzy:
                        result.status = VerificationStatus.VERIFIED
                    result.confidence = 0.75 if was_fuzzy else 1.0
                    result.detail = (
                        f"验证通过: {result.resolved_file}:{line}, "
                        f"函数 '{fun_found}' 已确认"
                    )
                else:
                    result.confidence = 0.5 if was_fuzzy else 0.7
                    result.detail = (
                        f"文件存在，但行 {line} 处未找到函数 '{function_name}'"
                    )
            else:
                if not was_fuzzy:
                    result.status = VerificationStatus.VERIFIED
                result.confidence = 0.6 if was_fuzzy else 0.9
                result.detail = f"文件与行号已确认: {result.resolved_file}:{line}"
        else:
            result.confidence = 0.3
            result.detail = (
                f"文件存在，但行号 {line} 超出范围 "
                f"(文件共 {len(lines)} 行)"
            )

        return result

    def _read_file(self, path: Path) -> List[str]:
        """Read file with caching."""
        key = str(path)
        if key not in self._file_cache:
            try:
                content = path.read_text(encoding='utf-8', errors='ignore')
                self._file_cache[key] = content.split('\n')
            except Exception:
                self._file_cache[key] = []
        return self._file_cache[key]

    def _find_function_at(self, lines: List[str], line: int,
                          expected_name: str) -> Optional[str]:
        """
        Search around the given line for a function definition matching
        the expected name.
        """
        # Search backwards from line for function/class definition
        for i in range(line - 1, max(-1, line - 31), -1):
            ln = lines[i].strip()
            # Common function definition patterns
            for pattern in [
                r'(?:def|fn|func|function)\s+(\w+)',
                r'(\w+)\s*[:=]\s*(?:function|func|async\s+function)',
                r'(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(',
                r'(?:public|private|protected|static)\s+\w+\s+(\w+)\s*\(',
            ]:
                m = re.search(pattern, ln)
                if m and m.group(1).lower() == expected_name.lower():
                    return m.group(1)

        return None

    @staticmethod
    def _levenshtein(a: str, b: str) -> int:
        """Compute Levenshtein distance between two strings."""
        if len(a) < len(b):
            return LocationVerifier._levenshtein(b, a)
        if len(b) == 0:
            return len(a)
        prev = list(range(len(b) + 1))
        for i, ca in enumerate(a):
            curr = [i + 1]
            for j, cb in enumerate(b):
                curr.append(min(
                    prev[j + 1] + 1,     # deletion
                    curr[j] + 1,          # insertion
                    prev[j] + (ca != cb), # substitution
                ))
            prev = curr
        return prev[-1]
