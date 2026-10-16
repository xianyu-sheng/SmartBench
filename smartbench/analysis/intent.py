"""Deterministic design-intent extraction from repository text.

An engineering trade-off cannot be adjudicated from code shape alone: the same
error retry that looks like a busy-loop hazard can be a deliberate fail-stop
design, documented in a comment, a README or a design note.  This module scans
the repository's own text — source comments and documentation — for explicit
intent statements and exposes them as *context* facts.

Intent facts never prove or disprove a defect.  They are inputs to the
adjudication layer, which uses them to label a finding as ``intentional``,
``design_trade_off`` or ``out_of_scope`` instead of ``actionable``.  Missing
intent always yields ``unknown``, never a fabricated intent.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterable

from smartbench.ir import EvidenceRef
from smartbench.path_safety import read_text_bounded

_SKIPPED_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "node_modules",
        "vendor",
        "dist",
        "build",
        "target",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
    }
)

_DOCUMENT_SUFFIXES = frozenset({".md", ".rst", ".txt", ".adoc"})
_TEST_SUFFIXES = ("_test.go", "_test.py", "_test.js", "_test.ts")

# Lines that are comments or documentation prose.  The scanner still checks the
# prose of documentation files without requiring a comment marker.
_COMMENT_LINE = re.compile(r"^\s*(#|//|/\*|\*|<!--|--|;|!)\s?")


class IntentKind(str, Enum):
    """Bounded set of documented design intents."""

    FAIL_STOP = "fail_stop"
    INTENTIONAL = "intentional"
    TRADE_OFF = "trade_off"
    OUT_OF_SCOPE = "out_of_scope"
    BOUNDED_RETRY = "bounded_retry"


_INTENT_PATTERNS: tuple[tuple[IntentKind, re.Pattern[str]], ...] = (
    (
        IntentKind.FAIL_STOP,
        re.compile(
            r"(?i)\b(fail[- ]?stop|fail[- ]?fast|"
            r"crash(?:ing)? (?:is|are) (?:acceptable|preferred|preferable)|"
            r"prefer(?:s|red)? (?:to )?(?:crash|exit|panic)|"
            r"must (?:exit|crash|panic|abort)|"
            r"should (?:exit|crash|abort) rather than)\b"
        ),
    ),
    (
        IntentKind.INTENTIONAL,
        re.compile(
            r"(?i)\b(by design|intentional(?:ly)?|on purpose|deliberately|"
            r"not a bug|won'?t ?fix|we intentionally)\b"
        ),
    ),
    (
        IntentKind.TRADE_OFF,
        re.compile(
            r"(?i)\b(trade[- ]?offs?|known limitation|acceptable|for now|"
            r"we accept)\b"
        ),
    ),
    (
        IntentKind.OUT_OF_SCOPE,
        re.compile(
            r"(?i)\b(out of scope|not supported|unsupported|won'?t support|"
            r"no plans? to)\b"
        ),
    ),
    (
        IntentKind.BOUNDED_RETRY,
        re.compile(
            r"(?i)\b(back[- ]?off|retry limit|max(?:imum)? (?:retries|attempts)|"
            r"bounded|exponential backoff)\b"
        ),
    ),
)


@dataclass(frozen=True)
class IntentFact:
    """One documented intent statement with source provenance."""

    kind: IntentKind
    statement: str
    location: EvidenceRef

    @property
    def fact_id(self) -> str:
        material = f"{self.location.file_path}:{self.location.line_start}:{self.kind.value}"
        return "intent-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.fact_id,
            "kind": self.kind.value,
            "statement": self.statement,
            "location": self.location.to_dict(),
        }


@dataclass(frozen=True)
class IntentPack:
    """Bounded collection of intent facts for one project."""

    facts: tuple[IntentFact, ...] = field(default_factory=tuple)
    files_scanned: int = 0

    def for_file(self, file_path: str) -> tuple[IntentFact, ...]:
        normalized = file_path.replace("\\", "/").lstrip("./")
        return tuple(
            fact
            for fact in self.facts
            if fact.location.file_path.replace("\\", "/").lstrip("./") == normalized
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "files_scanned": self.files_scanned,
            "facts": [fact.to_dict() for fact in self.facts],
        }


class IntentScanner:
    """Extract documented design intent without importing a language parser."""

    def __init__(
        self,
        *,
        max_files: int = 400,
        max_file_bytes: int = 256 * 1024,
        max_facts: int = 500,
    ) -> None:
        self.max_files = max(1, int(max_files))
        self.max_file_bytes = max(1, int(max_file_bytes))
        self.max_facts = max(1, int(max_facts))

    def scan(self, project_path: Path) -> IntentPack:
        root = Path(project_path).resolve()
        if not root.is_dir():
            return IntentPack()
        facts: list[IntentFact] = []
        files_scanned = 0
        for path in sorted(root.rglob("*")):
            if len(facts) >= self.max_facts or files_scanned >= self.max_files:
                break
            if not path.is_file():
                continue
            parts = set(path.parts)
            if parts & _SKIPPED_DIRECTORIES:
                continue
            is_document = path.suffix.lower() in _DOCUMENT_SUFFIXES
            if not is_document and path.name.endswith(_TEST_SUFFIXES):
                continue
            if not is_document and path.suffix == "":
                continue
            source = read_text_bounded(path, self.max_file_bytes)
            if source is None:
                continue
            files_scanned += 1
            relative = self._relative(root, path)
            for fact in self._scan_text(source, relative, is_document):
                facts.append(fact)
                if len(facts) >= self.max_facts:
                    break
        return IntentPack(facts=tuple(facts), files_scanned=files_scanned)

    @staticmethod
    def _relative(root: Path, path: Path) -> str:
        try:
            return str(path.relative_to(root)).replace("\\", "/")
        except ValueError:
            return str(path).replace("\\", "/")

    def _scan_text(
        self,
        source: str,
        relative: str,
        is_document: bool,
    ) -> Iterable[IntentFact]:
        for line_number, line in enumerate(source.splitlines(), start=1):
            if not line.strip():
                continue
            if not is_document and not _COMMENT_LINE.match(line):
                continue
            for kind, pattern in _INTENT_PATTERNS:
                if not pattern.search(line):
                    continue
                yield IntentFact(
                    kind=kind,
                    statement=line.strip()[:300],
                    location=EvidenceRef(
                        file_path=relative,
                        line_start=line_number,
                        snippet=line.strip()[:300],
                        source="deterministic-intent",
                    ),
                )
