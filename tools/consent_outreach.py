#!/usr/bin/env python3
"""Ledger-based consent outreach (the GitHub bot entry point).

Reads pending cases from ``trajectories/v0/consent.json``, resolves a public
maintainer contact, and optionally sends one email per repository. Dry run by
default. On send it records ``requested_at`` and the Message-ID in the ledger
(never the email address), so the caller can open a PR with the record.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from smartbench.consent.contacts import resolve_contact  # noqa: E402
from smartbench.consent.email import SmtpConfig, render_request, send_message  # noqa: E402

LEDGER = ROOT / "trajectories" / "v0" / "consent.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def outreach(
    ledger_path: Path,
    *,
    send: bool = False,
    limit: int = 5,
    repository: str = "",
) -> List[Dict[str, Any]]:
    document = json.loads(ledger_path.read_text(encoding="utf-8"))
    cases = document.get("cases")
    if not isinstance(cases, list):
        raise SystemExit(f"invalid consent ledger: {ledger_path}")
    config = SmtpConfig.from_env()
    if send and not config.configured():
        raise SystemExit(
            "SMTP is not configured; set SMARTBENCH_SMTP_HOST and SMARTBENCH_SMTP_FROM"
        )

    results: List[Dict[str, Any]] = []
    changed = False
    selected = [
        case
        for case in cases
        if isinstance(case, dict)
        and case.get("status") == "pending"
        and not case.get("requested_at")
        and (not repository or str(case.get("repository", "")).lower() == repository.lower())
    ][: max(1, min(int(limit), 10))]

    for case in selected:
        name = str(case.get("repository", ""))
        contact = resolve_contact(name)
        if contact is None:
            results.append(
                {
                    "repository": name,
                    "status": "contact_missing",
                    "contact_source": None,
                    "message_id": None,
                }
            )
            if not case.get("note"):
                case["note"] = "No public contact found; nothing sent."
                changed = True
            continue
        result = {
            "repository": name,
            "status": "planned" if not send else "requested",
            "contact_source": contact["source"],
            "message_id": None,
        }
        if not send:
            results.append(result)
            continue
        rendered = render_request(name)
        subject = rendered.splitlines()[0].removeprefix("Subject: ").strip()
        body = "\n".join(rendered.splitlines()[1:]).lstrip("\n")
        message_id = send_message(
            config,
            to=contact["email"],
            subject=subject,
            body=body,
        )
        result["message_id"] = message_id
        results.append(result)
        case["requested_at"] = _now()
        case["note"] = f"Consent request sent by email (message-id {message_id})."
        changed = True

    if send and changed:
        ledger_path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", default=str(LEDGER))
    parser.add_argument("--send", action="store_true", help="Actually send emails")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--repository", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        results = outreach(
            Path(args.ledger),
            send=args.send,
            limit=args.limit,
            repository=args.repository,
        )
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for result in results:
            print(
                f"{result['repository']}: {result['status']} "
                f"({result.get('contact_source') or 'no public contact'})"
            )
        if not args.send:
            print("dry run: no email sent (use --send)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
