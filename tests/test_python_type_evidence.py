"""Tests for the Python surface-type evidence provider."""

from pathlib import Path

from smartbench.core.adapters.python import PythonAdapter
from smartbench.ir import TypeEvidenceRole, TypeEvidenceSource


def _build_ir(src: str, filename: str = "app.py"):
    """Build a SemanticIR over a single in-memory Python file."""
    tmp = Path("/tmp") / f"py_evidence_dir_{filename}"
    tmp.mkdir(exist_ok=True)
    (tmp / filename).write_text(src, encoding="utf-8")
    try:
        return PythonAdapter().parse_semantic_project(tmp)
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


def _evidence_types(ir) -> list[tuple[str, str]]:
    """Return (type_name, source) pairs from Python type evidence."""
    out = []
    for ev in ir.type_evidence:
        if ev.role == TypeEvidenceRole.RECEIVER:
            out.append((ev.type_name, ev.source))
    return out


def test_annotated_assignment_binds_receiver():
    src = (
        "import socket\n"
        "\n"
        "def handle():\n"
        "    conn: socket.socket = socket.socket()\n"
        "    conn.send(b'x')\n"
        "    conn.close()\n"
    )
    ir = _build_ir(src)
    pairs = _evidence_types(ir)
    assert any(t == "socket.socket" for t, _ in pairs), f"got {pairs}"


def test_parameter_annotation_binds_receiver():
    src = (
        "import io\n"
        "\n"
        "def write_to(f: io.BufferedWriter):\n"
        "    f.write(b'x')\n"
    )
    ir = _build_ir(src)
    pairs = _evidence_types(ir)
    assert any("BufferedWriter" in t for t, _ in pairs), f"got {pairs}"


def test_return_annotation_propagates_to_call_target():
    src = (
        "import tempfile\n"
        "\n"
        "def make_temp() -> tempfile.NamedTemporaryFile:\n"
        "    return tempfile.NamedTemporaryFile()\n"
        "\n"
        "def use():\n"
        "    f = make_temp()\n"
        "    f.write(b'x')\n"
    )
    ir = _build_ir(src)
    pairs = _evidence_types(ir)
    assert any("NamedTemporaryFile" in t for t, _ in pairs), f"got {pairs}"


def test_attribute_chain_receiver_skipped():
    """x.method() where x is an attribute chain should not bind a type."""
    src = (
        "class C:\n"
        "    pass\n"
        "\n"
        "c = C()\n"
        "c.method()\n"
    )
    ir = _build_ir(src)
    pairs = _evidence_types(ir)
    # 'c.method' has receiver 'c' (simple name) but no annotation → no evidence.
    assert not pairs, f"expected no evidence, got {pairs}"


def test_no_annotation_no_evidence():
    src = (
        "def f():\n"
        "    x = open('a')\n"
        "    x.read()\n"
    )
    ir = _build_ir(src)
    pairs = _evidence_types(ir)
    # x has no annotation and no return-annotation source → abstain.
    assert not pairs, f"expected abstention, got {pairs}"


def test_provider_metadata_present():
    src = "def f():\n    return 1\n"
    ir = _build_ir(src)
    meta = ir.meta.get("python_type_evidence", {})
    assert meta.get("provider") == "python.surface"
    assert meta.get("schema_version", "").startswith("semantic-ir/type-evidence/")
    assert "files_analyzed" in meta
    assert "count" in meta


def test_evidence_source_is_surface_for_capitalized_type():
    src = (
        "import io\n"
        "\n"
        "def f(h: io.BufferedWriter):\n"
        "    h.flush()\n"
    )
    ir = _build_ir(src)
    pairs = _evidence_types(ir)
    assert pairs, "expected evidence"
    t, source = pairs[0]
    assert source == TypeEvidenceSource.SURFACE_DECLARATION, f"source={source}"
