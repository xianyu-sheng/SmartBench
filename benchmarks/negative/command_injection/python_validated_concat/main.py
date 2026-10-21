"""Negative fixture: input validated to an integer before use.

Resembles: string concatenation into a shell command (command_injection).
Not a bug because: int() rejects everything that is not an integer.
True positive if: the validated value is dropped and the raw string is used.
"""

import os


def show_revision(revision: str) -> None:
    number = int(revision)
    os.system("git log -1 --format=%h " + str(number))
