"""Intent pack and adjudication tests (Phase 2)."""

from pathlib import Path

import pytest

from smartbench.analysis.adjudication import (
    Adjudication,
    Adjudicator,
    MemoryEntry,
    MemoryStore,
    apply_adjudications,
)
from smartbench.analysis.intent import IntentKind, IntentScanner
from smartbench.core import (
    AdapterRegistry,
    RuleRegistry,
    UnifiedDiagnosticConfig,
    UnifiedDiagnosticEngine,
)
from smartbench.core.adapters import GoAdapter
from smartbench.core.rules.base import Finding, Location, Severity
from smartbench.core.rules.retry import RetryWithoutBackoffRule
from smartbench.graph.tree_parser import get_parser

requires_go = pytest.mark.skipif(
    get_parser("go") is None, reason="tree-sitter Go unavailable"
)

FAIL_STOP_COMMENT = "// This path is fail-stop on purpose: prefer to crash rather than retry.\n"
TRADE_OFF_DOC = "# Trade-offs\n\nWe accept the extra retry cost for now.\n"

BUSY_LOOP = """
package sample

import (
	"log"
	"net"
)

func listen(l net.Listener) {
	for {
		c, err := l.Accept()
		if err != nil {
			log.Printf("accepting connection: %s", err)
			continue
		}
		go handleConn(c)
	}
}

func handleConn(c net.Conn) {}
"""


def _finding(**metadata) -> Finding:
    return Finding(
        rule_id=str(metadata.pop("rule_id", "retry_without_backoff")),
        rule_name="Retry without backoff",
        severity=Severity.WARNING,
        location=Location(
            file_path=str(metadata.pop("file_path", "server.go")),
            line_start=int(metadata.pop("line", 10)),
        ),
        message="retry loop",
        metadata=dict(metadata),
    )


