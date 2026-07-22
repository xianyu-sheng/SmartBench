"""Apply machine-readable patches in a temporary copy and run tests.

This verifier is deliberately opt-in.  A temporary directory protects the
working tree from edits, but it is not an operating-system security sandbox:
project tests still execute with the current user's permissions.  Callers must
only enable it for repositories they trust.
"""

import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

_DEFAULT_MAX_COPY_FILES = 20_000
_DEFAULT_MAX_COPY_BYTES = 512 * 1024 * 1024
_DEFAULT_MAX_PATCH_BYTES = 1024 * 1024
_SENSITIVE_ENV_MARKERS = (
    "API_KEY",
    "AUTH",
    "COOKIE",
    "CREDENTIAL",
    "PASSWORD",
    "PRIVATE_KEY",
    "SECRET",
    "SESSION",
    "TOKEN",
)
_SENSITIVE_ENV_PREFIXES = (
    "AWS_",
    "AZURE_",
    "GITHUB_",
    "GITLAB_",
    "GOOGLE_",
    "OPENAI_",
)


class SandboxVerifier:
    """Apply a unified diff in a temporary project copy and validate tests."""

    def __init__(
        self,
        project_path: str,
        timeout_seconds: int = 60,
        max_copy_files: int = _DEFAULT_MAX_COPY_FILES,
        max_copy_bytes: int = _DEFAULT_MAX_COPY_BYTES,
        max_patch_bytes: int = _DEFAULT_MAX_PATCH_BYTES,
    ):
        self.project_path = Path(project_path).resolve()
        try:
            self.timeout = max(1, int(timeout_seconds))
        except (TypeError, ValueError, OverflowError):
            self.timeout = 60
        self.max_copy_files = self._positive_limit(
            max_copy_files, _DEFAULT_MAX_COPY_FILES
        )
        self.max_copy_bytes = self._positive_limit(
            max_copy_bytes, _DEFAULT_MAX_COPY_BYTES
        )
        self.max_patch_bytes = self._positive_limit(
            max_patch_bytes, _DEFAULT_MAX_PATCH_BYTES
        )

    @staticmethod
    def _positive_limit(value: int, default: int) -> int:
        try:
            return max(1, int(value))
        except (TypeError, ValueError, OverflowError):
            return default

    def verify_fix(
        self,
        file_path: str,
        original_line: int,
        suggestion: str,
        patch: str = "",
        test_command: Optional[Sequence[str]] = None,
    ) -> Dict:
        """Apply *patch* in a temporary copy and compare test results.

        ``suggestion`` and ``original_line`` are retained as explanatory
        metadata; natural-language suggestions are never treated as code.
        ``test_command`` must be an argument sequence, never a shell string.
        """
        result = {
            "status": "skipped",
            "baseline_output": "",
            "test_output": "",
            "patch_applied": False,
            "sandbox_path": "",
            "error": None,
        }

        if not isinstance(file_path, str) or not file_path.strip():
            result["error"] = "A non-empty string file path is required"
            return result
        if not isinstance(patch, str):
            result["error"] = "Patch must be a unified-diff string"
            return result

        target, error = self._resolve_project_file(file_path)
        if error:
            result["error"] = error
            return result
        if not patch or not patch.strip():
            result["error"] = "No machine-applicable unified diff was provided"
            return result
        if len(patch.encode("utf-8", errors="ignore")) > self.max_patch_bytes:
            result["error"] = "Patch exceeds the safe size limit"
            return result

        target_relative = target.relative_to(self.project_path).as_posix()
        patch_error = self._validate_patch_paths(patch, target_relative)
        if patch_error:
            result["error"] = patch_error
            return result

        if isinstance(test_command, (str, bytes)):
            result["error"] = "test_command must be an argument list, not a shell string"
            return result
        command = list(test_command) if test_command else self._detect_test_command()
        if not command or not all(isinstance(part, str) and part for part in command):
            result["error"] = "No test command detected for this project"
            return result

        sandbox_root: Optional[Path] = None
        try:
            sandbox_root = Path(tempfile.mkdtemp(prefix="smartbench_sandbox_"))
            result["sandbox_path"] = str(sandbox_root)
            baseline_sandbox = sandbox_root / "baseline"
            patched_sandbox = sandbox_root / "patched"
            baseline_sandbox.mkdir()
            patched_sandbox.mkdir()
            self._copy_project(baseline_sandbox)
            self._copy_project(patched_sandbox)

            sandbox_target = patched_sandbox / target.relative_to(self.project_path)
            if not sandbox_target.is_file():
                result["error"] = f"File missing in sandbox: {file_path}"
                return result

            baseline = self._run_tests(baseline_sandbox, command)
            result["baseline_output"] = baseline.get("output", "")[:2000]
            if baseline["status"] != "passed":
                result["status"] = "baseline_failed"
                result["error"] = (
                    "Baseline tests did not pass; the proposed patch cannot be evaluated"
                )
                if baseline.get("error"):
                    result["error"] += f": {baseline['error']}"
                return result

            applied = self._apply_patch(patched_sandbox, patch)
            if applied["status"] != "passed":
                result["status"] = "failed"
                result["error"] = applied.get("error") or "Patch could not be applied"
                result["test_output"] = applied.get("output", "")[:2000]
                return result
            result["patch_applied"] = True

            after = self._run_tests(patched_sandbox, command)
            result["test_output"] = after.get("output", "")[:2000]
            result["status"] = after["status"]
            if after["status"] != "passed":
                result["error"] = after.get("error") or "Tests failed after applying patch"
            return result
        except Exception as exc:
            result["status"] = "error"
            result["error"] = str(exc)[:500]
            return result
        finally:
            if sandbox_root is not None:
                shutil.rmtree(sandbox_root, ignore_errors=True)

    def verify_all_proposals(self, proposals: List[Dict]) -> List[Dict]:
        """Annotate proposals that contain an explicit unified diff."""
        for proposal in proposals:
            if not isinstance(proposal, dict):
                continue

            location = proposal.get("location", "")
            file_path, line = self._parse_location(location)
            if not file_path:
                proposal["__sandbox_verification"] = {
                    "status": "skipped",
                    "error": "No file location in proposal",
                }
                continue

            try:
                proposal["__sandbox_verification"] = self.verify_fix(
                    file_path=file_path,
                    original_line=line or 1,
                    suggestion=(
                        proposal.get("implementation", "")
                        or proposal.get("solution", "")
                    ),
                    patch=proposal.get("patch", "") or "",
                )
            except Exception as exc:
                proposal["__sandbox_verification"] = {
                    "status": "error",
                    "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                }
        return proposals

    def _resolve_project_file(self, file_path: str) -> Tuple[Optional[Path], Optional[str]]:
        """Resolve a proposal path and reject absolute/traversing/symlink paths."""
        raw = Path(file_path)
        if raw.is_absolute():
            return None, f"Absolute paths are not allowed: {file_path}"
        try:
            target = (self.project_path / raw).resolve(strict=True)
            target.relative_to(self.project_path)
        except (OSError, RuntimeError, ValueError):
            return None, f"File is missing or outside the project: {file_path}"
        if not target.is_file():
            return None, f"Not a regular file: {file_path}"
        if (self.project_path / raw).is_symlink():
            return None, f"Symlink targets are not allowed: {file_path}"
        return target, None

    def _validate_patch_paths(
        self,
        patch: str,
        allowed_path: Optional[str] = None,
    ) -> Optional[str]:
        """Reject unsafe paths and edits outside the proposal target."""
        paths = []
        for line in patch.splitlines():
            values = []
            if line.startswith(("--- ", "+++ ")):
                values = [line[4:].split("\t", 1)[0].strip()]
            elif line.startswith("diff --git "):
                try:
                    parts = shlex.split(line)
                except ValueError:
                    return "Patch has malformed diff headers"
                if len(parts) != 4:
                    return "Patch has malformed diff headers"
                values = parts[2:]

            for value in values:
                if value == "/dev/null":
                    continue
                if value.startswith(("a/", "b/")):
                    value = value[2:]
                paths.append(value)

        if not paths:
            return "Patch has no unified-diff file headers"
        for value in paths:
            path = PurePosixPath(value)
            if path.is_absolute() or ".." in path.parts or not value:
                return f"Patch path is outside the project: {value}"
            if allowed_path is not None and path.as_posix() != allowed_path:
                return f"Patch modifies an unexpected file: {value}"
        return None

    def _copy_project(self, sandbox: Path) -> None:
        """Copy a bounded set of regular project files without symlinks."""
        skip_dirs = {
            ".git", "node_modules", "__pycache__", ".venv", "venv",
            "target", "build", "dist", ".smartbench", ".pytest_cache",
            ".mypy_cache", ".ruff_cache",
        }
        skip_suffixes = {".pyc", ".pyo", ".so", ".o", ".a", ".whl", ".egg"}

        copied_files = 0
        copied_bytes = 0
        for current, dirnames, filenames in os.walk(
            self.project_path, topdown=True, followlinks=False
        ):
            current_path = Path(current)
            relative_dir = current_path.relative_to(self.project_path)
            destination_dir = sandbox / relative_dir
            destination_dir.mkdir(parents=True, exist_ok=True)
            dirnames[:] = [
                dirname
                for dirname in sorted(dirnames)
                if dirname not in skip_dirs
                and not (current_path / dirname).is_symlink()
            ]

            for filename in sorted(filenames):
                source = current_path / filename
                if (
                    source.is_symlink()
                    or not source.is_file()
                    or source.suffix in skip_suffixes
                ):
                    continue
                try:
                    size = source.stat().st_size
                except OSError:
                    continue
                copied_files += 1
                copied_bytes += size
                if copied_files > self.max_copy_files:
                    raise RuntimeError("Project exceeds sandbox copy file limit")
                if copied_bytes > self.max_copy_bytes:
                    raise RuntimeError("Project exceeds sandbox copy byte limit")
                shutil.copy2(source, destination_dir / filename)

    def _detect_test_command(self) -> Optional[List[str]]:
        """Return a shell-free test command for a recognized project."""
        root_files = {item.name for item in self.project_path.iterdir() if item.is_file()}
        if (
            ("pyproject.toml" in root_files or "setup.py" in root_files)
            and (self.project_path / "tests").is_dir()
        ):
            return [sys.executable, "-m", "pytest", "tests", "-x"]
        if "go.mod" in root_files:
            return ["go", "test", "./...", "-timeout", "30s"]
        if "Cargo.toml" in root_files:
            return ["cargo", "test"]
        if "package.json" in root_files:
            return ["npm", "test"]
        if "Makefile" in root_files:
            return ["make", "test"]
        return None

    def _apply_patch(self, sandbox: Path, patch: str) -> Dict:
        """Validate and apply a unified diff with git, without a shell."""
        if shutil.which("git") is None:
            return {
                "status": "skipped",
                "output": "",
                "error": "git is required to apply a unified diff",
            }
        check = self._run_process(
            ["git", "apply", "--check", "--recount", "-"], sandbox, input_text=patch
        )
        if check["status"] != "passed":
            check["error"] = "Patch validation failed"
            return check
        applied = self._run_process(
            ["git", "apply", "--recount", "-"], sandbox, input_text=patch
        )
        if applied["status"] != "passed":
            applied["error"] = "Patch application failed"
        return applied

    def _run_tests(self, sandbox: Path, command: Sequence[str]) -> Dict:
        return self._run_process(list(command), sandbox)

    def _run_process(
        self,
        command: List[str],
        cwd: Path,
        input_text: Optional[str] = None,
    ) -> Dict:
        try:
            proc = subprocess.run(
                command,
                cwd=str(cwd),
                input=input_text,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                shell=False,
                env=self._subprocess_environment(),
            )
            output = (proc.stdout[-3000:] + "\n" + proc.stderr[-1000:]).strip()
            return {
                "status": "passed" if proc.returncode == 0 else "failed",
                "output": output,
                "exit_code": proc.returncode,
                "error": None,
            }
        except subprocess.TimeoutExpired as exc:
            output = ""
            if isinstance(exc.stdout, str):
                output += exc.stdout[-2000:]
            if isinstance(exc.stderr, str):
                output += exc.stderr[-1000:]
            return {
                "status": "timeout",
                "output": output,
                "exit_code": -1,
                "error": f"Test execution timed out after {self.timeout}s",
            }
        except Exception as exc:
            return {
                "status": "error",
                "output": "",
                "exit_code": -1,
                "error": str(exc),
            }

    @staticmethod
    def _subprocess_environment() -> Dict[str, str]:
        """Drop credential-like variables before repository code executes."""
        environment = {}
        for name, value in os.environ.items():
            upper_name = name.upper()
            if upper_name.startswith(_SENSITIVE_ENV_PREFIXES):
                continue
            if any(marker in upper_name for marker in _SENSITIVE_ENV_MARKERS):
                continue
            environment[name] = value
        environment.update({
            "CI": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "PIP_NO_INPUT": "1",
            "SMARTBENCH_SANDBOX": "1",
        })
        return environment

    @staticmethod
    def _parse_location(location: str) -> Tuple[Optional[str], Optional[int]]:
        if not isinstance(location, str) or not location.strip():
            return None, None
        match = re.search(r"^(.+?):(\d+)(?:-\d+)?$", location.strip())
        if match:
            return match.group(1), int(match.group(2))
        return location.strip(), None
