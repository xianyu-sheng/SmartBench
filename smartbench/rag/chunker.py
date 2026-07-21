"""
CodeChunker — language-aware code chunking.

Strategy:
  1. Reuse graph nodes for function/class boundaries (already parsed, fast)
  2. Extract exact code for each node using line_start/line_end
  3. For inter-function gaps: split on blank-line boundaries
  4. For files without parseable functions: line-count chunking with overlap
"""

import re
from pathlib import Path
from typing import Dict, List, Optional, Set

from smartbench.graph.schema import CodeGraph, CodeNode, NodeType
from smartbench.rag import Chunk

# Directories and files to skip during indexing
EXCLUDED_DIRS: Set[str] = {
    '.git', 'node_modules', '__pycache__', 'target', 'build',
    'vendor', '.venv', 'venv', 'dist', '.idea', '.vscode', 'obj',
    '.smartbench', '.claude', '.pytest_cache',
}

EXCLUDED_SUFFIXES: Set[str] = {
    '.pyc', '.pyo', '.so', '.dll', '.exe', '.o', '.a', '.lib',
    '.png', '.jpg', '.jpeg', '.gif', '.ico', '.svg', '.mp4',
    '.pdf', '.zip', '.tar', '.gz', '.whl', '.egg',
    '.db', '.sqlite', '.sqlite3',
}

EXCLUDED_PATTERNS: List[re.Pattern] = [
    re.compile(p) for p in [
        r'.*\.min\.js$', r'.*\.min\.css$', r'.*\.chunk\.js$',
        r'.*/dist/.*', r'.*/build/.*', r'.*/vendor/.*',
        r'.*/node_modules/.*', r'.*/\.git/.*',
        r'package-lock\.json$', r'yarn\.lock$', r'poetry\.lock$',
        r'Pipfile\.lock$', r'.*\.lock$',
    ]
]

# Code file extensions that should be indexed
CODE_EXTENSIONS: Set[str] = {
    '.py', '.go', '.rs', '.cpp', '.cc', '.cxx', '.c', '.h', '.hpp',
    '.java', '.kt', '.kts', '.js', '.jsx', '.ts', '.tsx',
    '.rb', '.swift', '.cs', '.zig', '.m', '.mm',
    '.vue', '.svelte', '.scala', '.clj', '.ex', '.exs',
    '.yaml', '.yml', '.toml', '.json', '.xml',
    '.sh', '.bash', '.ps1', '.bat',
    '.sql', '.graphql', '.proto',
    '.md', '.rst', '.txt',
}


def _is_excluded(path: Path) -> bool:
    """Check if a path should be excluded from indexing."""
    parts = set(path.parts)
    if parts & EXCLUDED_DIRS:
        return True
    if path.suffix.lower() in EXCLUDED_SUFFIXES:
        return True
    path_str = str(path).replace('\\', '/')
    for pat in EXCLUDED_PATTERNS:
        if pat.match(path_str):
            return True
    return False


def _should_index(path: Path) -> bool:
    """Check if a file should be indexed."""
    if _is_excluded(path):
        return False
    if path.suffix.lower() in CODE_EXTENSIONS:
        return True
    # Also index files without extensions that look like code
    if not path.suffix:
        try:
            content = path.read_text(encoding='utf-8', errors='ignore')
            # Heuristic: if it looks like a script or config
            if content.strip().startswith('#!') or content.strip().startswith('/*'):
                return True
        except Exception:
            pass
    return False


