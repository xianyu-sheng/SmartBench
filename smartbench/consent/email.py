"""Send consent requests by email.

Sending is explicit: ``send_pending`` defaults to a dry run, and real delivery
requires SMTP configuration from the environment. One email per repository;
no findings or code are included in the message.
"""

from __future__ import annotations

import os
import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path
from typing import Any, Dict, List, Optional

from smartbench.consent.contacts import resolve_contact
from smartbench.consent.queue import load_queue, update_request

POLICY_URL = "https://github.com/xianyu-sheng/SmartBench/blob/main/trajectories/CONSENT.md"
LEADERBOARD_URL = "https://github.com/xianyu-sheng/SmartBench/blob/main/trajectories/CONTRIBUTORS.md"
MAX_PER_RUN = 10


@dataclass
class SmtpConfig:
    host: str
    port: int
    sender: str
    user: str = ""
    password: str = ""
    use_tls: bool = True
    use_ssl: bool = False

    @classmethod
    def from_env(cls) -> "SmtpConfig":
        host = os.environ.get("SMARTBENCH_SMTP_HOST", "").strip()
        sender = os.environ.get("SMARTBENCH_SMTP_FROM", "").strip()
        try:
            port = int(os.environ.get("SMARTBENCH_SMTP_PORT", "587") or "587")
        except ValueError:
            port = 587
        use_ssl = os.environ.get("SMARTBENCH_SMTP_SSL", "").lower() in {"1", "true", "yes"}
        use_tls = os.environ.get("SMARTBENCH_SMTP_TLS", "1").lower() not in {
            "0",
            "false",
            "no",
        }
        return cls(
            host=host,
            port=port,
            sender=sender,
            user=os.environ.get("SMARTBENCH_SMTP_USER", "").strip(),
            password=os.environ.get("SMARTBENCH_SMTP_PASSWORD", ""),
            use_tls=use_tls and not use_ssl,
            use_ssl=use_ssl,
        )

    def configured(self) -> bool:
        return bool(self.host and self.sender)


def render_request(
    repository: str,
    *,
    policy_url: str = POLICY_URL,
    leaderboard_url: str = LEADERBOARD_URL,
) -> str:
    return f"""Subject: Permission to publish a redacted SmartBench audit trajectory for {repository}

Hi,

I maintain SmartBench, an open-source code-diagnosis tool. We ran a local audit
of {repository} and saved a redacted audit trajectory. Nothing is published yet.

Before publishing, we ask the repository owner for explicit written permission.
If granted, the trajectory would contain only:

- rule/analysis identifiers and project-relative file paths/line numbers;
- the finding summary and verification result;
- the public issue/PR link, if any.

It would not contain raw prompts/responses, secrets, absolute paths, or private
data. If this repository has a security policy, we will follow it before
discussing any details.

If you consent, please reply with:

"I, <name>, consent to SmartBench publishing a redacted audit trajectory for
{repository} under CC BY 4.0."

We will record the link as evidence and list the repository on the
data-contributor leaderboard (opt-in; tell us if you prefer a different name or
no listing). If you decline, we will not publish it and will delete our local
copy. You can ask for removal at any time.

Policy: {policy_url}
Leaderboard: {leaderboard_url}
"""


def send_message(
    config: SmtpConfig,
    *,
    to: str,
    subject: str,
    body: str,
) -> str:
    """Send one plain-text email and return its Message-ID."""
    message_id = make_msgid(domain="smartbench.local")
    message = EmailMessage()
    message["From"] = formataddr(("SmartBench Audit", config.sender))
    message["To"] = to
    message["Subject"] = subject
    message["Message-ID"] = message_id
    message["X-SmartBench-Consent"] = "request"
    message.set_content(body)

    if config.use_ssl:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(config.host, config.port, context=context, timeout=30) as smtp:
            if config.user:
                smtp.login(config.user, config.password)
            smtp.send_message(message)
    else:
        with smtplib.SMTP(config.host, config.port, timeout=30) as smtp:
            if config.use_tls:
                smtp.starttls(context=ssl.create_default_context())
            if config.user:
                smtp.login(config.user, config.password)
            smtp.send_message(message)
    return message_id


def send_pending(
    *,
    queue_path: str | Path | None = None,
    dry_run: bool = True,
    limit: int = 5,
    repository: Optional[str] = None,
    token: Optional[str] = None,
    smtp_config: Optional[SmtpConfig] = None,
) -> List[Dict[str, Any]]:
    """Resolve contacts and send at most ``limit`` staged requests.

    Dry-run returns the plan and writes nothing. Real delivery marks each queue
    entry ``requested`` with a timestamp and Message-ID.
    """
    queue = load_queue(queue_path)
    requests = queue.get("requests", [])
    selected: List[Dict[str, Any]] = []
    for entry in requests:
        if str(entry.get("status")) != "staged":
            continue
        if entry.get("requested_at"):
            continue
        if repository and str(entry.get("repository", "")).lower() != repository.lower():
            continue
        selected.append(entry)
        if len(selected) >= max(1, min(int(limit), MAX_PER_RUN)):
            break

    results: List[Dict[str, Any]] = []
    config = smtp_config or SmtpConfig.from_env()
    if not dry_run and not config.configured():
        raise RuntimeError(
            "SMTP is not configured; set SMARTBENCH_SMTP_HOST and SMARTBENCH_SMTP_FROM"
        )

    for entry in selected:
        repository_name = str(entry.get("repository", ""))
        contact = resolve_contact(repository_name, token=token)
        if contact is None:
            results.append(
                {
                    "repository": repository_name,
                    "status": "contact_missing",
                    "contact_source": None,
                    "message_id": None,
                }
            )
            if not dry_run:
                update_request(
                    repository_name,
                    queue_path=queue_path,
                    status="contact_missing",
                    note="No public contact found; nothing sent.",
                )
            continue

        result = {
            "repository": repository_name,
            "status": "planned" if dry_run else "requested",
            "contact_source": contact["source"],
            "message_id": None,
        }
        if dry_run:
            results.append(result)
            continue

        rendered = render_request(repository_name)
        subject = rendered.splitlines()[0].removeprefix("Subject: ").strip()
        body = "\n".join(rendered.splitlines()[1:]).lstrip("\n")
        try:
            message_id = send_message(
                config,
                to=contact["email"],
                subject=subject,
                body=body,
            )
        except (OSError, smtplib.SMTPException) as exc:
            result["status"] = "error"
            result["error"] = str(exc)
            results.append(result)
            update_request(
                repository_name,
                queue_path=queue_path,
                status="error",
                note=f"Send failed: {exc}",
            )
            continue
        result["message_id"] = message_id
        results.append(result)
        update_request(
            repository_name,
            queue_path=queue_path,
            status="requested",
            requested_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            contact_source=contact["source"],
            message_id=message_id,
            note="Consent request sent by email; awaiting reply.",
        )
    return results
