"""
CodeGraphBuilder — parses source files into a CodeGraph.

Uses tree-sitter when available (superior precision), falls back to
regex-based heuristic parsing for all supported languages.

Architecture: language-specific parsers register with the builder.
Adding a new language = adding a new parser class.
"""

from pathlib import Path
from typing import Dict, List, Optional, Set, Any, Tuple
import re
import time

from smartbench.graph.schema import (
    CodeGraph, CodeNode, CodeEdge, NodeType, EdgeType,
)
from smartbench.detector.fingerprint import Language


# ── Regex patterns per language ──────────────────────────────────────

# These are heuristic, not 100% precise. They capture the most common
# function/class definition patterns with reasonable accuracy.

_PATTERNS = {
    Language.PYTHON: {
        "function": re.compile(
            r'^\s*def\s+(?P<name>\w+)\s*\(', re.MULTILINE
        ),
        "class": re.compile(
            r'^\s*class\s+(?P<name>\w+)\s*[(:]', re.MULTILINE
        ),
        "import": re.compile(
            r'^(?:from\s+(?P<module>\S+)\s+)?import\s+(?P<names>[\w\s,]+)',
            re.MULTILINE,
        ),
        "call": re.compile(
            r'(?P<name>\w+)\s*\(', re.MULTILINE
        ),
    },
    Language.GO: {
        "function": re.compile(
            r'func\s+(?:\(\w+\s+\*?\w+\)\s+)?(?P<name>\w+)\s*\(', re.MULTILINE
        ),
        "struct": re.compile(
            r'type\s+(?P<name>\w+)\s+struct\s*\{', re.MULTILINE
        ),
        "interface": re.compile(
            r'type\s+(?P<name>\w+)\s+interface\s*\{', re.MULTILINE
        ),
        "import": re.compile(
            r'import\s+\(\s*((?:"[^"]+"\s*)+)\)|import\s+"(?P<pkg>[^"]+)"',
            re.MULTILINE,
        ),
        "call": re.compile(
            r'(?P<name>\w+)\s*\(', re.MULTILINE
        ),
    },
    Language.RUST: {
        "function": re.compile(
            r'fn\s+(?P<name>\w+)\s*[<\(]', re.MULTILINE
        ),
        "struct": re.compile(
            r'(?:pub\s+)?struct\s+(?P<name>\w+)', re.MULTILINE
        ),
        "impl": re.compile(
            r'impl\s+(?:[\w<>,:\s]+\s+)?(?:for\s+)?(?P<name>\w+)', re.MULTILINE
        ),
        "trait": re.compile(
            r'(?:pub\s+)?trait\s+(?P<name>\w+)', re.MULTILINE
        ),
        "call": re.compile(
            r'(?P<name>\w+)\s*\(', re.MULTILINE
        ),
    },
    Language.CPP: {
        "function": re.compile(
            r'(?:virtual\s+)?(?:static\s+)?(?:inline\s+)?(?:[\w:]+\s+)+'
            r'(?P<name>\w+)\s*\([^)]*\)\s*(?:const\s*)?\{?',
            re.MULTILINE,
        ),
        "class": re.compile(
            r'class\s+(?P<name>\w+)', re.MULTILINE
        ),
        "struct": re.compile(
            r'struct\s+(?P<name>\w+)', re.MULTILINE
        ),
        "call": re.compile(
            r'(?P<name>\w+)\s*\(', re.MULTILINE
        ),
    },
    Language.JAVA: {
        "function": re.compile(
            r'(?:public|private|protected|static|\s)+[\w<>\[\]]+\s+'
            r'(?P<name>\w+)\s*\([^)]*\)\s*(?:\{|throws)',
            re.MULTILINE,
        ),
        "class": re.compile(
            r'(?:public\s+)?class\s+(?P<name>\w+)', re.MULTILINE
        ),
        "interface": re.compile(
            r'(?:public\s+)?interface\s+(?P<name>\w+)', re.MULTILINE
        ),
        "call": re.compile(
            r'(?P<name>\w+)\s*\(', re.MULTILINE
        ),
    },
    Language.JAVASCRIPT: {
        "function": re.compile(
            r'(?:function\s+(?P<name>\w+)|(?P<name2>\w+)\s*=\s*(?:async\s+)?'
            r'\([^)]*\)\s*=>|(?P<name3>\w+)\s*=\s*function)',
            re.MULTILINE,
        ),
        "class": re.compile(
            r'class\s+(?P<name>\w+)', re.MULTILINE
        ),
        "import": re.compile(
            r'import\s+.*?\s+from\s+[\'"](?P<module>[^\'"]+)[\'"]',
            re.MULTILINE,
        ),
        "call": re.compile(
            r'(?P<name>\w+)\s*\(', re.MULTILINE
        ),
    },
    Language.TYPESCRIPT: {
        "function": re.compile(
            r'(?:function\s+(?P<name>\w+)|(?P<name2>\w+)\s*=\s*(?:async\s+)?'
            r'\([^)]*\)\s*=>|(?P<name3>\w+)\s*=\s*function|'
            r'(?:public|private|protected)\s+(?:async\s+)?'
            r'(?P<name4>\w+)\s*\([^)]*\)\s*[:{])',
            re.MULTILINE,
        ),
        "class": re.compile(
            r'class\s+(?P<name>\w+)', re.MULTILINE
        ),
        "import": re.compile(
            r'import\s+.*?\s+from\s+[\'"](?P<module>[^\'"]+)[\'"]',
            re.MULTILINE,
        ),
        "call": re.compile(
            r'(?P<name>\w+)\s*\(', re.MULTILINE
        ),
    },
    Language.RUBY: {
        "function": re.compile(
            r'def\s+(?P<name>\w+)', re.MULTILINE
        ),
        "class": re.compile(
            r'class\s+(?P<name>\w+)', re.MULTILINE
        ),
        "call": re.compile(
            r'(?P<name>\w+)\s*\(', re.MULTILINE
        ),
    },
    Language.SWIFT: {
        "function": re.compile(
            r'func\s+(?P<name>\w+)\s*\(', re.MULTILINE
        ),
        "class": re.compile(
            r'class\s+(?P<name>\w+)', re.MULTILINE
        ),
        "struct": re.compile(
            r'struct\s+(?P<name>\w+)', re.MULTILINE
        ),
        "call": re.compile(
            r'(?P<name>\w+)\s*\(', re.MULTILINE
        ),
    },
    Language.CSHARP: {
        "function": re.compile(
            r'(?:public|private|protected|internal|static|\s)+[\w<>\[\]]+\s+'
            r'(?P<name>\w+)\s*\([^)]*\)',
            re.MULTILINE,
        ),
        "class": re.compile(
            r'(?:public\s+)?class\s+(?P<name>\w+)', re.MULTILINE
        ),
        "interface": re.compile(
            r'(?:public\s+)?interface\s+(?P<name>\w+)', re.MULTILINE
        ),
        "call": re.compile(
            r'(?P<name>\w+)\s*\(', re.MULTILINE
        ),
    },
    Language.KOTLIN: {
        "function": re.compile(
            r'(?:fun\s+(?P<name>\w+)\s*\(|(?P<name2>\w+)\s*=\s*fun\s*\()',
            re.MULTILINE,
        ),
        "class": re.compile(
            r'(?:data\s+)?class\s+(?P<name>\w+)', re.MULTILINE
        ),
        "call": re.compile(
            r'(?P<name>\w+)\s*\(', re.MULTILINE
        ),
    },
    Language.ZIG: {
        "function": re.compile(
            r'fn\s+(?P<name>\w+)\s*\(', re.MULTILINE
        ),
        "struct": re.compile(
            r'(?:pub\s+)?const\s+(?P<name>\w+)\s*=\s*struct', re.MULTILINE
        ),
        "call": re.compile(
            r'(?P<name>\w+)\s*\(', re.MULTILINE
        ),
    },
}

