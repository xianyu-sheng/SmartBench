"""
CodeGraphBuilder — parses source files into a CodeGraph.

Uses tree-sitter when available (superior precision), falls back to
regex-based heuristic parsing for all supported languages.

Architecture: language-specific parsers register with the builder.
Adding a new language = adding a new parser class.
"""

import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from smartbench.detector.fingerprint import Language
from smartbench.graph.schema import (
    CodeEdge,
    CodeGraph,
    CodeNode,
    EdgeType,
    NodeType,
)
from smartbench.path_safety import (
    is_project_file,
    read_text_bounded,
    read_text_prefix,
    resolve_project_file,
)
from smartbench.provenance import SourceRole, classify_source_role

_GRAPH_MAX_DIRECTORIES = 5_000
_GRAPH_MAX_DISCOVERED_FILES = 50_000
_DEFAULT_MAX_FILES = 500
_DEFAULT_MAX_FILE_BYTES = 2 * 1024 * 1024

# ── Regex patterns per language ──────────────────────────────────────

# These are heuristic, not 100% precise. They capture the most common
# function/class definition patterns with reasonable accuracy.

_PATTERNS = {
    Language.PYTHON: {
        "function": re.compile(
            r'^[ \t]*(?:async[ \t]+)?def[ \t]+(?P<name>\w+)[ \t]*\(',
            re.MULTILINE,
        ),
        "class": re.compile(
            r'^[ \t]*class[ \t]+(?P<name>\w+)[ \t]*'
            r'(?:\([^\r\n]*\))?[ \t]*:',
            re.MULTILINE,
        ),
        "import": re.compile(
            r'^[ \t]*(?:from[ \t]+(?P<module>\S+)[ \t]+)?'
            r'import[ \t]+(?P<names>[^\r\n#]+)',
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
            r'^[ \t]*import[ \t]*\((?P<packages>.*?)\)'
            r'|^[ \t]*import[ \t]+(?:[\w.]+[ \t]+)?"(?P<pkg>[^"]+)"',
            re.MULTILINE | re.DOTALL,
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
            r'^[ \t]*import[ \t]+'
            r'(?:(?:[^;\r\n]+?)[ \t]+from[ \t]+)?'
            r'[\'"](?P<module>[^\'"\r\n]+)[\'"]',
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
            r'^[ \t]*import[ \t]+'
            r'(?:(?:[^;\r\n]+?)[ \t]+from[ \t]+)?'
            r'[\'"](?P<module>[^\'"\r\n]+)[\'"]',
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
        "obj", ".tox", ".eggs", "*.egg-info", ".smartbench",
        ".pytest_cache", ".mypy_cache", ".ruff_cache",
    }

    # Files to exclude
    EXCLUDED_PATTERNS: List[str] = [
        "*_test.go", "*_test.py", "test_*.py", "*.spec.ts", "*.test.ts",
        "*.pb.go", "*.pb.cc", "*_generated.go",
    ]

    def __init__(
        self,
        max_files: int = _DEFAULT_MAX_FILES,
        use_treesitter: bool = True,
        max_file_bytes: int = _DEFAULT_MAX_FILE_BYTES,
    ):
        """
        Args:
            max_files: Maximum source files to parse (safety limit)
            use_treesitter: Attempt to use tree-sitter if installed
            max_file_bytes: Maximum bytes read from one source file
        """
        try:
            self.max_files = max(1, int(max_files))
        except (TypeError, ValueError, OverflowError):
            self.max_files = _DEFAULT_MAX_FILES
        try:
            self.max_file_bytes = max(1, int(max_file_bytes))
        except (TypeError, ValueError, OverflowError):
            self.max_file_bytes = _DEFAULT_MAX_FILE_BYTES
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
            "max_files": self.max_files,
            "max_file_bytes": self.max_file_bytes,
        })

        # 1. Discover source files
        source_files = self._discover_files(root, language, file_filter)
        if not source_files:
            return graph

        # 2. Parse each file → nodes + edges
        # Try tree-sitter first for supported languages, fall back to regex
        patterns = _PATTERNS.get(language, {})
        all_functions: Dict[str, List[CodeNode]] = {}
        file_contents: Dict[str, str] = {}

        tree_parser = None
        if self._treesitter_available:
            from smartbench.graph.tree_parser import get_parser
            tree_parser = get_parser(language.value)

        for file_path in source_files:
            rel_path = file_path.relative_to(root).as_posix()
            content = read_text_bounded(file_path, self.max_file_bytes)
            if content is None:
                continue
            file_contents[rel_path] = content

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
                all_functions.setdefault(fn.name, []).append(fn)

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
        self._resolve_calls(
            graph, all_functions, patterns, file_contents
        )

        # 4. Resolve imports
        self._resolve_imports(
            graph, source_files, language, patterns, file_contents
        )

        elapsed = int((time.time() - start_time) * 1000)
        graph.meta["build_time_ms"] = elapsed

        return graph

    # ── File discovery ────────────────────────────────────────────────

    def _discover_files(self, root: Path, language: Language,
                        file_filter: Optional[List[str]] = None) -> List[Path]:
        """Find bounded project source files with a single pruned walk."""
        extensions = set(_LANG_EXTENSIONS.get(language, []))
        if not extensions:
            return []

        if file_filter:
            filtered = []
            for requested in file_filter:
                if not isinstance(requested, (str, Path)):
                    continue
                resolved = resolve_project_file(root, requested)
                if resolved is None or resolved.suffix not in extensions:
                    continue
                if any(
                    self._match_pattern(resolved.name, pattern)
                    for pattern in self.EXCLUDED_PATTERNS
                ):
                    continue
                try:
                    if resolved.stat().st_size > self.max_file_bytes:
                        continue
                except OSError:
                    continue
                filtered.append(resolved)
            return sorted(set(filtered))[:self.max_files]

        files: List[Path] = []
        visited_directories = 0
        # Count files relevant to this frontend, not every unrelated file in
        # a monorepo.  A global filename counter can starve a secondary
        # language when an earlier top-level package contains many assets or
        # sources in another language.
        discovered_files = 0
        for current, dirnames, filenames in os.walk(
            root, topdown=True, followlinks=False
        ):
            visited_directories += 1
            if visited_directories > _GRAPH_MAX_DIRECTORIES:
                break

            current_path = Path(current)
            safe_dirs = []
            for dirname in sorted(dirnames):
                candidate = current_path / dirname
                if (
                    any(
                        self._match_pattern(dirname, pattern)
                        for pattern in self.EXCLUDED_DIRS
                    )
                    or candidate.is_symlink()
                ):
                    continue
                try:
                    candidate.resolve().relative_to(root)
                except (OSError, RuntimeError, ValueError):
                    continue
                safe_dirs.append(dirname)
            dirnames[:] = safe_dirs

            for filename in sorted(filenames):
                candidate = current_path / filename
                if candidate.suffix not in extensions:
                    continue
                if any(
                    self._match_pattern(filename, pattern)
                    for pattern in self.EXCLUDED_PATTERNS
                ):
                    continue
                discovered_files += 1
                if discovered_files > _GRAPH_MAX_DISCOVERED_FILES:
                    return self._bounded_file_sample(files)
                if not is_project_file(root, candidate):
                    continue
                try:
                    if candidate.stat().st_size > self.max_file_bytes:
                        continue
                except OSError:
                    continue
                files.append(candidate)

        return self._bounded_file_sample(files)

    def _bounded_file_sample(self, files: List[Path]) -> List[Path]:
        """Select a deterministic repository-wide sample.

        Returning as soon as ``max_files`` is reached makes the result depend
        on which top-level directory sorts first and can entirely starve later
        packages in a monorepo.  Source provenance prioritizes authored code;
        evenly spaced selection within a role preserves repository-wide path
        coverage without weakening the hard bound.
        """
        ordered = sorted(set(files))
        if len(ordered) <= self.max_files:
            return ordered
        priorities = {
            SourceRole.PRODUCTION: 0,
            SourceRole.UNKNOWN: 0,
            SourceRole.TEST: 1,
            SourceRole.FIXTURE: 2,
            SourceRole.EXAMPLE: 2,
            SourceRole.GENERATED: 3,
            SourceRole.DOCUMENTATION: 3,
        }
        buckets: Dict[int, List[Path]] = {}
        for path in ordered:
            role, _ = classify_source_role(
                path.as_posix(),
                read_text_prefix(path, 4 * 1024),
            )
            buckets.setdefault(priorities[role], []).append(path)

        selected: List[Path] = []
        for priority in sorted(buckets):
            candidates = buckets[priority]
            remaining = self.max_files - len(selected)
            if remaining <= 0:
                break
            if len(candidates) <= remaining:
                selected.extend(candidates)
                continue
            selected.extend(self._even_sample(candidates, remaining))
            break
        return sorted(selected)

    @staticmethod
    def _even_sample(files: List[Path], limit: int) -> List[Path]:
        if limit <= 0:
            return []
        if limit == 1:
            return files[:1]
        last = len(files) - 1
        return [
            files[round(index * last / (limit - 1))]
            for index in range(limit)
        ]

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
                line_end=fn.get("end_line", fn["line"]),
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
                line_end=cls.get("end_line", cls["line"]),
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

            if not name:
                continue

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

        nodes.sort(key=lambda node: node.line_start)
        for index, node in enumerate(nodes):
            next_line = (
                nodes[index + 1].line_start
                if index + 1 < len(nodes)
                else len(lines) + 1
            )
            node.line_end = max(node.line_start, next_line - 1)

        return nodes

    def _parse_classes(self, content: str, file_path: str,
                       language: Language, patterns: Dict) -> List[CodeNode]:
        """Extract class/struct/interface definitions."""
        nodes = []

        for pattern_key in ("class", "struct", "interface", "trait", "impl"):
            pat = patterns.get(pattern_key)
            if not pat:
                continue

            for match in pat.finditer(content):
                name = match.group("name")
                if not name:
                    continue

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
                       all_functions: Dict[str, List[CodeNode]],
                       patterns: Dict,
                       file_contents: Dict[str, str]) -> None:
        """For each function node, find calls to other known functions."""
        call_pattern = patterns.get("call")
        if not call_pattern:
            return

        func_nodes = [n for n in graph.nodes.values() if n.node_type == NodeType.FUNCTION]
        if not func_nodes:
            return

        for fn in func_nodes:
            content = file_contents.get(fn.file_path, "")
            if not content:
                continue

            lines = content.split("\n")
            start_index = max(0, fn.line_start - 1)
            end_line = fn.line_end or (fn.line_start + 199)
            end_index = min(len(lines), end_line, start_index + 200)
            scope = "\n".join(lines[start_index:end_index])

            called_names: Set[str] = set()
            for match in call_pattern.finditer(scope):
                name = match.group("name")
                if name and name != fn.name and name in all_functions:
                    called_names.add(name)

            for callee_name in called_names:
                candidates = all_functions[callee_name]
                same_file = [
                    candidate
                    for candidate in candidates
                    if candidate.file_path == fn.file_path
                ]
                if len(same_file) == 1:
                    callee = same_file[0]
                elif len(candidates) == 1:
                    callee = candidates[0]
                else:
                    # A name-only regex cannot safely distinguish duplicate
                    # definitions in different files.
                    continue
                graph.add_edge(CodeEdge(
                    source_id=fn.id,
                    target_id=callee.id,
                    edge_type=EdgeType.CALLS,
                ))

    def _resolve_imports(
        self,
        graph: CodeGraph,
        source_files: List[Path],
        language: Language,
        patterns: Dict,
        file_contents: Dict[str, str],
    ) -> None:
        """Create IMPORT edges between files and imported modules."""
        import_pattern = patterns.get("import")
        if not import_pattern:
            return

        for file_path in source_files:
            try:
                rel_path = file_path.relative_to(
                    Path(graph.meta["project_path"])
                ).as_posix()
            except (KeyError, ValueError):
                continue
            file_id = CodeNode.make_id(rel_path, rel_path, NodeType.FILE)
            content = file_contents.get(rel_path)
            if content is None:
                continue

            seen_modules: Set[str] = set()
            for match in import_pattern.finditer(content):
                module_names = self._import_names_from_match(language, match)
                for module_name in module_names:
                    if module_name in seen_modules:
                        continue
                    seen_modules.add(module_name)
                    # Create a MODULE node for the import
                    module_id = CodeNode.make_id(
                        rel_path, f"import:{module_name}", NodeType.IMPORT,
                    )
                    module_node = CodeNode(
                        id=module_id,
                        node_type=NodeType.IMPORT,
                        name=module_name,
                        file_path=rel_path,
                        line_start=content.count("\n", 0, match.start()) + 1,
                        language=language.value,
                    )
                    graph.add_node(module_node)
                    graph.add_edge(CodeEdge(
                        source_id=file_id,
                        target_id=module_id,
                        edge_type=EdgeType.IMPORTS,
                    ))

    @staticmethod
    def _import_names_from_match(language: Language, match: re.Match) -> List[str]:
        """Normalize one language-specific import match into module names."""
        groups = match.groupdict()

        if language == Language.PYTHON:
            module = groups.get("module")
            if module:
                return [module.strip()]

            names = groups.get("names") or ""
            modules = []
            for imported in names.split(","):
                # ``import package as alias`` still refers to ``package``.
                module_name = imported.strip().split(maxsplit=1)[0]
                if module_name:
                    modules.append(module_name)
            return modules

        if language == Language.GO:
            package = groups.get("pkg")
            if package:
                return [package.strip()]
            package_block = groups.get("packages") or ""
            return [name.strip() for name in re.findall(r'"([^"]+)"', package_block)]

        module = groups.get("module") or groups.get("pkg")
        return [module.strip()] if module and module.strip() else []

    # ── Tree-sitter language mapping ───────────────────────────────────

    @staticmethod
    def _check_treesitter() -> bool:
        """Check if tree-sitter is available (kept for backward compat)."""
        try:
            from smartbench.graph.tree_parser import is_available
            return is_available()
        except ImportError:
            return False
