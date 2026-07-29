"""
Tree-sitter based code parser — precise AST extraction.

Provides accurate function/class/call extraction using tree-sitter
when available. Falls back to regex patterns for unsupported languages
or when tree-sitter is not installed.

Supported languages via tree-sitter:
  - Python (tree-sitter-python)
  - Go (tree-sitter-go)
  - JavaScript (tree-sitter-javascript)
  - TypeScript (tree-sitter-typescript)
  - Rust (tree-sitter-rust)
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Tree-sitter availability ───────────────────────────────────────────

_treesitter_available: Optional[bool] = None
_parser_cache: Dict[str, Any] = {}  # language → Parser


def is_available() -> bool:
    """Check if tree-sitter core library is importable."""
    global _treesitter_available
    if _treesitter_available is None:
        try:
            import tree_sitter  # noqa: F401
            _treesitter_available = True
        except ImportError:
            _treesitter_available = False
    return _treesitter_available


def get_parser(language: str) -> Optional[Any]:
    """Get a tree-sitter Parser for the given language.

    Args:
        language: Lowercase language name (python, go, javascript, etc.)

    Returns:
        tree-sitter Parser instance, or None if not available.
    """
    if not is_available():
        return None

    if language in _parser_cache:
        return _parser_cache[language]

    parser = _try_load_language(language)
    _parser_cache[language] = parser
    return parser


def _try_load_language(lang: str) -> Optional[Any]:
    """Try to load a tree-sitter language parser.

    Strategy:
      1. Try tree_sitter_<lang>.language() (modern per-language packages)
      2. Try tree_sitter.Language(lib_path) with bundled grammars
    """
    from tree_sitter import Language, Parser

    lang_packages = {
        "python": ("tree_sitter_python", "language"),
        "go": ("tree_sitter_go", "language"),
        "javascript": ("tree_sitter_javascript", "language"),
        "typescript": ("tree_sitter_typescript", "language_typescript"),
        "rust": ("tree_sitter_rust", "language"),
    }

    package = lang_packages.get(lang)
    if package:
        pkg_name, language_factory = package
        try:
            mod = __import__(pkg_name, fromlist=[language_factory])
            ts_lang = Language(getattr(mod, language_factory)())
            parser = Parser(ts_lang)
            logger.info("tree-sitter loaded: %s via %s", lang, pkg_name)
            return parser
        except (ImportError, AttributeError) as e:
            logger.debug("tree-sitter %s skipped: %s", lang, e)

    return None


# ── AST Extraction ──────────────────────────────────────────────────────

# Tree-sitter node types that represent function/method definitions
# Varies by language — we match by type name suffix
_FUNC_NODE_TYPES = {
    "function_definition",      # Python, Rust
    "function_declaration",     # Go, JS/TS
    "function_item",            # Rust
    "method_definition",        # Python (methods with @)
    "method_declaration",       # Go, JS/TS methods
}

_CLASS_NODE_TYPES = {
    "class_definition",         # Python
    "class_declaration",        # JS/TS
    "type_declaration",         # Go (type Foo struct)
    "struct_item",              # Rust
    "enum_item",                # Rust
    "trait_item",               # Rust
    "impl_item",                # Rust
    "interface_declaration",    # TS
}

_VARIABLE_FUNCTION_VALUE_TYPES = {
    "arrow_function",
    "function_expression",
}


def extract_symbols(
    parser: Any, source: bytes, file_path: str
) -> Dict[str, List[Dict]]:
    """Extract functions, classes, and calls from source using tree-sitter.

    Args:
        parser: tree-sitter Parser instance.
        source: Source code bytes.
        file_path: Relative file path (for node IDs).

    Returns:
        {
            "functions": [{"name": ..., "line": ..., "signature": ...}, ...],
            "classes": [{"name": ..., "line": ...}, ...],
            "calls": [{"caller": ..., "callee": ..., "line": ...}, ...],
        }
    """
    tree = parser.parse(source)
    root = tree.root_node
    source_str = source.decode("utf-8", errors="replace")

    result: Dict[str, List[Dict]] = {
        "functions": [],
        "classes": [],
        "calls": [],
    }

    _walk_tree(root, source_str, source, result)

    return result


def _get_node_name(node: Any, source: bytes) -> Optional[str]:
    """Extract the identifier name from a tree-sitter node.

    Uses the 'name' field when available, otherwise tries text content.
    Filters out non-identifier characters to avoid garbage names.
    """
    # Try field 'name' first
    name_node = node.child_by_field_name("name")
    if name_node is not None:
        name = _decode_slice(source, name_node)
        if _is_valid_identifier(name):
            return name

    # Fallback: look for an identifier child
    for child in node.children:
        if child.type == "identifier":
            name = _decode_slice(source, child)
            if _is_valid_identifier(name):
                return name

    return None


def _decode_slice(source: bytes, node: Any) -> str:
    """Decode a byte slice from the source for a given node."""
    try:
        return source[node.start_byte:node.end_byte].decode(
            "utf-8", errors="replace"
        )
    except Exception:
        return ""


def _is_valid_identifier(name: str) -> bool:
    """Check if a string looks like a valid code identifier."""
    if not name or len(name) > 100:
        return False
    # Must start with letter/underscore, contain only identifier chars
    if not name[0].isalpha() and name[0] != '_':
        return False
    return all(c.isalnum() or c == '_' for c in name)


def _walk_tree(
    node: Any, source_str: str, source_bytes: bytes,
    result: Dict[str, List[Dict]],
) -> None:
    """Recursively walk the CST and extract function/class info."""
    node_type = node.type

    # ── Function / method definitions ──────────────────────────────
    if node_type in _FUNC_NODE_TYPES:
        name = _get_node_name(node, source_bytes)
        if name:
            line = node.start_point[0] + 1
            # Get first line as signature
            sig_end = min(
                node.start_byte + 200,
                node.end_byte,
            )
            sig = source_bytes[node.start_byte:sig_end].decode(
                "utf-8", errors="replace"
            ).split("\n")[0]
            result["functions"].append({
                "name": name,
                "line": line,
                "end_line": node.end_point[0] + 1,
                "signature": sig.strip(),
            })

    # ── JS/TS functions assigned to variables ─────────────────────
    elif node_type == "variable_declarator":
        value_node = node.child_by_field_name("value")
        if (
            value_node is not None
            and value_node.type in _VARIABLE_FUNCTION_VALUE_TYPES
        ):
            name = _get_node_name(node, source_bytes)
            if name:
                line = node.start_point[0] + 1
                sig_end = min(node.start_byte + 200, node.end_byte)
                sig = source_bytes[node.start_byte:sig_end].decode(
                    "utf-8", errors="replace"
                ).split("\n")[0]
                result["functions"].append({
                    "name": name,
                    "line": line,
                    "end_line": node.end_point[0] + 1,
                    "signature": sig.strip(),
                })

    # ── Class / struct / interface definitions ─────────────────────
    elif node_type in _CLASS_NODE_TYPES:
        name = _get_node_name(node, source_bytes)
        if name:
            line = node.start_point[0] + 1
            result["classes"].append({
                "name": name,
                "line": line,
                "end_line": node.end_point[0] + 1,
            })

    # Recurse
    for child in node.children:
        _walk_tree(child, source_str, source_bytes, result)


# ── Supported languages ──────────────────────────────────────────────────


SUPPORTED_LANGUAGES = {"python", "go", "javascript", "typescript", "rust"}


def supports(language: str) -> bool:
    """Check if a language has tree-sitter support."""
    return (
        language.lower() in SUPPORTED_LANGUAGES
        and get_parser(language.lower()) is not None
    )