# File extensions per language (for discovery)
_LANG_EXTENSIONS: Dict[Language, List[str]] = {
    Language.PYTHON: [".py"],
    Language.GO: [".go"],
    Language.RUST: [".rs"],
    Language.CPP: [".cpp", ".cc", ".cxx", ".h", ".hpp"],
    Language.C: [".c", ".h"],
    Language.JAVA: [".java"],
    Language.KOTLIN: [".kt", ".kts"],
    Language.JAVASCRIPT: [".js", ".mjs", ".cjs"],
    Language.TYPESCRIPT: [".ts", ".tsx"],
    Language.RUBY: [".rb"],
    Language.SWIFT: [".swift"],
    Language.CSHARP: [".cs"],
    Language.ZIG: [".zig"],
}


class CodeGraphBuilder:
    """
    Builds a CodeGraph from a project directory.

    Usage:
        builder = CodeGraphBuilder()
        graph = builder.build("/path/to/project", Language.GO)

    The builder:
    1. Discovers all source files for the detected language
    2. Parses each file to extract functions, classes, imports
    3. Resolves call edges between nodes
    4. Returns a CodeGraph ready for querying
    """

    # Directories to exclude from scanning
    EXCLUDED_DIRS: Set[str] = {
        ".git", "node_modules", "__pycache__", "target", "build",
        "vendor", ".venv", "venv", "dist", ".idea", ".vscode",
        "obj", ".tox", ".eggs", "*.egg-info",
    }

    # Files to exclude
    EXCLUDED_PATTERNS: List[str] = [
        "*_test.go", "*_test.py", "test_*.py", "*.spec.ts", "*.test.ts",
        "*.pb.go", "*.pb.cc", "*_generated.go",
    ]

    def __init__(self, max_files: int = 500, use_treesitter: bool = True):
        """
        Args:
            max_files: Maximum source files to parse (safety limit)
            use_treesitter: Attempt to use tree-sitter if installed
        """
        self.max_files = max_files
        self.use_treesitter = use_treesitter
        self._treesitter_available = False
        self._ts_parser = None
        if use_treesitter:
            self._init_treesitter()

    def _init_treesitter(self) -> None:
        """Lazy-init tree-sitter. Called once per builder instance."""
        try:
            from smartbench.graph.tree_parser import is_available
            self._treesitter_available = is_available()
        except ImportError:
            self._treesitter_available = False

    def build(self, project_path: str, language: Language,
              file_filter: Optional[List[str]] = None) -> CodeGraph:
        """
        Build a complete code graph.

        Args:
            project_path: Root directory of the project
            language: Primary language to parse
            file_filter: Optional list of specific files (relative paths) to parse

        Returns:
            A CodeGraph ready for querying
        """
        start_time = time.time()
        root = Path(project_path).resolve()
        graph = CodeGraph(meta={
            "project_path": str(root),
            "language": language.value,
            "build_time_ms": 0,
        })

        # 1. Discover source files
        source_files = self._discover_files(root, language, file_filter)
        if not source_files:
            return graph

        # 2. Parse each file → nodes + edges
        # Try tree-sitter first for supported languages, fall back to regex
        patterns = _PATTERNS.get(language, {})
        all_functions: Dict[str, CodeNode] = {}  # name → node for call resolution

        tree_parser = None
        if self._treesitter_available:
            from smartbench.graph.tree_parser import get_parser
            tree_parser = get_parser(language.value)

        for file_path in source_files:
            rel_path = str(file_path.relative_to(root))
            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
            except (OSError, PermissionError):
                continue

            # File node
            file_node = CodeNode(
                id=CodeNode.make_id(rel_path, rel_path, NodeType.FILE),
                node_type=NodeType.FILE,
                name=rel_path,
                file_path=rel_path,
                language=language.value,
                properties={"line_count": content.count("\n")},
            )
            graph.add_node(file_node)

            if tree_parser is not None:
                # ── Tree-sitter path (precise AST) ──────────────────
                func_nodes, class_nodes = self._parse_file_treesitter(
                    tree_parser, content, rel_path, language
                )
            else:
                # ── Regex path (fallback) ──────────────────────────
                func_nodes = self._parse_functions(
                    content, rel_path, language, patterns
                )
                class_nodes = self._parse_classes(
                    content, rel_path, language, patterns
                )

            for fn in func_nodes:
                graph.add_node(fn)
                graph.add_edge(CodeEdge(
                    source_id=file_node.id,
                    target_id=fn.id,
                    edge_type=EdgeType.CONTAINS,
                ))
                all_functions[fn.name] = fn

            for cn in class_nodes:
                graph.add_node(cn)
                graph.add_edge(CodeEdge(
                    source_id=file_node.id,
                    target_id=cn.id,
                    edge_type=EdgeType.CONTAINS,
                ))

        # 3. Resolve calls between functions
        # Always use regex for call resolution — it's more robust than
        # tree-sitter's call_expression traversal across languages.
        # Tree-sitter is used for precise function/class extraction only.
        self._resolve_calls(graph, all_functions, patterns)

        # 4. Resolve imports
        self._resolve_imports(graph, root, source_files, language, patterns)

        elapsed = int((time.time() - start_time) * 1000)
        graph.meta["build_time_ms"] = elapsed

        return graph

    # ── File discovery ────────────────────────────────────────────────

    def _discover_files(self, root: Path, language: Language,
                        file_filter: Optional[List[str]] = None) -> List[Path]:
        """Find all source files for the given language."""
        if file_filter:
            return [root / f for f in file_filter if (root / f).exists()]

        extensions = _LANG_EXTENSIONS.get(language, [])
        files = []

        for ext in extensions:
            for f in root.rglob(f"*{ext}"):
                # Check excluded dirs
                parts = set(p.name for p in f.parents)
                if parts & self.EXCLUDED_DIRS:
                    continue
                # Check excluded patterns
                if any(self._match_pattern(f.name, pat) for pat in self.EXCLUDED_PATTERNS):
                    continue
                files.append(f)
                if len(files) >= self.max_files:
                    break
            if len(files) >= self.max_files:
                break

        return files

    @staticmethod
    def _match_pattern(name: str, pattern: str) -> bool:
        """Simple glob matching for exclusion patterns."""
        import fnmatch
        return fnmatch.fnmatch(name, pattern)

    # ── Tree-sitter based parsing ─────────────────────────────────────

    def _parse_file_treesitter(
        self, parser: Any, content: str, file_path: str, language: Language
    ) -> Tuple[List[CodeNode], List[CodeNode]]:
        """Parse a single file using tree-sitter AST.

        Returns:
            (function_nodes, class_nodes) tuple.
        """
        from smartbench.graph.tree_parser import extract_symbols

        source_bytes = content.encode("utf-8")
        symbols = extract_symbols(parser, source_bytes, file_path)

        func_nodes = []
        for fn in symbols["functions"]:
            node = CodeNode(
                id=CodeNode.make_id(
                    file_path, fn["name"], NodeType.FUNCTION, fn["line"]
                ),
                node_type=NodeType.FUNCTION,
                name=fn["name"],
                file_path=file_path,
                line_start=fn["line"],
                language=language.value,
                properties={"signature": fn.get("signature", "")},
            )
            func_nodes.append(node)

        class_nodes = []
        for cls in symbols["classes"]:
            node = CodeNode(
                id=CodeNode.make_id(
                    file_path, cls["name"], NodeType.CLASS, cls["line"]
                ),
                node_type=NodeType.CLASS,
                name=cls["name"],
                file_path=file_path,
                line_start=cls["line"],
                language=language.value,
            )
            class_nodes.append(node)

        return func_nodes, class_nodes

    # ── Regex-based parsing (fallback) ──────────────────────────────────

    def _parse_functions(self, content: str, file_path: str,
                         language: Language, patterns: Dict) -> List[CodeNode]:
        """Extract function/method definitions."""
        func_pattern = patterns.get("function")
        if not func_pattern:
            return []

        nodes = []
        seen: Set[str] = set()
        lines = content.split("\n")

        for match in func_pattern.finditer(content):
            # Get name from whichever named group matched
            name = None
            for gname in ("name", "name2", "name3", "name4"):
                try:
                    name = match.group(gname)
                    if name:
                        break
                except IndexError:
                    continue

            if not name or name in seen:
                continue
            seen.add(name)

            line_no = content[:match.start()].count("\n") + 1
            node = CodeNode(
                id=CodeNode.make_id(file_path, name, NodeType.FUNCTION, line_no),
                node_type=NodeType.FUNCTION,
                name=name,
                file_path=file_path,
                line_start=line_no,
                language=language.value,
                properties={"signature": lines[line_no - 1].strip() if line_no <= len(lines) else ""},
            )
            nodes.append(node)

        return nodes

    def _parse_classes(self, content: str, file_path: str,
                       language: Language, patterns: Dict) -> List[CodeNode]:
        """Extract class/struct/interface definitions."""
        nodes = []
        seen: Set[str] = set()

        for pattern_key in ("class", "struct", "interface", "trait", "impl"):
            pat = patterns.get(pattern_key)
            if not pat:
                continue

            for match in pat.finditer(content):
                name = match.group("name")
                if not name or name in seen:
                    continue
                seen.add(name)

                line_no = content[:match.start()].count("\n") + 1
                node = CodeNode(
                    id=CodeNode.make_id(file_path, name, NodeType.CLASS, line_no),
                    node_type=NodeType.CLASS,
                    name=name,
                    file_path=file_path,
                    line_start=line_no,
                    language=language.value,
                )
                nodes.append(node)

        return nodes

    # ── Edge resolution ───────────────────────────────────────────────

    def _resolve_calls(self, graph: CodeGraph,
                       all_functions: Dict[str, CodeNode],
                       patterns: Dict) -> None:
        """For each function node, find calls to other known functions."""
        call_pattern = patterns.get("call")
        if not call_pattern:
            return

        func_nodes = [n for n in graph.nodes.values() if n.node_type == NodeType.FUNCTION]
        if not func_nodes:
            return

        # Cache file contents to avoid repeated I/O (BUG FIX: was reading per-function)
        file_contents: Dict[str, str] = {}
        for fn in func_nodes:
            if fn.file_path not in file_contents:
                try:
                    file_path = (Path(graph.meta["project_path"]) / fn.file_path)
                    file_contents[fn.file_path] = file_path.read_text(
                        encoding="utf-8", errors="ignore"
                    )
                except (OSError, PermissionError, KeyError):
                    file_contents[fn.file_path] = ""

        for fn in func_nodes:
            content = file_contents.get(fn.file_path, "")
            if not content:
                continue

            func_start_line = fn.line_start
            lines = content.split("\n")
            scope = "\n".join(lines[func_start_line:func_start_line + 200])

            called_names: Set[str] = set()
            for match in call_pattern.finditer(scope):
                name = match.group("name")
                if name and name != fn.name and name in all_functions:
                    called_names.add(name)

            for callee_name in called_names:
                callee = all_functions[callee_name]
                graph.add_edge(CodeEdge(
                    source_id=fn.id,
                    target_id=callee.id,
                    edge_type=EdgeType.CALLS,
                ))

    def _resolve_imports(self, graph: CodeGraph, root: Path,
                         source_files: List[Path], language: Language,
                         patterns: Dict) -> None:
        """Create IMPORT edges between files and imported modules."""
        import_pattern = patterns.get("import")
        if not import_pattern:
            return

        for file_path in source_files:
            rel_path = str(file_path.relative_to(root))
            file_id = CodeNode.make_id(rel_path, rel_path, NodeType.FILE)

            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
            except (OSError, PermissionError):
                continue

            for match in import_pattern.finditer(content):
                # Extract module name from whichever group matched
                module_name = None
                for gname in ("module", "pkg", "names"):
                    try:
                        module_name = match.group(gname)
                        if module_name:
                            break
                    except IndexError:
                        continue

                if module_name:
                    # Create a MODULE node for the import
                    module_id = CodeNode.make_id(
                        rel_path, f"import:{module_name}", NodeType.IMPORT,
                    )
                    module_node = CodeNode(
                        id=module_id,
                        node_type=NodeType.IMPORT,
                        name=module_name.strip().strip('"').strip("'"),
                        file_path=rel_path,
                        language=language.value,
                    )
                    graph.add_node(module_node)
                    graph.add_edge(CodeEdge(
                        source_id=file_id,
                        target_id=module_id,
                        edge_type=EdgeType.IMPORTS,
                    ))

    # ── Tree-sitter language mapping ───────────────────────────────────

    @staticmethod
    def _check_treesitter() -> bool:
        """Check if tree-sitter is available (kept for backward compat)."""
        try:
            from smartbench.graph.tree_parser import is_available
            return is_available()
        except ImportError:
            return False
