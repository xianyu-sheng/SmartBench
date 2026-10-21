"""Negative fixture: list arguments, no shell.

Resembles: subprocess.run() (command_injection).
Not a bug because: arguments are passed as a list; no shell parses them.
True positive if: shell=True is added and user input is concatenated.
"""

import subprocess


def list_dir(path: str) -> None:
    subprocess.run(["ls", "-la", path], check=True)
