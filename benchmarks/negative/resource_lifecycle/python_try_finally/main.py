"""Negative fixture: try/finally closes the handle.

Resembles: open() without a `with` block (resource_leak).
Not a bug because: the `finally` block closes the handle on every exit path.
True positive if: the `finally` block is removed, or close() moves to the
success path only and an early return can skip it.
"""


def read_config(path: str) -> str:
    handle = open(path, "r", encoding="utf-8")
    try:
        return handle.read()
    finally:
        handle.close()
