"""Static Python AST import graph scanner and file role classifier."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from sdd_parallel.models import FileRole

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

    def __init__(self, repo_root: Path, src_dir: str = "src") -> None:
        self.repo_root = repo_root.resolve()
        self.src_dir = src_dir
        self.src_root = self.repo_root / src_dir
        self._files: dict[str, FileMetadata] = {}
        self._module_to_path: dict[str, str] = {}

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

        # Second pass: resolve internal imports and decorator patterns
        for rel_path, tree in raw_trees.items():
            meta = self._files[rel_path]
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

        # Third pass: assign FileRole
        for rel_path, meta in self._files.items():
            if self._is_test_path(rel_path):
                meta.role = FileRole.STANDARD
            elif (
                meta.has_schema_version
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
