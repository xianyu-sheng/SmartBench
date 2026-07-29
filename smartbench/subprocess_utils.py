"""Bounded subprocess execution for local tools and repository metadata."""

import os
import signal
import subprocess
import tempfile
import threading
from typing import Mapping, Optional, Sequence

_DEFAULT_STDOUT_BYTES = 256 * 1024
_DEFAULT_STDERR_BYTES = 64 * 1024
_READ_CHUNK_BYTES = 64 * 1024


class _BoundedOutput:
    """Keep a bounded head and tail while continuously draining a pipe."""

    def __init__(self, limit: int):
        self.limit = max(1, int(limit))
        self.head_limit = max(1, self.limit * 3 // 4)
        self.tail_limit = max(0, self.limit - self.head_limit)
        self.head = bytearray()
        self.tail = bytearray()
        self.total = 0

    def consume(self, stream) -> None:
        try:
            while True:
                block = stream.read(_READ_CHUNK_BYTES)
                if not block:
                    break
                self.total += len(block)
                missing_head = self.head_limit - len(self.head)
                if missing_head > 0:
                    self.head.extend(block[:missing_head])
                    block = block[missing_head:]
                if block and self.tail_limit:
                    self.tail.extend(block)
                    if len(self.tail) > self.tail_limit:
                        del self.tail[:-self.tail_limit]
        except (OSError, ValueError):
            pass
        finally:
            stream.close()

    def text(self) -> str:
        if self.total <= self.limit:
            raw = bytes(self.head + self.tail)
        else:
            omitted = self.total - len(self.head) - len(self.tail)
            marker = f"\n... [{omitted} output bytes omitted] ...\n".encode()
            raw = bytes(self.head) + marker + bytes(self.tail)
        return raw.decode("utf-8", errors="replace")


def run_bounded(
    command: Sequence[str],
    *,
    cwd: Optional[str] = None,
    timeout: Optional[float] = None,
    env: Optional[Mapping[str, str]] = None,
    input_text: Optional[str] = None,
    max_stdout_bytes: int = _DEFAULT_STDOUT_BYTES,
    max_stderr_bytes: int = _DEFAULT_STDERR_BYTES,
) -> subprocess.CompletedProcess:
    """Run an argument vector while bounding captured stdout and stderr.

    Pipes are drained concurrently so noisy child processes cannot deadlock.
    On timeout, the child is killed and ``TimeoutExpired`` carries the bounded
    output collected before termination.
    """
    args = list(command)
    if not args or not all(isinstance(value, str) for value in args):
        raise ValueError("Command must be a non-empty string argument list")

    stdout_output = _BoundedOutput(max_stdout_bytes)
    stderr_output = _BoundedOutput(max_stderr_bytes)

    with tempfile.TemporaryFile() as stdin_file:
        if input_text is not None:
            stdin_file.write(input_text.encode("utf-8"))
            stdin_file.seek(0)

        process = subprocess.Popen(
            args,
            cwd=cwd,
            stdin=stdin_file if input_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            env=dict(env) if env is not None else None,
            start_new_session=os.name == "posix",
        )
        assert process.stdout is not None
        assert process.stderr is not None
        readers = [
            threading.Thread(
                target=stdout_output.consume,
                args=(process.stdout,),
                daemon=True,
            ),
            threading.Thread(
                target=stderr_output.consume,
                args=(process.stderr,),
                daemon=True,
            ),
        ]
        for reader in readers:
            reader.start()

        timed_out = False
        try:
            return_code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    process.kill()
            else:
                process.kill()
            return_code = process.wait()
        finally:
            for reader in readers:
                reader.join(timeout=2)
            for stream in (process.stdout, process.stderr):
                if not stream.closed:
                    stream.close()
            for reader in readers:
                reader.join(timeout=2)

    stdout = stdout_output.text()
    stderr = stderr_output.text()
    if timed_out:
        raise subprocess.TimeoutExpired(
            args,
            timeout,
            output=stdout,
            stderr=stderr,
        )
    return subprocess.CompletedProcess(args, return_code, stdout, stderr)
