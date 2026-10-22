"""Resolve a public maintainer contact without harvesting private addresses.

Only two sources are used:

1. the public GitHub profile email;
2. an email explicitly written in ``SECURITY.md`` or ``CONTRIBUTING.md``.

Commit-author emails are deliberately ignored. If no public contact exists the
request is marked ``contact_missing`` and nothing is sent.
"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

API = "https://api.github.com"
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
CONTACT_FILES = (
    ".github/SECURITY.md",
    "SECURITY.md",
    ".github/CONTRIBUTING.md",
    "CONTRIBUTING.md",
)


def _get_json(url: str, token: Optional[str], timeout: int = 15) -> Optional[Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "smartbench-consent/1.0",
        },
    )
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, OSError):
        return None


def _usable(email: str) -> bool:
    lowered = email.strip().lower()
    if not lowered or EMAIL_RE.fullmatch(lowered) is None:
        return False
    return not any(
        marker in lowered
        for marker in (
            "noreply",
            "no-reply",
            "users.noreply.github.com",
            "example.com",
            "example.org",
            ".invalid",
        )
    )


def _file_text(repository: str, path: str, token: Optional[str]) -> str:
    quoted = urllib.parse.quote(path)
    payload = _get_json(
        f"{API}/repos/{repository}/contents/{quoted}", token
    )
    if not isinstance(payload, dict) or payload.get("encoding") != "base64":
        return ""
    try:
        return base64.b64decode(payload.get("content", "")).decode("utf-8", "replace")
    except (ValueError, TypeError):
        return ""


def _manual_contact(repository: str) -> Optional[Dict[str, str]]:
    """Read a local, uncommitted repo -> email mapping if configured."""
    path = os.environ.get("SMARTBENCH_CONSENT_CONTACTS", "").strip()
    if not path:
        return None
    try:
        mapping = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(mapping, dict):
        return None
    email = str(
        mapping.get(repository) or mapping.get(repository.lower()) or ""
    ).strip()
    if _usable(email):
        return {"email": email, "source": "local manual contact mapping"}
    return None


def resolve_contact(
    repository: str,
    *,
    token: Optional[str] = None,
) -> Optional[Dict[str, str]]:
    """Return ``{email, source}`` for a public contact, or ``None``."""
    manual = _manual_contact(repository)
    if manual:
        return manual
    token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    owner = repository.split("/", 1)[0]
    user = _get_json(f"{API}/users/{urllib.parse.quote(owner)}", token)
    if isinstance(user, dict):
        email = str(user.get("email") or "").strip()
        if _usable(email):
            return {"email": email, "source": f"https://github.com/{owner}"}

    for path in CONTACT_FILES:
        text = _file_text(repository, path, token)
        if not text:
            continue
        for match in EMAIL_RE.findall(text):
            if _usable(match):
                return {
                    "email": match,
                    "source": f"https://github.com/{repository}/blob/HEAD/{path}",
                }
    return None