def test_intent_scanner_finds_markers_in_comments_and_docs(tmp_path: Path):
    (tmp_path / "server.go").write_text(
        "package sample\n\n" + FAIL_STOP_COMMENT + "func serve() {}\n",
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text(TRADE_OFF_DOC, encoding="utf-8")

    pack = IntentScanner().scan(tmp_path)
    kinds = {fact.kind for fact in pack.facts}

    assert IntentKind.FAIL_STOP in kinds
    assert IntentKind.TRADE_OFF in kinds
    assert pack.for_file("server.go")
    assert all(fact.location.line_start >= 1 for fact in pack.facts)


def test_intent_scanner_skips_tests_and_vendor(tmp_path: Path):
    (tmp_path / "server_test.go").write_text(
        "package sample\n\n" + FAIL_STOP_COMMENT, encoding="utf-8"
    )
    vendor = tmp_path / "vendor" / "dep"
    vendor.mkdir(parents=True)
    (vendor / "dep.go").write_text(
        "package dep\n\n" + FAIL_STOP_COMMENT, encoding="utf-8"
    )

    pack = IntentScanner().scan(tmp_path)

    assert pack.facts == ()


def test_adjudicator_orders_memory_then_intent_then_patch_risk(tmp_path: Path):
    (tmp_path / "server.go").write_text(
        "package sample\n\n" + FAIL_STOP_COMMENT + "func serve() {}\n",
        encoding="utf-8",
    )
    pack = IntentScanner().scan(tmp_path)
    finding = _finding(patch_risk="regression_under_persistent_error")

    memory = MemoryStore.from_document(
        {
            "version": "smartbench.intent/v1",
            "entries": [
                {
                    "path": "server.go",
                    "label": "design_trade_off",
                    "reason": "maintainers accepted fail-stop here",
                }
            ],
        }
    )
    decisions = Adjudicator().adjudicate([finding], pack, memory)
    assert decisions[0].label is Adjudication.DESIGN_TRADE_OFF
    assert "user intent memory" in decisions[0].reasons[0]

    decisions = Adjudicator().adjudicate([finding], pack)
    assert decisions[0].label is Adjudication.INTENTIONAL
    assert decisions[0].basis, "intent label must cite the intent fact"

    decisions = Adjudicator().adjudicate([finding], None)
    assert decisions[0].label is Adjudication.DESIGN_TRADE_OFF


def test_adjudicator_uses_reachability_and_defaults_to_actionable():
    theoretical = Adjudicator().adjudicate([_finding(reachability="low")])
    assert theoretical[0].label is Adjudication.THEORETICAL

    actionable = Adjudicator().adjudicate([_finding()])
    assert actionable[0].label is Adjudication.ACTIONABLE
    assert actionable[0].reasons == ()


def test_memory_store_rejects_invalid_schema():
    assert MemoryStore.from_document({"version": "nope", "entries": []}).entries == ()
    assert MemoryStore.from_document({"entries": [{"label": "intentional"}]}).entries == ()
    store = MemoryStore.from_document(
        {
            "version": "smartbench.intent/v1",
            "entries": [
                {"label": "not-a-label"},
                {"label": "out_of_scope", "path": "docs/*.go", "rule_id": "x"},
            ],
        }
    )
    assert [entry.label for entry in store.entries] == [Adjudication.OUT_OF_SCOPE]


def test_apply_adjudications_writes_metadata(tmp_path: Path, monkeypatch):
    (tmp_path / "server.go").write_text(
        "package sample\n\n" + FAIL_STOP_COMMENT + "func serve() {}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SMARTBENCH_INTENT_FILE", str(tmp_path / "missing.yaml"))
    finding = _finding(patch_risk="regression_under_persistent_error")

    decisions = apply_adjudications(tmp_path, [finding])

    assert decisions[0].label is Adjudication.INTENTIONAL
    assert finding.metadata["adjudication"] == "intentional"
    assert finding.metadata["adjudication_basis"]


def test_memory_file_env_override(tmp_path: Path, monkeypatch):
    memory_file = tmp_path / "intent.yaml"
    memory_file.write_text(
        "version: smartbench.intent/v1\n"
        "entries:\n"
        "  - path: 'server.go'\n"
        "    label: out_of_scope\n"
        "    reason: 'not supported'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SMARTBENCH_INTENT_FILE", str(memory_file))

    decisions = apply_adjudications(tmp_path, [_finding()])

    assert decisions[0].label is Adjudication.OUT_OF_SCOPE


@requires_go
def test_engine_attaches_adjudication_to_retry_finding(tmp_path: Path):
    (tmp_path / "main.go").write_text(BUSY_LOOP, encoding="utf-8")
    adapters = AdapterRegistry()
    adapters.register(GoAdapter())
    rules = RuleRegistry()
    rules.register(RetryWithoutBackoffRule())
    engine = UnifiedDiagnosticEngine(adapters, rules)

    result = engine.diagnose(
        tmp_path,
        UnifiedDiagnosticConfig(
            rule_ids=["retry_without_backoff"],
            min_confidence=0.0,
            build_evidence_packs=False,
        ),
    )

    assert result.errors == []
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.metadata["adjudication"] == "design_trade_off"
    assert finding.metadata["patch_risk"] == "regression_under_persistent_error"
    assert result.stats["adjudication_design_trade_off"] == 1


@requires_go
def test_engine_marks_documented_fail_stop_as_intentional(tmp_path: Path, monkeypatch):
    (tmp_path / "main.go").write_text(FAIL_STOP_COMMENT + BUSY_LOOP, encoding="utf-8")
    adapters = AdapterRegistry()
    adapters.register(GoAdapter())
    rules = RuleRegistry()
    rules.register(RetryWithoutBackoffRule())
    engine = UnifiedDiagnosticEngine(adapters, rules)

    result = engine.diagnose(
        tmp_path,
        UnifiedDiagnosticConfig(
            rule_ids=["retry_without_backoff"],
            min_confidence=0.0,
            build_evidence_packs=False,
        ),
    )

    assert len(result.findings) == 1
    assert result.findings[0].metadata["adjudication"] == "intentional"


def test_memory_store_roundtrip_and_deduplication(tmp_path: Path):
    target = tmp_path / "intent.yaml"
    store = MemoryStore.empty()
    entry = MemoryEntry(
        label=Adjudication.DESIGN_TRADE_OFF,
        reason="maintainers accepted fail-stop",
        source="https://example.invalid/1",
        path="server.go",
        rule_id="retry_without_backoff",
    )

    store = store.with_entry(entry)
    store = store.with_entry(entry)
    saved = store.save(target)

    assert saved == target
    loaded = MemoryStore.load(target)
    assert len(loaded.entries) == 1
    assert loaded.entries[0].label is Adjudication.DESIGN_TRADE_OFF
    assert loaded.entries[0].source == "https://example.invalid/1"


def test_validate_document_reports_schema_problems():
    from smartbench.analysis.adjudication import validate_document

    assert validate_document({"version": "smartbench.intent/v1", "entries": []}) == []
    errors = validate_document(
        {
            "version": "nope",
            "entries": [{"label": "bad"}, {"label": "intentional", "extra": 1}],
        }
    )
    assert any("version" in error for error in errors)
    assert any("label" in error for error in errors)
    assert any("unknown fields" in error for error in errors)
    assert validate_document(None) == ["document must be a mapping"]


def test_adjudicate_suggestions_uses_intent_and_memory(tmp_path: Path):
    from smartbench.analysis.adjudication import adjudicate_suggestions

    (tmp_path / "server.go").write_text(
        "package sample\n\n" + FAIL_STOP_COMMENT + "func serve() {}\n",
        encoding="utf-8",
    )
    intent = IntentScanner().scan(tmp_path)
    suggestions = [
        {"title": "patch accept loop", "location": "server.go:12"},
        {"title": "unrelated", "location": "other.go:3"},
    ]

    memory = MemoryStore.from_document(
        {
            "version": "smartbench.intent/v1",
            "entries": [
                {"path": "other.go", "label": "out_of_scope", "reason": "n/a"}
            ],
        }
    )
    decisions = adjudicate_suggestions(tmp_path, suggestions, memory=memory, intent_pack=intent)

    assert [decision.label for decision in decisions] == [
        Adjudication.INTENTIONAL,
        Adjudication.OUT_OF_SCOPE,
    ]
    assert suggestions[0]["adjudication"] == "intentional"
    assert suggestions[0]["adjudication_basis"]
    assert suggestions[1]["adjudication"] == "out_of_scope"


def test_intent_cli_add_list_and_validate(tmp_path: Path):
    import json

    from typer.testing import CliRunner

    from smartbench.cli.main import app

    target = tmp_path / "intent.yaml"
    runner = CliRunner()

    added = runner.invoke(
        app,
        [
            "intent",
            "add",
            "--label",
            "design_trade_off",
            "--path",
            "server.go",
            "--rule-id",
            "retry_without_backoff",
            "--reason",
            "maintainers kept fail-stop",
            "--source",
            "https://example.invalid/2",
            "--file",
            str(target),
        ],
    )
    assert added.exit_code == 0, added.output

    listed = runner.invoke(app, ["intent", "list", "--file", str(target)])
    assert listed.exit_code == 0
    assert "server.go" in listed.output
    assert "retry_withou" in listed.output

    validated = runner.invoke(app, ["intent", "validate", "--file", str(target)])
    assert validated.exit_code == 0
    assert "OK" in validated.output

    target.write_text("version: nope\nentries: []\n", encoding="utf-8")
    invalid = runner.invoke(app, ["intent", "validate", "--file", str(target)])
    assert invalid.exit_code == 1
    assert "version" in invalid.output
    assert json.dumps(listed.output)


def test_check_upstream_record_writes_memory(tmp_path: Path, monkeypatch):
    import json

    from typer.testing import CliRunner

    from smartbench.cli.main import app
    from smartbench.frontends.upstream_checker import (
        GitHubUpstreamChecker,
        UpstreamCheckResult,
        UpstreamMatch,
    )

    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {
                "findings": [
                    {
                        "rule_id": "retry_without_backoff",
                        "location": {"file_path": "server.go", "line_start": 10},
                        "metadata": {},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    memory = tmp_path / "intent.yaml"
    monkeypatch.setenv("SMARTBENCH_INTENT_FILE", str(memory))

    def fake_check(self, findings):
        return [
            UpstreamCheckResult(
                rule_id="retry_without_backoff",
                file_path="server.go",
                line_start=10,
                query="repo:owner/repo Accept",
                status="discussed",
                matches=[
                    UpstreamMatch(
                        url="https://example.invalid/3",
                        title="retry loop is by design",
                        state="closed",
                        is_pull_request=True,
                        intent_hint="by design",
                    )
                ],
            )
        ]

    monkeypatch.setattr(GitHubUpstreamChecker, "check", fake_check)
    runner = CliRunner()

    first = runner.invoke(
        app,
        [
            "check-upstream",
            "--input",
            str(report),
            "--repo",
            "owner/repo",
            "--record",
        ],
    )
    assert first.exit_code == 0, first.output
    loaded = MemoryStore.load(memory)
    assert len(loaded.entries) == 1
    assert loaded.entries[0].label is Adjudication.INTENTIONAL
    assert loaded.entries[0].path == "server.go"

    second = runner.invoke(
        app,
        [
            "check-upstream",
            "--input",
            str(report),
            "--repo",
            "owner/repo",
            "--record",
        ],
    )
    assert second.exit_code == 0
    assert len(MemoryStore.load(memory).entries) == 1