class CodeChunker:
    """
    Language-aware code chunker.

    Chunks code at function/class boundaries (using graph node info)
    with overlap for continuous coverage.
    """

    def __init__(self, chunk_size: int = 200, overlap: int = 30,
                 max_chunk_chars: int = 3000):
        """
        Args:
            chunk_size: Target lines per chunk (for non-structured files)
            overlap: Overlapping lines between adjacent chunks
            max_chunk_chars: Maximum characters per chunk (truncation threshold)
        """
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.max_chunk_chars = max_chunk_chars

    # ── Public API ──────────────────────────────────────────────────────

    def chunk_project(self, project_path: str,
                      graph: Optional[CodeGraph] = None) -> List[Chunk]:
        """
        Chunk entire project.

        - Uses graph nodes for function/class boundaries (fast, already parsed)
        - For files not in graph, does file-level chunking
        - Adds file header chunks (imports, module docstrings)

        Args:
            project_path: Root directory of the project
            graph: Optional CodeGraph for structural boundary detection

        Returns:
            List of Chunk objects ready for embedding
        """
        chunks: List[Chunk] = []
        root = Path(project_path)

        # Build a lookup of file_path -> list of function/class nodes
        file_nodes: Dict[str, List[CodeNode]] = {}
        if graph:
            for node in graph.nodes.values():
                if node.node_type in (NodeType.FUNCTION, NodeType.CLASS):
                    file_nodes.setdefault(node.file_path, []).append(node)

        # Discover all indexable files
        discovered = self._discover_files(root)

        for rel_path, full_path in discovered:
            try:
                content = full_path.read_text(encoding='utf-8', errors='ignore')
            except Exception:
                continue

            if not content.strip():
                continue

            lang = self._guess_language(rel_path)
            nodes = file_nodes.get(rel_path, [])

            if nodes:
                # Structured chunking: use graph node boundaries
                file_chunks = self._chunk_structured(
                    rel_path, lang, content, nodes
                )
            else:
                # Unstructured: line-count chunking
                file_chunks = self._chunk_unstructured(
                    rel_path, lang, content
                )

            chunks.extend(file_chunks)

        return chunks

    def chunk_file(self, file_path: str, language: str,
                   content: str) -> List[Chunk]:
        """
        Parse a single file into chunks.

        Args:
            file_path: Relative file path
            language: Language identifier string
            content: Full file content

        Returns:
            List of Chunk objects
        """
        # Try to find function/class boundaries with regex
        func_ranges = self._find_function_ranges(content, language)
        if func_ranges:
            return self._chunk_from_ranges(file_path, language, content, func_ranges)
        return self._chunk_unstructured(file_path, language, content)

    # ── File discovery ──────────────────────────────────────────────────

    def _discover_files(self, root: Path) -> List[tuple]:
        """
        Discover all indexable files in the project.

        Returns:
            List of (relative_path, absolute_path) tuples
        """
        files = []
        for path in root.rglob('*'):
            if path.is_file() and _should_index(path):
                try:
                    rel = path.relative_to(root)
                    files.append((str(rel).replace('\\', '/'), path))
                except ValueError:
                    pass
        return files

    # ── Chunking strategies ─────────────────────────────────────────────

    def _chunk_structured(self, file_path: str, language: str,
                          content: str, nodes: List[CodeNode]) -> List[Chunk]:
        """
        Chunk using graph node boundaries.
        One chunk per function/class, with file header chunk.
        """
        lines = content.split('\n')
        chunks: List[Chunk] = []
        covered: set = set()

        # Sort nodes by line_start
        sorted_nodes = sorted(nodes, key=lambda n: n.line_start)

        for node in sorted_nodes:
            start = max(0, node.line_start - 1)  # 0-indexed
            end = min(len(lines), node.line_end) if node.line_end > 0 else start + 50
            if end <= start:
                end = min(len(lines), start + 50)

            chunk_lines = lines[start:end]
            chunk_text = '\n'.join(chunk_lines)
            if not chunk_text.strip():
                continue

            # Build header for embedding context
            header = (f"# file: {file_path}  "
                      f"{node.node_type.value}: {node.name}  "
                      f"(line {node.line_start}-{node.line_end})\n")
            full_text = header + chunk_text

            if len(full_text) > self.max_chunk_chars:
                full_text = full_text[:self.max_chunk_chars] + "\n# ... (truncated)"

            chunks.append(Chunk(
                id=Chunk.make_id(file_path, node.line_start, node.line_end),
                content=full_text,
                file_path=file_path,
                start_line=node.line_start,
                end_line=node.line_end,
                language=language,
                node_type=node.node_type.value,
                node_name=node.name,
                metadata={
                    "signature": node.properties.get("signature", ""),
                    "visibility": node.properties.get("visibility", ""),
                },
            ))

            for i in range(start, end):
                covered.add(i)

        # Add file header chunk (first 30 lines if not covered by nodes)
        header_end = min(30, len(lines))
        header_covered = all(i in covered for i in range(header_end))
        if not header_covered and header_end > 0:
            header_text = '\n'.join(lines[:header_end])
            if header_text.strip():
                chunks.append(Chunk(
                    id=Chunk.make_id(file_path, 1, header_end),
                    content=f"# file: {file_path}  (header/imports)\n{header_text}",
                    file_path=file_path,
                    start_line=1,
                    end_line=header_end,
                    language=language,
                    node_type="file_header",
                    node_name=file_path,
                ))

        return chunks

    def _chunk_unstructured(self, file_path: str, language: str,
                            content: str) -> List[Chunk]:
        """
        Chunk a file without known structure.
        Splits at blank-line boundaries, then merges small chunks.
        """
        lines = content.split('\n')

        # For small files (< 2x chunk_size), use one chunk
        if len(lines) <= self.chunk_size * 2:
            text = '\n'.join(lines)
            if len(text) > self.max_chunk_chars:
                text = text[:self.max_chunk_chars] + "\n# ... (truncated)"
            return [Chunk(
                id=Chunk.make_id(file_path, 1, len(lines)),
                content=f"# file: {file_path}  (full file)\n{text}",
                file_path=file_path,
                start_line=1,
                end_line=len(lines),
                language=language,
                node_type="file",
                node_name=file_path,
            )] if text.strip() else []

        # For large files, split into chunks with overlap
        chunks: List[Chunk] = []
        i = 0
        while i < len(lines):
            end = min(i + self.chunk_size, len(lines))
            chunk_lines = lines[i:end]
            text = '\n'.join(chunk_lines)
            if text.strip():
                if len(text) > self.max_chunk_chars:
                    text = text[:self.max_chunk_chars] + "\n# ... (truncated)"
                chunks.append(Chunk(
                    id=Chunk.make_id(file_path, i + 1, end),
                    content=f"# file: {file_path}  lines {i+1}-{end}\n{text}",
                    file_path=file_path,
                    start_line=i + 1,
                    end_line=end,
                    language=language,
                    node_type="block",
                    node_name=f"{file_path}:{i+1}",
                ))
            i += self.chunk_size - self.overlap
            if i >= len(lines):
                break

        return chunks

    def _chunk_from_ranges(self, file_path: str, language: str,
                           content: str,
                           ranges: List[tuple]) -> List[Chunk]:
        """
        Chunk from pre-computed (start_line, end_line, name, type) ranges.
        """
        lines = content.split('\n')
        chunks: List[Chunk] = []

        for start, end, name, ntype in ranges:
            chunk_lines = lines[max(0, start-1):min(len(lines), end)]
            text = '\n'.join(chunk_lines)
            if not text.strip():
                continue
            header = f"# file: {file_path}  {ntype}: {name}  (line {start}-{end})\n"
            full_text = header + text
            if len(full_text) > self.max_chunk_chars:
                full_text = full_text[:self.max_chunk_chars] + "\n# ... (truncated)"
            chunks.append(Chunk(
                id=Chunk.make_id(file_path, start, end),
                content=full_text,
                file_path=file_path,
                start_line=start,
                end_line=end,
                language=language,
                node_type=ntype,
                node_name=name,
            ))

        return chunks

    # ── Helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _guess_language(file_path: str) -> str:
        """Guess language from file extension."""
        ext_map = {
            '.py': 'python', '.go': 'go', '.rs': 'rust',
            '.cpp': 'cpp', '.cc': 'cpp', '.cxx': 'cpp', '.c': 'c',
            '.h': 'c', '.hpp': 'cpp',
            '.java': 'java', '.kt': 'kotlin', '.kts': 'kotlin',
            '.js': 'javascript', '.jsx': 'javascript',
            '.ts': 'typescript', '.tsx': 'typescript',
            '.rb': 'ruby', '.swift': 'swift', '.cs': 'csharp',
            '.zig': 'zig',
            '.yaml': 'yaml', '.yml': 'yaml', '.toml': 'toml',
            '.json': 'json', '.xml': 'xml',
            '.sh': 'shell', '.bash': 'shell', '.ps1': 'powershell',
            '.sql': 'sql', '.md': 'markdown', '.txt': 'text',
        }
        suffix = Path(file_path).suffix.lower()
        return ext_map.get(suffix, 'unknown')

    @staticmethod
    def _find_function_ranges(content: str, language: str) -> List[tuple]:
        """
        Find function/class ranges using heuristic regex patterns.

        Returns:
            List of (start_line, end_line, name, type)
        """
        # Simple patterns for the most common languages
        patterns = {
            'python': [
                (r'^\s*def\s+(\w+)\s*\(', 'function'),
                (r'^\s*class\s+(\w+)\s*[(:]', 'class'),
            ],
            'go': [
                (r'^func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)\s*\(', 'function'),
                (r'^type\s+(\w+)\s+struct\s*\{', 'struct'),
            ],
            'rust': [
                (r'^\s*(?:pub\s+)?fn\s+(\w+)\s*[<\(]', 'function'),
                (r'^\s*(?:pub\s+)?struct\s+(\w+)', 'struct'),
            ],
            'javascript': [
                (r'(?:^|\s)function\s+(\w+)\s*\(', 'function'),
                (r'(?:^|\s)class\s+(\w+)', 'class'),
                (r'(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(', 'function'),
                (r'(\w+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>', 'function'),
            ],
            'typescript': [
                (r'(?:^|\s)function\s+(\w+)\s*\(', 'function'),
                (r'(?:^|\s)class\s+(\w+)', 'class'),
                (r'(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(', 'function'),
                (r'(\w+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>', 'function'),
            ],
            'java': [
                (r'(?:public|private|protected|static|\s)+[\w<>[\]]+\s+(\w+)\s*\(', 'function'),
                (r'(?:public\s+)?class\s+(\w+)', 'class'),
            ],
        }

        ranges = []
        lines = content.split('\n')
        lang_patterns = patterns.get(language, [])

        for i, line in enumerate(lines, 1):
            for pattern, ntype in lang_patterns:
                m = re.search(pattern, line)
                if m:
                    name = m.group(1)
                    # Estimate end line (next blank line or dedent)
                    end = i + 1
                    for j in range(i, min(i + 100, len(lines))):
                        if j >= len(lines):
                            break
                        if lines[j].strip() == '' and j > i + 3:
                            end = j
                            break
                    ranges.append((i, end, name, ntype))

        return ranges
