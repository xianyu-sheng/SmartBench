"""Negative fixture: a context manager owns the cleanup.

Resembles: open() without a close() (resource_leak).
Not a bug because: the `with` block closes the handle on every exit path.
True positive if: the file object escapes the `with` block and is used later.
"""


def read_config(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()
