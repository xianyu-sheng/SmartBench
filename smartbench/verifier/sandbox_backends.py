"""Execution backends for the opt-in sandbox verifier.

Two backends, both explicit opt-ins:

- ``LocalTempBackend`` — the existing behavior: a temporary copy of the project
  and a host subprocess running with the current user's permissions. It
  protects the working tree, **not** the host.
- ``ContainerBackend`` — a rootless podman/docker container with networking
  disabled, a read-only root filesystem, a writable workspace mount, a tmpfs
  ``/tmp``, a non-root user on POSIX, resource limits and an explicit
  environment allowlist (no host variables pass through).

A container shares the host kernel; this is a stronger boundary than running
tests directly, not a microVM and not a formal security guarantee.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Protocol, Sequence

from smartbench.subprocess_utils import run_bounded

logger = logging.getLogger(__name__)

SENSITIVE_ENV_MARKERS = (
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
SENSITIVE_ENV_PREFIXES = (
    "AWS_",
    "AZURE_",
    "GITHUB_",
    "GITLAB_",
    "GOOGLE_",
    "OPENAI_",
)

DEFAULT_CONTAINER_IMAGE = "python:3.12-slim"


class SandboxBackendError(RuntimeError):
    """The requested backend cannot be used on this machine."""


@dataclass(frozen=True)
class BackendResult:
    status: str
    output: str
    exit_code: int
    error: Optional[str] = None


class SandboxBackend(Protocol):
    name: str

    def run(
        self,
        command: Sequence[str],
        cwd: Path,
        *,
        timeout: int,
        input_text: Optional[str] = None,
    ) -> BackendResult: ...


class LocalTempBackend:
    """Run commands on the host with filtered environment variables."""

    name = "local"

    def run(
        self,
        command: Sequence[str],
        cwd: Path,
        *,
        timeout: int,
        input_text: Optional[str] = None,
    ) -> BackendResult:
        try:
            proc = run_bounded(
                list(command),
                cwd=str(cwd),
                timeout=timeout,
                env=self.subprocess_environment(),
                input_text=input_text,
            )
            output = (proc.stdout[-3000:] + "\n" + proc.stderr[-1000:]).strip()
            return BackendResult(
                status="passed" if proc.returncode == 0 else "failed",
                output=output,
                exit_code=proc.returncode,
            )
        except subprocess.TimeoutExpired as exc:
            output = ""
            if isinstance(exc.stdout, str):
                output += exc.stdout[-2000:]
            if isinstance(exc.stderr, str):
                output += exc.stderr[-1000:]
            return BackendResult(
                status="timeout",
                output=output,
                exit_code=-1,
                error=f"Test execution timed out after {timeout}s",
            )
        except Exception as exc:
            return BackendResult(
                status="error",
                output="",
                exit_code=-1,
                error=str(exc),
            )

    @staticmethod
    def subprocess_environment() -> Dict[str, str]:
        """Drop credential-like variables before repository code executes."""
        environment = {}
        for name, value in os.environ.items():
            upper_name = name.upper()
            if upper_name.startswith(SENSITIVE_ENV_PREFIXES):
                continue
            if any(marker in upper_name for marker in SENSITIVE_ENV_MARKERS):
                continue
            environment[name] = value
        environment.update(
            {
                "CI": "1",
                "GIT_TERMINAL_PROMPT": "0",
                "PIP_NO_INPUT": "1",
                "SMARTBENCH_SANDBOX": "1",
            }
        )
        return environment


class ContainerBackend:
    """Run commands in a rootless container with deny-by-default isolation."""

    name = "container"

    def __init__(
        self,
        *,
        image: str = DEFAULT_CONTAINER_IMAGE,
        runtime: Optional[str] = None,
        memory: str = "1g",
        cpus: str = "2.0",
        pids_limit: int = 256,
        tmpfs_size: str = "64m",
    ):
        self.image = image or DEFAULT_CONTAINER_IMAGE
        self.runtime = _detect_runtime(runtime)
        self.memory = memory
        self.cpus = cpus
        self.pids_limit = max(16, int(pids_limit))
        self.tmpfs_size = tmpfs_size

    def run(
        self,
        command: Sequence[str],
        cwd: Path,
        *,
        timeout: int,
        input_text: Optional[str] = None,
    ) -> BackendResult:
        workspace = Path(cwd).resolve()
        if not workspace.is_dir():
            return BackendResult(
                status="error",
                output="",
                exit_code=-1,
                error=f"workspace not found: {workspace}",
            )
        args = self._command(list(command), workspace, input_text=input_text)
        try:
            proc = run_bounded(args, cwd=str(workspace), timeout=timeout, input_text=input_text)
            output = (proc.stdout[-3000:] + "\n" + proc.stderr[-1000:]).strip()
            return BackendResult(
                status="passed" if proc.returncode == 0 else "failed",
                output=output,
                exit_code=proc.returncode,
            )
        except subprocess.TimeoutExpired as exc:
            output = ""
            if isinstance(exc.stdout, str):
                output += exc.stdout[-2000:]
            if isinstance(exc.stderr, str):
                output += exc.stderr[-1000:]
            return BackendResult(
                status="timeout",
                output=output,
                exit_code=-1,
                error=f"Container execution timed out after {timeout}s",
            )
        except Exception as exc:
            return BackendResult(
                status="error",
                output="",
                exit_code=-1,
                error=str(exc),
            )

    def _command(
        self,
        command: Sequence[str],
        workspace: Path,
        *,
        input_text: Optional[str] = None,
    ) -> list[str]:
        args = [
            self.runtime,
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--security-opt",
            "no-new-privileges",
            "--cap-drop",
            "ALL",
            "--pids-limit",
            str(self.pids_limit),
            "--memory",
            self.memory,
            "--cpus",
            self.cpus,
            "--tmpfs",
            f"/tmp:rw,nosuid,nodev,size={self.tmpfs_size}",
            "-v",
            f"{workspace}:/workspace:rw",
            "-w",
            "/workspace",
            "--env",
            "CI=1",
            "--env",
            "HOME=/tmp",
            "--env",
            "GIT_TERMINAL_PROMPT=0",
            "--env",
            "SMARTBENCH_SANDBOX=1",
        ]
        user = _current_user()
        if user:
            args.extend(["--user", user])
        if input_text is not None:
            args.append("-i")
        args.append(self.image)
        args.extend(command)
        return args


def make_backend(
    name: str,
    *,
    image: Optional[str] = None,
    runtime: Optional[str] = None,
) -> SandboxBackend:
    normalized = (name or "local").strip().lower()
    if normalized == "local":
        return LocalTempBackend()
    if normalized == "container":
        return ContainerBackend(image=image or DEFAULT_CONTAINER_IMAGE, runtime=runtime)
    raise SandboxBackendError(f"unknown sandbox backend: {name!r} (use local or container)")


def _detect_runtime(preferred: Optional[str]) -> str:
    candidates = [preferred] if preferred else ["podman", "docker"]
    for candidate in candidates:
        if not candidate:
            continue
        path = shutil.which(candidate)
        if path:
            return path
    wanted = ", ".join(str(item) for item in candidates if item)
    raise SandboxBackendError(
        f"container backend requires a runtime ({wanted}); install rootless "
        "podman or docker, or pass --sandbox-runtime"
    )


def _current_user() -> Optional[str]:
    if hasattr(os, "getuid") and hasattr(os, "getgid"):
        return f"{os.getuid()}:{os.getgid()}"
    return None
