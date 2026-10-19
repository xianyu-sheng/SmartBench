"""Single-file HTML report with evidence source anchors (issue #22).

Design contract
---------------
* Every finding location and every evidence reference resolves to an
  in-document anchor (``#evidence-<digest>``). The anchor target embeds the
  referenced source lines, so clicking a reference lands on the exact
  ``file:line`` even when the report is moved or opened offline.
* A report is one self-contained HTML file: inline CSS only, no
  ``<script>``, no ``<link>``, no remote fonts or images.
* Locations that cannot be resolved inside the analysed project (missing
  path, missing line number, absolute path or ``..`` escape) degrade to
  plain text with a ``data-reason`` marker - never to a broken link.
* Output is deterministic for a fixed ``generated_at`` so the committed
  sample report (``docs/sample_report.html``) can be asserted in CI.
"""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from smartbench.path_safety import read_text_bounded, resolve_project_file

__all__ = [
    "HTMLReportOptions",
    "SourceLocation",
    "render_html_report",
    "write_html_report",
]

SNIPPET_MAX_LINES = 24
SNIPPET_MAX_LINE_CHARS = 400
SNIPPET_MAX_BYTES = 1_000_000

_SEVERITIES = ("error", "warning", "info")
_SEVERITY_RANK = {"error": 0, "warning": 1, "info": 2}

# ``src/app.py:12``, ``src/app.py:12-20``, ``src/app.py#L12-L20``
_LINE_SUFFIX = re.compile(r"[#:]\s*L?(?P<start>\d+)(?:\s*[-:,]\s*L?(?P<end>\d+))?$")
_ABSOLUTE_PREFIX = re.compile(r"^[A-Za-z]:/|^//")
_FACT_SLUG = re.compile(r"[^A-Za-z0-9_-]+")

_CSS = """
:root {
  color-scheme: light dark;
  --fg: #1f2328;
  --muted: #59636e;
  --border: #d1d9e0;
  --surface: #f6f8fa;
  --error: #cf222e;
  --warning: #9a6700;
  --info: #0969da;
  --link: #0969da;
}
@media (prefers-color-scheme: dark) {
  :root {
    --fg: #e6edf3;
    --muted: #9198a1;
    --border: #3d444d;
    --surface: #161b22;
    --error: #ff7b72;
    --warning: #d29922;
    --info: #4493f8;
    --link: #4493f8;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0 auto;
  max-width: 980px;
  padding: 32px 20px 64px;
  background: Canvas;
  color: var(--fg);
  font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
}
h1 { font-size: 1.6rem; margin: 0 0 4px; }
h2 {
  font-size: 1.2rem;
  margin: 40px 0 12px;
  padding-bottom: 6px;
  border-bottom: 1px solid var(--border);
}
h3 { font-size: 1rem; margin: 0 0 8px; }
code, pre {
  font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace;
  font-size: 0.86rem;
}
.report-meta, .report-counts, .meta-line { color: var(--muted); margin: 4px 0; }
.sep { margin: 0 6px; }
.finding {
  border: 1px solid var(--border);
  border-left: 4px solid var(--muted);
  border-radius: 6px;
  padding: 14px 16px;
  margin: 12px 0;
}
.finding--error { border-left-color: var(--error); }
.finding--warning { border-left-color: var(--warning); }
.finding--info { border-left-color: var(--info); }
.badge {
  font-size: 0.7rem;
  font-weight: 700;
  letter-spacing: 0.04em;
  padding: 2px 6px;
  border-radius: 4px;
  background: var(--surface);
  border: 1px solid var(--border);
}
.badge--error { color: var(--error); }
.badge--warning { color: var(--warning); }
.badge--info { color: var(--info); }
.rule-id { color: var(--muted); }
.message { margin: 6px 0; }
.evidence-list { margin: 8px 0 4px; padding-left: 20px; }
.evidence-list li { margin: 2px 0; }
.ev-ref { color: var(--link); text-decoration: none; border-bottom: 1px dotted currentColor; }
.ev-ref:hover { text-decoration: underline; }
.ev-ref--unresolved { color: var(--muted); border-bottom-style: dashed; cursor: help; }
.evidence-item { margin: 14px 0 20px; }
.permalink { color: inherit; text-decoration: none; }
.snippet {
  margin: 0;
  padding: 10px 12px;
  overflow-x: auto;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 6px;
}
.snippet .ln {
  display: inline-block;
  width: 4ch;
  margin-right: 12px;
  text-align: right;
  color: var(--muted);
  user-select: none;
}
.snippet .src { white-space: pre; }
.snippet-missing { color: var(--muted); font-style: italic; }
.fact-item { margin: 6px 0; }
.fact-ref { color: var(--link); text-decoration: none; }
.fact-ref--unlinked { color: var(--muted); }
.empty { color: var(--muted); }
.report-footer {
  margin-top: 48px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
  color: var(--muted);
  font-size: 0.82rem;
}
"""


