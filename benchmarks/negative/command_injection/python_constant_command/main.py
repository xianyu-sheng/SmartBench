"""Negative fixture: a constant command with no interpolation.

Resembles: os.system() (command_injection).
Not a bug because: the command string is a literal; no external input reaches it.
True positive if: a user-controlled value is interpolated into the command.
"""

import os


def show_status() -> None:
    os.system("git status --short")
