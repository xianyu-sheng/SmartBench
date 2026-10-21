"""Negative fixture: an early guard clause.

Resembles: dereferencing a possibly-None value (null_dereference).
Not a bug because: the function raises before the attribute access.
True positive if: the guard is removed or moved after the access.
"""


def describe(config):
    if config is None:
        raise ValueError("config is required")
    return config["name"]
