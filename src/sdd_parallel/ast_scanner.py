"""Static Python AST import graph scanner and file role classifier."""

from __future__ import annotations

import ast
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from sdd_parallel.models import (
    DEFAULT_CONTRACT_GLOBS,
    FileRole,
    TaskNode,
    is_contract_file,
)

_SPINE_KEYWORDS = ("SCHEMA_VERSION", "auto_migrate", "_migrate_v")
_ADDITIVE_DECORATORS = (
    "command",
    "tool",
    "get",
    "post",
    "put",
    "delete",
    "patch",
    "include_router",
    "register",
    "route",
)
_IGNORED_SCAN_DIRS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".worktrees",
    ".parallel",
    ".tmp",
    "_bmad",
    "_bmad-output",
    "build",
    "dist",
    "phases",
    "docs",
}
_EXCLUDED_SYMBOLS = frozenset(
    {
        "__init__",
        "__str__",
        "__repr__",
        "__eq__",
        "__call__",
        "__enter__",
        "__exit__",
        "get",
        "set",
        "update",
        "delete",
        "save",
        "run",
        "main",
        "start",
        "stop",
        "execute",
        "close",
        "read",
        "write",
        "open",
        "clear",
        "pop",
        "copy",
        "setUp",
        "tearDown",
        "setUpClass",
        "tearDownClass",
        "True",
        "False",
        "None",
    }
)


@dataclass(slots=True)
class FileMetadata:
    """Static AST analysis metadata for a single source file."""

    rel_path: str
    module_name: str
    role: FileRole = FileRole.STANDARD
    imports: set[str] = field(default_factory=set)
    imported_by: set[str] = field(default_factory=set)
    has_schema_version: bool = False
    has_registry_decorators: bool = False