@dataclass(frozen=True)
class HTMLReportOptions:
    """Rendering options for :func:`render_html_report`."""

    title: str = "SmartBench diagnosis report"
    project_root: Path | None = None
    project_label: str | None = None
    generated_at: datetime | None = None
    max_snippet_lines: int = SNIPPET_MAX_LINES
    include_source: bool = True


@dataclass(frozen=True)
class SourceLocation:
    """A normalised ``file:line`` position used for evidence anchors."""

    path: str
    line_start: int | None = None
    line_end: int | None = None
    inside_project: bool = True

    @property
    def resolvable(self) -> bool:
        """Whether this location can safely become an anchor."""
        return bool(self.path) and self.inside_project and self.line_start is not None

    @property
    def display(self) -> str:
        """Human readable ``path:line`` label."""
        if not self.path:
            return ""
        if self.line_start is None:
            return self.path
        if self.line_end is not None and self.line_end > self.line_start:
            return "{}:{}-{}".format(self.path, self.line_start, self.line_end)
        return "{}:{}".format(self.path, self.line_start)


@dataclass(frozen=True)
class SourceSnippet:
    """A bounded slice of source text anchored at ``start_line``."""

    start_line: int
    lines: tuple[str, ...]


@dataclass(frozen=True)
class EvidenceEntry:
    """One deduplicated evidence target rendered in the evidence section."""

    anchor: str
    location: SourceLocation
    snippet: SourceSnippet | None


@dataclass(frozen=True)
class _Finding:
    rule_id: str
    rule_name: str
    severity: str
    message: str
    location: SourceLocation
    confidence: float
    evidence: tuple[SourceLocation, ...]
    fact_ids: tuple[str, ...]


@dataclass(frozen=True)
class _Fact:
    fact_id: str
    statement: str
    locations: tuple[SourceLocation, ...]


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _field(value: Any, *names: str) -> Any:
    """Read the first present mapping key or attribute from ``value``."""
    if value is None:
        return None
    if isinstance(value, Mapping):
        for name in names:
            if name in value and value[name] is not None:
                return value[name]
        return None
    for name in names:
        found = getattr(value, name, None)
        if found is not None:
            return found
    return None


