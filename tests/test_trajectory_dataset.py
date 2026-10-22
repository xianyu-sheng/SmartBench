"""Validate the committed v0 audit-trajectory dataset."""

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from smartbench.trajectory import validate_document

ROOT = Path(__file__).resolve().parent.parent
V0 = ROOT / "trajectories" / "v0"


def _documents():
    return [
        json.loads(line)
        for line in (V0 / "trajectories.jsonl").read_text(encoding="utf-8").splitlines()
    ]


def test_manifest_counts_and_hash():
    manifest = json.loads((V0 / "manifest.json").read_text(encoding="utf-8"))
    documents = _documents()

    assert manifest["schema_version"] == "smartbench.dataset/v1"
    assert manifest["license"] == "CC-BY-4.0"
    assert manifest["documents"] == len(documents) == 73
    assert manifest["splits"] == {"benchmark": 50, "negative": 14, "real_world": 9}
    assert Counter(doc["config"]["split"] for doc in documents) == manifest["splits"]
    canonical = (V0 / "trajectories.jsonl").read_bytes().replace(bytes([13, 10]), bytes([10]))
    assert hashlib.sha256(canonical).hexdigest() == manifest["sha256"]


def test_every_document_is_valid_and_redacted():
    identifiers = set()
    for document in _documents():
        assert validate_document(document) == []
        assert document["schema_version"] == "smartbench.trajectory/v1"
        identifiers.add(document["trajectory_id"])
        payload = json.dumps(document, ensure_ascii=False)
        assert not re.search(r"[A-Za-z]:\\\\", payload), "absolute Windows path leaked"
        assert "/home/" not in payload and "/Users/" not in payload
        assert not re.search(r"sk-[A-Za-z0-9_-]{16,}", payload)
    assert len(identifiers) == len(_documents())


def test_real_world_entries_carry_provenance():
    real = [doc for doc in _documents() if doc["config"]["split"] == "real_world"]
    assert len(real) == 9
    for document in real:
        assert document["verification"]
        assert document["adjudication"]
        provenance = document["provenance"]
        assert provenance["reference"].startswith("https://github.com/")
        assert provenance["outcome"]
        assert provenance["disclosure"]
        assert document["findings"]


def test_negative_split_is_finding_free():
    negatives = [doc for doc in _documents() if doc["config"]["split"] == "negative"]
    assert len(negatives) == 14
    assert all(not document["findings"] for document in negatives)


def test_license_and_dataset_card_exist():
    assert (ROOT / "trajectories" / "LICENSE").is_file()
    assert (V0 / "dataset_card.md").is_file()
    assert "CC BY 4.0" in (ROOT / "trajectories" / "LICENSE").read_text(encoding="utf-8")