class ASTHotspotScanner:
    """Builds internal Python import graph and classifies Spine vs Additive Registry files."""

    def __init__(
        self,
        repo_root: Path,
        src_dir: str = "src",
        contract_globs: Sequence[str] | None = None,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.src_dir = src_dir
        self.src_root = self.repo_root / src_dir
        self.contract_globs = (
            tuple(contract_globs) if contract_globs is not None else DEFAULT_CONTRACT_GLOBS
        )
        self._files: dict[str, FileMetadata] = {}
        self._module_to_path: dict[str, str] = {}
        self._symbols_to_files: dict[str, set[str]] = {}

    def _path_to_module(self, py_file: Path) -> str:
        if self.src_root.is_dir() and py_file.is_relative_to(self.src_root):
            base = self.src_root
        else:
            base = self.repo_root
        rel = py_file.relative_to(base).with_suffix("")
        parts = list(rel.parts)
        if parts and parts[-1] == "__init__":
            parts.pop()
        return ".".join(parts)

    def scan(self) -> dict[str, FileMetadata]:
        """Scan all .py files under src_dir, compute in-degree, and classify roles."""
        self._files.clear()
        self._module_to_path.clear()
        self._symbols_to_files.clear()

        if self.src_root.is_dir():
            scan_root = self.src_root
        elif self.src_dir == "src" and self.repo_root.is_dir():
            scan_root = self.repo_root
        else:
            return self._files

        py_files: list[Path] = []
        for py_file in sorted(scan_root.rglob("*.py")):
            try:
                parts = py_file.relative_to(self.repo_root).parts
            except ValueError:
                continue
            if any(part in _IGNORED_SCAN_DIRS or part.endswith(".egg-info") for part in parts):
                continue
            py_files.append(py_file)

        raw_trees: dict[str, ast.Module] = {}

        for py_file in py_files:
            rel_path = py_file.relative_to(self.repo_root).as_posix()
            mod_name = self._path_to_module(py_file)
            try:
                source = py_file.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=rel_path)
            except (OSError, SyntaxError):
                continue

            has_spine = any(kw in source for kw in _SPINE_KEYWORDS)
            meta = FileMetadata(
                rel_path=rel_path,
                module_name=mod_name,
                has_schema_version=has_spine,
            )
            self._files[rel_path] = meta
            if mod_name:
                self._module_to_path[mod_name] = rel_path
            raw_trees[rel_path] = tree

        # Second pass: resolve internal imports, decorator patterns, and defined symbols
        for rel_path, tree in raw_trees.items():
            meta = self._files[rel_path]
            is_test = self._is_test_path(rel_path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        target_rel = self._resolve_module_to_path(alias.name)
                        if target_rel and target_rel != rel_path:
                            meta.imports.add(target_rel)
                            self._files[target_rel].imported_by.add(rel_path)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    target_rel = self._resolve_module_to_path(node.module)
                    if target_rel and target_rel != rel_path:
                        meta.imports.add(target_rel)
                        self._files[target_rel].imported_by.add(rel_path)
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for dec in node.decorator_list:
                        if self._is_registry_decorator(dec):
                            meta.has_registry_decorators = True
                    if not is_test and node.name not in _EXCLUDED_SYMBOLS and len(node.name) >= 3:
                        self._symbols_to_files.setdefault(node.name, set()).add(rel_path)
                elif isinstance(node, ast.ClassDef):
                    if not is_test and node.name not in _EXCLUDED_SYMBOLS and len(node.name) >= 3:
                        self._symbols_to_files.setdefault(node.name, set()).add(rel_path)
                elif isinstance(node, ast.Assign) and not is_test:
                    for target in node.targets:
                        if (
                            isinstance(target, ast.Name)
                            and target.id.isupper()
                            and len(target.id) >= 2
                            and target.id not in _EXCLUDED_SYMBOLS
                        ):
                            self._symbols_to_files.setdefault(target.id, set()).add(rel_path)
                elif isinstance(node, ast.AnnAssign) and not is_test:
                    if (
                        isinstance(node.target, ast.Name)
                        and node.target.id.isupper()
                        and len(node.target.id) >= 2
                        and node.target.id not in _EXCLUDED_SYMBOLS
                    ):
                        self._symbols_to_files.setdefault(node.target.id, set()).add(rel_path)

        # Third pass: assign FileRole
        for rel_path, meta in self._files.items():
            if self._is_test_path(rel_path):
                meta.role = FileRole.STANDARD
            elif (
                is_contract_file(rel_path, self.contract_globs)
                or meta.has_schema_version
                or Path(rel_path).name in {"models.py", "schema.py"}
                or len(meta.imported_by) >= 5
            ):
                meta.role = FileRole.SPINE
            elif (
                meta.has_registry_decorators
                or Path(rel_path).name in {"cli.py", "__init__.py"}
                or rel_path.endswith("/mcp/server.py")
            ):
                meta.role = FileRole.ADDITIVE_REGISTRY
            else:
                meta.role = FileRole.STANDARD

        return self._files

    @staticmethod
    def _is_test_path(rel_posix: str) -> bool:
        parts = rel_posix.strip("/").split("/")
        filename = parts[-1] if parts else ""
        return "tests" in parts or filename.startswith("test_") or filename.endswith("_test.py")

    def _resolve_module_to_path(self, mod_name: str) -> str | None:
        if mod_name in self._module_to_path:
            return self._module_to_path[mod_name]
        parts = mod_name.split(".")
        while parts:
            candidate = ".".join(parts)
            if candidate in self._module_to_path:
                return self._module_to_path[candidate]
            parts.pop()
        return None

    @staticmethod
    def _is_registry_decorator(node: ast.expr) -> bool:
        target = node.func if isinstance(node, ast.Call) else node
        if isinstance(target, ast.Attribute):
            return target.attr in _ADDITIVE_DECORATORS
        if isinstance(target, ast.Name):
            return target.id in _ADDITIVE_DECORATORS
        return False

    def classify_file(self, rel_path: str) -> FileRole:
        """Return architectural role of a relative file path."""
        normalized = rel_path.replace("\\", "/").lstrip("./")
        if self._is_test_path(normalized):
            return FileRole.STANDARD
        if normalized in self._files:
            return self._files[normalized].role
        if is_contract_file(normalized, self.contract_globs):
            return FileRole.SPINE
        filename = Path(normalized).name
        if filename in {"schema.py", "models.py"}:
            return FileRole.SPINE
        if filename in {"cli.py", "__init__.py"} or normalized.endswith("/mcp/server.py"):
            return FileRole.ADDITIVE_REGISTRY
        return FileRole.STANDARD

    def get_importers(self, rel_path: str) -> set[str]:
        """Return all files that directly import rel_path."""
        normalized = rel_path.replace("\\", "/")
        meta = self._files.get(normalized)
        return set(meta.imported_by) if meta else set()

    def get_symbol_definitions(self) -> dict[str, set[str]]:
        """Return dictionary mapping discovered symbol names to defining relative file paths."""
        return {k: set(v) for k, v in self._symbols_to_files.items()}

    def find_symbol_files(self, symbol: str) -> set[str]:
        """Find relative file paths defining the given symbol name."""
        return set(self._symbols_to_files.get(symbol, ()))

    def resolve_symbols_in_text(self, text: str) -> set[str]:
        """Extract identifier tokens from text and match against known code symbols."""
        if not text or not self._symbols_to_files:
            return set()
        tokens = set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", text))
        candidate_files: set[str] = set()
        for tok in tokens:
            if tok in self._symbols_to_files and tok not in _EXCLUDED_SYMBOLS:
                candidate_files.update(self._symbols_to_files[tok])
        return candidate_files

    def link_task_symbols(
        self,
        task: TaskNode,
        extra_text: str = "",
    ) -> list[str]:
        """Scan task description/title/notes for symbol references and enrich task files.

        Invariant A (Context-Read vs Target-Write Disambiguation):
        - If task.write_files is initially empty: resolved symbols identify target files for
          symbol-only tasks and are added to task.write_files. Symbols found in task.title
          take precedence over context symbols in task.description.
        - If task.write_files is already non-empty: resolved symbols from other files represent
          context-read references (input dependencies). They are added to task.impact_files
          to enforce Type-2 semantic collision detection (W_A ∩ I_B != ∅) without falsely
          claiming Spine Locks or polluting task.write_files.
        """
        if not self._symbols_to_files:
            self.scan()

        newly_added: list[str] = []

        # 1. If write_files is empty, prioritize title symbols as primary write targets
        if not task.write_files and task.title:
            found_title_files = self.resolve_symbols_in_text(task.title)
            for file_path in sorted(found_title_files):
                norm = file_path.replace("\\", "/")
                if norm not in task.write_files:
                    task.write_files.append(norm)
                    newly_added.append(norm)

        combined_text = "\n".join(
            filter(
                None,
                [
                    task.title,
                    task.description,
                    " ".join(task.shared_function_notes.values()),
                    extra_text,
                ],
            )
        )
        found_files = self.resolve_symbols_in_text(combined_text)
        is_write_empty = not task.write_files

        for file_path in sorted(found_files):
            norm = file_path.replace("\\", "/")
            if is_write_empty:
                if norm not in task.write_files:
                    task.write_files.append(norm)
                    newly_added.append(norm)
            else:
                # Context-Read symbol: enrich impact_files for semantic collision checking
                if norm not in task.write_files and norm not in task.impact_files:
                    task.impact_files.append(norm)
                    newly_added.append(norm)

        return newly_added

    def link_tasks(
        self,
        tasks: Sequence[TaskNode],
        extra_texts: dict[str, str] | None = None,
    ) -> dict[str, list[str]]:
        """Enrich write_files across a collection of tasks from mentioned AST symbols."""
        if not self._symbols_to_files:
            self.scan()
        texts = extra_texts or {}
        linked_map: dict[str, list[str]] = {}
        for task in tasks:
            extra = texts.get(task.id, "")
            added = self.link_task_symbols(task, extra_text=extra)
            if added:
                linked_map[task.id] = added
        return linked_map