def _coerce_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    try:
        number = int(str(value).strip().lstrip("Ll"))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _normalize_path(raw: Any, project_root: Path | None) -> tuple[str, bool]:
    """Return ``(posix relative path, inside_project)`` for an evidence path.

    Absolute paths under ``project_root`` are rebased; absolute paths and
    ``..`` segments that leave the project are kept for display but marked
    as not-inside-project so they never become links.
    """
    text = str(raw or "").strip().replace("\\", "/")
    if not text:
        return "", False
    if project_root is not None:
        root = str(project_root).replace("\\", "/").rstrip("/")
        if root and (text == root or text.lower() == root.lower()):
            return "", False
        if root and text.startswith(root + "/"):
            text = text[len(root) + 1 :]
        elif root and text.lower().startswith(root.lower() + "/"):
            text = text[len(root) + 1 :]
    text = re.sub(r"^\./+", "", text)
    if text.startswith("/") or _ABSOLUTE_PREFIX.match(text):
        return text, False
    parts = [part for part in text.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        return "/".join(parts), False
    return "/".join(parts), bool(parts)


def coerce_location(value: Any, project_root: Path | None) -> SourceLocation:
    """Coerce a ``Location``/``EvidenceRef``/mapping/``"path:line"`` string."""
    if isinstance(value, SourceLocation):
        return value
    if isinstance(value, str):
        text = value.strip()
        start = end = None
        match = _LINE_SUFFIX.search(text)
        if match is not None:
            start = _coerce_int(match.group("start"))
            end = _coerce_int(match.group("end"))
            text = text[: match.start()].rstrip(" #:,")
        path, inside = _normalize_path(text, project_root)
        return SourceLocation(path, start, end, inside)

    path, inside = _normalize_path(_field(value, "file_path", "path", "file"), project_root)
    start = _coerce_int(_field(value, "line_start", "line", "lineno", "start_line"))
    end = _coerce_int(_field(value, "line_end", "end_line", "end"))
    if start is not None and end is not None and end < start:
        start, end = end, start
    return SourceLocation(path, start, end, inside)


def _coerce_severity(value: Any) -> str:
    text = str(getattr(value, "value", value) or "info").strip().lower()
    return text if text in _SEVERITY_RANK else "info"


def _coerce_fact_ids(*sources: Any) -> tuple[str, ...]:
    collected: list[str] = []
    for source in sources:
        raw = _field(source, "evidence_fact_ids", "fact_ids", "fact_id")
        if isinstance(raw, str):
            candidates: Iterable[Any] = (raw,)
        elif isinstance(raw, (list, tuple, set, frozenset)):
            candidates = raw
        else:
            candidates = ()
        for item in candidates:
            text = str(item).strip()
            if text and text not in collected:
                collected.append(text)
    return tuple(collected)


def _fact_anchor(fact_id: str) -> str:
    slug = _FACT_SLUG.sub("-", str(fact_id).strip()).strip("-").lower()
    if not slug:
        return "fact-unknown"
    return slug if slug.startswith("fact-") else "fact-" + slug


class EvidenceIndex:
    """Deduplicate evidence locations and assign stable anchors to them."""

    def __init__(
        self,
        project_root: Path | None = None,
        max_snippet_lines: int = SNIPPET_MAX_LINES,
        include_source: bool = True,
    ) -> None:
        self._project_root = project_root
        self._max_lines = max(0, int(max_snippet_lines))
        self._include_source = include_source
        self._anchors: dict[tuple[str, int, int], str] = {}
        self._entries: list[EvidenceEntry] = []

    @property
    def entries(self) -> tuple[EvidenceEntry, ...]:
        return tuple(self._entries)

    def anchor_for(self, location: SourceLocation) -> str | None:
        """Return the anchor for ``location``, registering it on first use."""
        if not location.resolvable:
            return None
        line_start = int(location.line_start or 0)
        line_end = int(location.line_end or line_start)
        key = (location.path, line_start, line_end)
        existing = self._anchors.get(key)
        if existing is not None:
            return existing
        digest = hashlib.sha256("\x1f".join(str(part) for part in key).encode("utf-8")).hexdigest()
        anchor = "evidence-" + digest[:12]
        self._anchors[key] = anchor
        self._entries.append(EvidenceEntry(anchor, location, self._read_snippet(location)))
        return anchor

    def _read_snippet(self, location: SourceLocation) -> SourceSnippet | None:
        if not self._include_source or self._project_root is None or self._max_lines == 0:
            return None
        resolved = resolve_project_file(self._project_root, location.path)
        if resolved is None:
            return None
        text = read_text_bounded(resolved, max_bytes=SNIPPET_MAX_BYTES)
        if text is None:
            return None
        lines = text.splitlines()
        start = location.line_start or 1
        if start > len(lines):
            return None
        end = min(location.line_end or start, start + self._max_lines - 1, len(lines))
        chunk = lines[start - 1 : end]
        if not chunk:
            return None
        return SourceSnippet(
            start,
            tuple(line[:SNIPPET_MAX_LINE_CHARS] for line in chunk),
        )


class _ReportBuilder:
    """Turn findings into one self-contained HTML document."""

    def __init__(self, options: HTMLReportOptions, facts: Iterable[Any]) -> None:
        self.options = options
        self.root = options.project_root
        self.evidence = EvidenceIndex(
            project_root=options.project_root,
            max_snippet_lines=options.max_snippet_lines,
            include_source=options.include_source,
        )
        self.fact_anchors: dict[str, str] = {}
        self._facts: list[_Fact] = []
        for value in facts:
            fact = self._coerce_fact(value)
            if fact is None or fact.fact_id in self.fact_anchors:
                continue
            self._facts.append(fact)
            self._register_fact_anchor(fact.fact_id)

    # -- coercion ---------------------------------------------------------

    def _coerce_fact(self, value: Any) -> _Fact | None:
        fact_id = _field(value, "fact_id", "id", "key")
        if fact_id is None:
            return None
        raw = _field(value, "evidence", "locations", "sources") or ()
        if isinstance(raw, (str, Mapping)) or hasattr(raw, "file_path"):
            raw = (raw,)
        try:
            items = tuple(raw)
        except TypeError:
            items = ()
        locations = tuple(coerce_location(item, self.root) for item in items)
        statement = _field(value, "statement", "summary", "message") or ""
        return _Fact(str(fact_id), str(statement), locations)

    def _register_fact_anchor(self, fact_id: str) -> None:
        base = _fact_anchor(fact_id)
        candidate = base
        suffix = 2
        while candidate in self.fact_anchors.values():
            candidate = "{}-{}".format(base, suffix)
            suffix += 1
        self.fact_anchors[fact_id] = candidate

    def _coerce_finding(self, value: Any) -> _Finding:
        evidence_raw = _field(value, "evidence") or ()
        if isinstance(evidence_raw, (str, Mapping)) or hasattr(evidence_raw, "file_path"):
            evidence_raw = (evidence_raw,)
        try:
            evidence_items = tuple(evidence_raw)
        except TypeError:
            evidence_items = ()
        confidence_raw = _field(value, "confidence")
        try:
            confidence = float(confidence_raw) if confidence_raw is not None else 1.0
        except (TypeError, ValueError):
            confidence = 1.0
        return _Finding(
            rule_id=str(_field(value, "rule_id") or "unknown-rule"),
            rule_name=str(_field(value, "rule_name") or ""),
            severity=_coerce_severity(_field(value, "severity")),
            message=str(_field(value, "message") or ""),
            location=coerce_location(_field(value, "location"), self.root),
            confidence=confidence,
            evidence=tuple(coerce_location(item, self.root) for item in evidence_items),
            fact_ids=_coerce_fact_ids(_field(value, "metadata"), value),
        )

    # -- rendering --------------------------------------------------------

    def render(self, findings: Iterable[Any]) -> str:
        normalized = [self._coerce_finding(item) for item in findings]
        normalized.sort(
            key=lambda item: (
                _SEVERITY_RANK[item.severity],
                item.location.path,
                item.location.line_start or 0,
                item.rule_id,
                item.message,
            )
        )
        for finding in normalized:
            self.evidence.anchor_for(finding.location)
            for reference in finding.evidence:
                self.evidence.anchor_for(reference)
        for fact in self._facts:
            for location in fact.locations:
                self.evidence.anchor_for(location)

        counts = {
            severity: sum(1 for finding in normalized if finding.severity == severity)
            for severity in _SEVERITIES
        }
        body = [
            self._render_header(len(normalized), counts),
            "<main>",
            self._render_findings(normalized),
            self._render_facts(),
            self._render_evidence(),
            "</main>",
            self._render_footer(),
        ]
        document = [
            "<!DOCTYPE html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            "<title>{}</title>".format(_esc(self.options.title)),
            "<style>" + _CSS + "</style>",
            "</head>",
            "<body>",
        ]
        document.extend(part for part in body if part)
        document.extend(["</body>", "</html>", ""])
        return "\n".join(document)

    def _render_header(self, total: int, counts: Mapping[str, int]) -> str:
        generated = self.options.generated_at or datetime.now(timezone.utc)
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=timezone.utc)
        stamp = generated.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        root_label = (
            self.options.project_label
            or (str(self.root) if self.root is not None else "")
            or "not recorded"
        )
        badges = " ".join(
            '<span class="count count--{}">{} {}</span>'.format(severity, counts[severity], severity)
            for severity in _SEVERITIES
        )
        return "\n".join(
            [
                '<header class="report-header">',
                "  <h1>{}</h1>".format(_esc(self.options.title)),
                '  <p class="report-meta">Project <code>{}</code>'
                '<span class="sep">·</span>generated <time datetime="{}">{}</time></p>'.format(
                    _esc(root_label), stamp, stamp
                ),
                '  <p class="report-counts">{} finding(s) {}</p>'.format(total, badges),
                "</header>",
            ]
        )

    def _render_findings(self, findings: list[_Finding]) -> str:
        lines = ['<section class="findings">', '  <h2 id="findings">Findings</h2>']
        if not findings:
            lines.append('  <p class="empty">No findings recorded.</p>')
        for position, finding in enumerate(findings, 1):
            anchor = self.evidence.anchor_for(finding.location)
            lines.append(
                '  <article class="finding finding--{}" id="finding-{}"'
                ' data-rule-id="{}" data-severity="{}">'.format(
                    finding.severity,
                    position,
                    _esc(finding.rule_id),
                    finding.severity,
                )
            )
            title = finding.rule_name or finding.rule_id
            lines.append(
                '    <h3><span class="badge badge--{}">{}</span> {}'
                ' <code class="rule-id">{}</code></h3>'.format(
                    finding.severity,
                    finding.severity.upper(),
                    _esc(title),
                    _esc(finding.rule_id),
                )
            )
            lines.append('    <p class="message">{}</p>'.format(_esc(finding.message)))
            lines.append(
                '    <p class="meta-line">at {} <span class="sep">·</span>'
                ' confidence <span class="confidence">{:.2f}</span></p>'.format(
                    self._render_ref(finding.location, anchor),
                    finding.confidence,
                )
            )
            if finding.evidence:
                lines.append('    <ul class="evidence-list">')
                for reference in finding.evidence:
                    reference_anchor = self.evidence.anchor_for(reference)
                    lines.append("      <li>{}</li>".format(self._render_ref(reference, reference_anchor)))
                lines.append("    </ul>")
            if finding.fact_ids:
                lines.append(
                    '    <p class="fact-line">evidence facts: {}</p>'.format(
                        " ".join(self._render_fact_chip(fact_id) for fact_id in finding.fact_ids)
                    )
                )
            lines.append("  </article>")
        lines.append("</section>")
        return "\n".join(lines)

    def _render_fact_chip(self, fact_id: str) -> str:
        anchor = self.fact_anchors.get(fact_id)
        if anchor is None:
            return '<code class="fact-ref fact-ref--unlinked">{}</code>'.format(_esc(fact_id))
        return '<a class="fact-ref" href="#{}" title="jump to fact anchor">{}</a>'.format(
            _esc(anchor), _esc(fact_id)
        )

    def _render_facts(self) -> str:
        if not self._facts:
            return ""
        lines = ['<section class="facts">', '  <h2 id="facts">Facts</h2>', '  <ul class="fact-list">']
        for fact in self._facts:
            lines.append('    <li id="{}" class="fact-item">'.format(_esc(self.fact_anchors[fact.fact_id])))
            lines.append('      <code class="fact-id">{}</code>'.format(_esc(fact.fact_id)))
            if fact.statement:
                lines.append('      <span class="fact-statement">{}</span>'.format(_esc(fact.statement)))
            if fact.locations:
                references = " ".join(
                    self._render_ref(location, self.evidence.anchor_for(location))
                    for location in fact.locations
                )
                lines.append('      <span class="fact-evidence">{}</span>'.format(references))
            lines.append("    </li>")
        lines.append("  </ul>")
        lines.append("</section>")
        return "\n".join(lines)

    def _render_evidence(self) -> str:
        entries = self.evidence.entries
        lines = ['<section class="evidence">', '  <h2 id="evidence">Evidence sources</h2>']
        if not entries:
            lines.append('  <p class="empty">No resolvable evidence locations.</p>')
        for entry in entries:
            location = entry.location
            lines.append('  <article class="evidence-item" id="{}">'.format(_esc(entry.anchor)))
            lines.append(
                '    <h3><a class="permalink" href="#{}">{}</a></h3>'.format(
                    _esc(entry.anchor), _esc(location.display)
                )
            )
            if entry.snippet is None:
                lines.append(
                    '    <p class="snippet-missing">Source text not embedded'
                    " (the file was unavailable at render time).</p>"
                )
            else:
                lines.append('    <pre class="snippet"><code>')
                for offset, text in enumerate(entry.snippet.lines):
                    number = entry.snippet.start_line + offset
                    lines.append(
                        '<span class="ln" id="{}-L{}">{:>5}</span>'
                        '<span class="src">{}</span>'.format(
                            _esc(entry.anchor), number, number, _esc(text)
                        )
                    )
                lines.append("</code></pre>")
            lines.append("  </article>")
        lines.append("</section>")
        return "\n".join(lines)

    @staticmethod
    def _render_ref(location: SourceLocation, anchor: str | None) -> str:
        display = location.display
        if anchor is not None:
            return (
                '<a class="ev-ref" href="#{}" data-source-path="{}"'
                ' data-line-start="{}" data-line-end="{}"'
                ' title="{} - jump to embedded source">{}</a>'
            ).format(
                _esc(anchor),
                _esc(location.path),
                location.line_start,
                location.line_end if location.line_end is not None else "",
                _esc(display),
                _esc(display),
            )
        if not location.path:
            reason, hint = "missing-path", "no source path was recorded for this location"
        elif not location.inside_project:
            reason, hint = "outside-project", "location is outside the analysed project"
        elif location.line_start is None:
            reason, hint = "no-line", "no line number was recorded for this location"
        else:
            reason, hint = "missing-path", "no source path was recorded for this location"
        label = display or "(location unavailable)"
        return (
            '<span class="ev-ref ev-ref--unresolved" data-reason="{}" title="{}">{}</span>'.format(
                reason, _esc(hint), _esc(label)
            )
        )

    @staticmethod
    def _render_footer() -> str:
        return (
            '<footer class="report-footer">Single-file report: no external JavaScript, CSS or'
            " fonts. Evidence anchors are generated from recorded file:line data.</footer>"
        )


def render_html_report(
    findings: Iterable[Any],
    *,
    options: HTMLReportOptions | None = None,
    facts: Iterable[Any] = (),
) -> str:
    """Render findings as one self-contained HTML document.

    Args:
        findings: ``Finding`` objects or their ``to_dict()`` payloads.
        options: Rendering options; ``project_root`` enables source embedding.
        facts: Optional fact records (``fact_id`` + ``evidence``) that findings
            can reference through ``metadata["evidence_fact_ids"]``.

    Returns:
        A complete HTML document as a string.
    """
    return _ReportBuilder(options or HTMLReportOptions(), facts).render(findings)


def write_html_report(
    path: str | Path,
    findings: Iterable[Any],
    *,
    options: HTMLReportOptions | None = None,
    facts: Iterable[Any] = (),
) -> Path:
    """Render the report and write it to ``path`` (UTF-8), returning the path."""
    target = Path(path).expanduser().absolute()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        render_html_report(findings, options=options, facts=facts),
        encoding="utf-8",
    )
    return target
