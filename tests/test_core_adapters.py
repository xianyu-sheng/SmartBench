"""
Tests for the unified diagnostic framework adapters.
"""

import tempfile
from pathlib import Path

import pytest

from smartbench.core.adapters import (
    AdapterRegistry,
    GoAdapter,
    JavaAdapter,
    JavaScriptAdapter,
    PythonAdapter,
    RustAdapter,
    TypeScriptAdapter,
    register_all_adapters,
)


class TestPythonAdapter:
    def test_metadata(self):
        adapter = PythonAdapter()
        assert adapter.language == "python"
        assert ".py" in adapter.file_extensions

    def test_can_parse(self):
        adapter = PythonAdapter()
        assert adapter.can_parse(Path("test.py")) is True
        assert adapter.can_parse(Path("test.go")) is False
        assert adapter.can_parse(Path("test.py.txt")) is False

    def test_parse_python_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # Create a simple Python file
            test_file = tmp_path / "test.py"
            test_file.write_text("""
def hello():
    print("Hello world")

class MyClass:
    def method(self):
        return 42
""")

            adapter = PythonAdapter()
            graph = adapter.parse_file(test_file, tmp_path)

            assert graph is not None
            assert len(graph.nodes) > 0


class TestGoAdapter:
    def test_metadata(self):
        adapter = GoAdapter()
        assert adapter.language == "go"
        assert ".go" in adapter.file_extensions

    def test_can_parse(self):
        adapter = GoAdapter()
        assert adapter.can_parse(Path("test.go")) is True
        assert adapter.can_parse(Path("test.py")) is False


class TestAdapterRegistry:
    def test_register_and_retrieve(self):
        registry = AdapterRegistry()
        registry.register(PythonAdapter())
        registry.register(GoAdapter())

        assert registry.get_adapter_for_language("python") is not None
        assert registry.get_adapter_for_language("go") is not None

    def test_get_adapter_for_file(self):
        registry = AdapterRegistry()
        registry.register(PythonAdapter())
        registry.register(GoAdapter())

        assert registry.get_adapter_for_file(Path("main.py")).language == "python"
        assert registry.get_adapter_for_file(Path("main.go")).language == "go"

    def test_register_duplicate_fails(self):
        registry = AdapterRegistry()
        registry.register(PythonAdapter())

        with pytest.raises(ValueError):
            registry.register(PythonAdapter())

    def test_list_languages(self):
        registry = AdapterRegistry()
        registry.register(PythonAdapter())
        registry.register(GoAdapter())

        langs = registry.list_languages()
        assert "python" in langs
        assert "go" in langs


class TestAllAdapters:
    def test_java_adapter_metadata(self):
        adapter = JavaAdapter()
        assert adapter.language == "java"
        assert ".java" in adapter.file_extensions

    def test_rust_adapter_metadata(self):
        adapter = RustAdapter()
        assert adapter.language == "rust"
        assert ".rs" in adapter.file_extensions

    def test_javascript_adapter_metadata(self):
        adapter = JavaScriptAdapter()
        assert adapter.language == "javascript"
        assert ".js" in adapter.file_extensions

    def test_typescript_adapter_metadata(self):
        adapter = TypeScriptAdapter()
        assert adapter.language == "typescript"
        assert ".ts" in adapter.file_extensions

    def test_register_all_adapters(self):
        registry = AdapterRegistry()
        register_all_adapters(registry)

        langs = registry.list_languages()
        assert "python" in langs
        assert "go" in langs
        assert "java" in langs
        assert "rust" in langs
        assert "javascript" in langs
        assert "typescript" in langs
        assert len(langs) >= 6
