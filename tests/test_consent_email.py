"""Tests for consent email rendering and sending (no network)."""

import json

from smartbench.consent.contacts import resolve_contact
from smartbench.consent.email import SmtpConfig, render_request, send_pending
from smartbench.consent.queue import load_queue, stage


def test_render_request_is_explicit_and_has_no_findings():
    text = render_request("owner/repo")

    assert "owner/repo" in text
    assert "I, <name>, consent" in text
    assert "CC BY 4.0" in text
    assert "CONSENT.md" in text
    assert "rule/analysis identifiers" in text
    assert "delete our local" in text


def test_send_pending_dry_run_does_not_change_queue(tmp_path, monkeypatch):
    queue = tmp_path / "outreach.json"
    stage("owner/repo", "trajectory-a", queue_path=queue)
    monkeypatch.setattr(
        "smartbench.consent.email.resolve_contact",
        lambda repository, token=None: {
            "email": "maintainer@acme.dev",
            "source": "https://github.com/owner",
        },
    )

    results = send_pending(queue_path=queue, dry_run=True)

    assert results[0]["status"] == "planned"
    assert load_queue(queue)["requests"][0]["status"] == "staged"


def test_send_pending_marks_requested(tmp_path, monkeypatch):
    queue = tmp_path / "outreach.json"
    stage("owner/repo", "trajectory-a", queue_path=queue)
    monkeypatch.setattr(
        "smartbench.consent.email.resolve_contact",
        lambda repository, token=None: {
            "email": "maintainer@acme.dev",
            "source": "https://github.com/owner",
        },
    )
    monkeypatch.setattr(
        "smartbench.consent.email.send_message",
        lambda config, *, to, subject, body: "<consent@smartbench.local>",
    )

    results = send_pending(
        queue_path=queue,
        dry_run=False,
        smtp_config=SmtpConfig(host="smtp.example.org", port=25, sender="audit@example.org"),
    )

    assert results[0]["status"] == "requested"
    entry = load_queue(queue)["requests"][0]
    assert entry["status"] == "requested"
    assert entry["requested_at"]
    assert entry["message_id"] == "<consent@smartbench.local>"


def test_send_pending_marks_contact_missing(tmp_path, monkeypatch):
    queue = tmp_path / "outreach.json"
    stage("owner/repo", "trajectory-a", queue_path=queue)
    monkeypatch.setattr(
        "smartbench.consent.email.resolve_contact",
        lambda repository, token=None: None,
    )

    results = send_pending(
        queue_path=queue,
        dry_run=False,
        smtp_config=SmtpConfig(host="smtp.example.org", port=25, sender="audit@example.org"),
    )

    assert results[0]["status"] == "contact_missing"
    assert load_queue(queue)["requests"][0]["status"] == "contact_missing"


def test_manual_contact_mapping_is_local_only(tmp_path, monkeypatch):
    mapping = tmp_path / "contacts.json"
    mapping.write_text(
        json.dumps({"owner/repo": "maintainer@acme.dev"}), encoding="utf-8"
    )
    monkeypatch.setenv("SMARTBENCH_CONSENT_CONTACTS", str(mapping))

    contact = resolve_contact("owner/repo")

    assert contact is not None
    assert contact["email"] == "maintainer@acme.dev"
    assert contact["source"] == "local manual contact mapping"
