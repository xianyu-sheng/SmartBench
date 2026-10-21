"""Negative fixture: the guard lives in a helper.

Resembles: dereferencing a maybe-None value (null_dereference).
Not a bug because: require_config raises on None and returns the value otherwise.
True positive if: the helper returns None on failure instead of raising.
"""


def require_config(config):
    if config is None:
        raise ValueError("config is required")
    return config


def describe(config):
    config = require_config(config)
    return config["name"]
