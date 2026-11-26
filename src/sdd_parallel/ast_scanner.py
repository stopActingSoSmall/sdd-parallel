"""Static Python AST import graph scanner and file role classifier."""

from __future__ import annotations

import ast
import builtins
import re
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from sdd_parallel.models import (
    DEFAULT_CONTRACT_GLOBS,
    FileRole,
    TaskNode,
    is_contract_file,
    normalize_rel_path,
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
UNRESOLVED_CALL = "<unresolved-call>"
_UNKNOWN_RECEIVER = "<unknown-receiver>"
_ROUTER_RECEIVER_HINTS: tuple[str, ...] = (
    "app",
    "router",
    "api",
    "cli",
    "bp",
    "blueprint",
    "mcp",
    "server",
    "subparsers",
    "registry",
)
_UNAMBIGUOUS_ROUTE_ATTRS: tuple[str, ...] = ("include_router", "route")
_NEGATED_CONSTRAINT_RE = re.compile(
    r"(?i)\b(?:do\s+not|don't|must\s+not|never|avoid|without\s+(?:changing|modifying|touching)|"
    r"keep\s+existing|preserve|unchanged|read[- ]only|"
    r"не\s+менять|не\s+трогать|без\s+изменения|не\s+изменять|сохранить)\b"
)
_READONLY_SECTION_HEADER_RE = re.compile(
    r"(?i)^#{1,6}\s+.*(?:forbidden|read[- ]only|out\s+of\s+scope|unchanged|не\s+трогать)"
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


def resolve_import_from_targets(
    node: ast.ImportFrom,
    rel_path: str,
    src_dir: str,
    resolve_module_fn: Callable[[str], str | None],
) -> set[str]:
    """Resolve internal target file paths for an ast.ImportFrom node (absolute or relative)."""
    norm_rel = normalize_rel_path(rel_path)
    stem_path = norm_rel[:-3] if norm_rel.endswith(".py") else norm_rel
    parts = [p for p in stem_path.split("/") if p]
    is_pkg_init = bool(parts and parts[-1] == "__init__")
    if is_pkg_init:
        parts.pop()
    norm_src = normalize_rel_path(src_dir)
    if norm_src and parts and parts[0] == norm_src:
        mod_parts = parts[1:]
    else:
        mod_parts = parts
    pkg_parts = mod_parts if is_pkg_init else mod_parts[:-1]

    base_mod = (node.module or "").strip(".")
    if node.level > 0:
        up = node.level - 1
        if up > len(pkg_parts):
            return set()
        anchor = pkg_parts[: len(pkg_parts) - up]
        rel_mod_parts = [p for p in base_mod.split(".") if p] if base_mod else []
        base_mod = ".".join(anchor + rel_mod_parts)
    elif not base_mod:
        return set()

    targets: set[str] = set()
    for alias in node.names:
        resolved: str | None = None
        if alias.name != "*":
            candidate = f"{base_mod}.{alias.name}" if base_mod else alias.name
            resolved = resolve_module_fn(candidate)
        if resolved is None and base_mod:
            resolved = resolve_module_fn(base_mod)
        if resolved and resolved != norm_rel:
            targets.add(resolved)
    return targets


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
    defined_functions: set[str] = field(default_factory=set)
    intra_file_calls: dict[str, set[str]] = field(default_factory=dict)


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
            rel_path = normalize_rel_path(py_file.relative_to(self.repo_root).as_posix())
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
            meta.defined_functions, meta.intra_file_calls = self._index_intra_file_calls(tree)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        target_rel = self._resolve_module_to_path(alias.name)
                        if target_rel and target_rel != rel_path:
                            meta.imports.add(target_rel)
                            self._files[target_rel].imported_by.add(rel_path)
                elif isinstance(node, ast.ImportFrom):
                    for target_rel in resolve_import_from_targets(
                        node=node,
                        rel_path=rel_path,
                        src_dir=self.src_dir,
                        resolve_module_fn=self._resolve_module_to_path,
                    ):
                        if target_rel in self._files and target_rel != rel_path:
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
    def _extract_decorator_receiver_name(value: ast.expr) -> str:
        if isinstance(value, ast.Name):
            return value.id.lower()
        if isinstance(value, ast.Attribute):
            return value.attr.lower()
        if isinstance(value, ast.Call):
            return ASTHotspotScanner._extract_decorator_receiver_name(value.func)
        return ""

    @staticmethod
    def _is_registry_decorator(node: ast.expr) -> bool:
        target = node.func if isinstance(node, ast.Call) else node
        if isinstance(target, ast.Attribute) and target.attr in _ADDITIVE_DECORATORS:
            if target.attr in _UNAMBIGUOUS_ROUTE_ATTRS:
                return True
            receiver = ASTHotspotScanner._extract_decorator_receiver_name(target.value)
            receiver_parts = {part for part in re.split(r"[_\d]+", receiver) if part}
            return bool(receiver_parts & set(_ROUTER_RECEIVER_HINTS))
        return False

    @staticmethod
    def _index_intra_file_calls(tree: ast.Module) -> tuple[set[str], dict[str, set[str]]]:
        """Extract top-level functions/methods and intra-file call edges."""
        defined_functions: set[str] = set()
        raw_calls: dict[str, set[str]] = {}
        class_methods: dict[str, set[str]] = {}

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defined_functions.add(node.name)
            elif isinstance(node, ast.ClassDef):
                methods: set[str] = set()
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        defined_functions.add(f"{node.name}.{item.name}")
                        methods.add(item.name)
                class_methods[node.name] = methods

        class_names = set(class_methods)
        resolvable_names: set[str] = set(dir(builtins)) | class_names
        for top in tree.body:
            if isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef)):
                resolvable_names.add(top.name)
            elif isinstance(top, ast.Import):
                resolvable_names.update((a.asname or a.name).split(".")[0] for a in top.names)
            elif isinstance(top, ast.ImportFrom):
                resolvable_names.update(a.asname or a.name for a in top.names)

        class_bases: dict[str, set[str]] = {
            n.name: {b.id for b in n.bases if isinstance(b, ast.Name) and b.id in class_names}
            for n in tree.body
            if isinstance(n, ast.ClassDef)
        }

        def _resolve_method(cls_name: str, attr_name: str) -> set[str]:
            """Resolve method over same-file ancestors and descendants (overrides)."""
            ancestors: set[str] = set()
            stack = [cls_name]
            while stack:
                for base in class_bases.get(stack.pop(), ()):
                    if base not in ancestors:
                        ancestors.add(base)
                        stack.append(base)
            descendants: set[str] = set()
            stack = [cls_name]
            while stack:
                cur = stack.pop()
                for child, bases in class_bases.items():
                    if cur in bases and child not in descendants:
                        descendants.add(child)
                        stack.append(child)
            related = {cls_name} | ancestors | descendants
            return {f"{c}.{attr_name}" for c in related if attr_name in class_methods.get(c, ())}

        def _method_name_fallback(attr_name: str) -> set[str]:
            return {f"{c}.{attr_name}" for c, ms in class_methods.items() if attr_name in ms}

        def _extract_local_class_types(
            body_node: ast.FunctionDef | ast.AsyncFunctionDef,
        ) -> dict[str, set[str]]:
            local_types: dict[str, set[str]] = {}
            all_args = (
                list(body_node.args.posonlyargs)
                + list(body_node.args.args)
                + list(body_node.args.kwonlyargs)
            )
            for arg in all_args:
                if isinstance(arg.annotation, ast.Name) and arg.annotation.id in class_names:
                    local_types.setdefault(arg.arg, set()).add(arg.annotation.id)
            for sub in ast.walk(body_node):
                if isinstance(sub, ast.Assign):
                    known = (
                        sub.value.func.id
                        if isinstance(sub.value, ast.Call)
                        and isinstance(sub.value.func, ast.Name)
                        and sub.value.func.id in class_names
                        else _UNKNOWN_RECEIVER
                    )
                    for target in sub.targets:
                        if isinstance(target, ast.Name):
                            local_types.setdefault(target.id, set()).add(known)
                elif isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name):
                    if (
                        isinstance(sub.value, ast.Call)
                        and isinstance(sub.value.func, ast.Name)
                        and sub.value.func.id in class_names
                    ):
                        local_types.setdefault(sub.target.id, set()).add(sub.value.func.id)
                    if isinstance(sub.annotation, ast.Name) and sub.annotation.id in class_names:
                        local_types.setdefault(sub.target.id, set()).add(sub.annotation.id)
            return local_types

        def _collect_calls(
            body_node: ast.FunctionDef | ast.AsyncFunctionDef,
            class_name: str | None = None,
        ) -> set[str]:
            found: set[str] = set()
            local_types = _extract_local_class_types(body_node)
            nested_defs = {
                n.name
                for n in ast.walk(body_node)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n is not body_node
            }
            for sub in ast.walk(body_node):
                if not isinstance(sub, ast.Call):
                    continue
                if isinstance(sub.func, ast.Name):
                    callee = sub.func.id
                    found.add(callee)
                    if callee in class_names:
                        found.add(f"{callee}.__init__")
                        found.add(f"{callee}.__new__")
                    elif callee not in resolvable_names and callee not in nested_defs:
                        # Callable local/parameter/alias: target unknowable statically.
                        found.add(UNRESOLVED_CALL)
                elif isinstance(sub.func, ast.Attribute):
                    attr_name = sub.func.attr
                    recv = sub.func.value
                    if isinstance(recv, ast.Name):
                        owner = recv.id
                        if class_name and owner in {"self", "cls", class_name}:
                            found |= _resolve_method(class_name, attr_name)
                        elif owner in local_types:
                            for resolved_cls in local_types[owner]:
                                if resolved_cls == _UNKNOWN_RECEIVER:
                                    found |= _method_name_fallback(attr_name)
                                else:
                                    found |= _resolve_method(resolved_cls, attr_name)
                        elif owner in class_names:
                            found |= _resolve_method(owner, attr_name)
                        else:
                            found.add(f"{owner}.{attr_name}")
                            found |= _method_name_fallback(attr_name)
                    elif (
                        isinstance(recv, ast.Call)
                        and isinstance(recv.func, ast.Name)
                        and recv.func.id in class_names
                    ):
                        found |= _resolve_method(recv.func.id, attr_name)
                    else:
                        found |= _method_name_fallback(attr_name)
                else:
                    # Subscript/call/lambda callee: target unknowable statically.
                    found.add(UNRESOLVED_CALL)
            return found

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                raw_calls[node.name] = _collect_calls(node)
            elif isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        fn_key = f"{node.name}.{item.name}"
                        raw_calls[fn_key] = _collect_calls(item, class_name=node.name)

        intra_file_calls = {
            fn_key: targets & ((defined_functions - {fn_key}) | {UNRESOLVED_CALL})
            for fn_key, targets in raw_calls.items()
        }
        return defined_functions, intra_file_calls

    @staticmethod
    def _reachable_intra_calls(
        start_set: set[str],
        intra_file_calls: dict[str, set[str]],
    ) -> set[str]:
        """Compute transitive intra-file call reachability from start_set via BFS."""
        visited: set[str] = set()
        queue: deque[str] = deque(start_set)
        while queue:
            curr = queue.popleft()
            for callee in intra_file_calls.get(curr, ()):
                if callee not in visited:
                    visited.add(callee)
                    queue.append(callee)
        return visited

    def are_functions_isolated(self, rel_path: str, scope_a: str, scope_b: str) -> bool:
        """Verify via AST that scope_a and scope_b refer to existing, non-calling functions."""
        norm_path = normalize_rel_path(rel_path)
        if not self._files:
            self.scan()
        tokens_a = set(re.findall(r"[A-Za-z_][A-Za-z0-9_.]*", scope_a))
        tokens_b = set(re.findall(r"[A-Za-z_][A-Za-z0-9_.]*", scope_b))
        if not tokens_a or not tokens_b or not tokens_a.isdisjoint(tokens_b):
            return False

        meta = self._files.get(norm_path)
        if meta is None:
            return False

        def _resolve_scope(tokens: set[str]) -> set[str]:
            resolved: set[str] = set()
            for tok in tokens:
                for fn in meta.defined_functions:
                    if fn == tok or fn.endswith(f".{tok}"):
                        resolved.add(fn)
            return resolved

        resolved_a = _resolve_scope(tokens_a)
        resolved_b = _resolve_scope(tokens_b)
        if not resolved_a or not resolved_b or not resolved_a.isdisjoint(resolved_b):
            return False

        reach_a = self._reachable_intra_calls(resolved_a, meta.intra_file_calls)
        reach_b = self._reachable_intra_calls(resolved_b, meta.intra_file_calls)
        # Fail-closed: an unresolvable dynamic call may reach anything in the file.
        if UNRESOLVED_CALL in reach_a or UNRESOLVED_CALL in reach_b:
            return False
        direct = resolved_a | resolved_b
        if any(UNRESOLVED_CALL in meta.intra_file_calls.get(f, ()) for f in direct):
            return False
        return not (bool(reach_a & resolved_b) or bool(reach_b & resolved_a))

    def classify_file(self, rel_path: str) -> FileRole:
        """Return architectural role of a relative file path."""
        normalized = normalize_rel_path(rel_path)
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
        normalized = normalize_rel_path(rel_path)
        meta = self._files.get(normalized)
        return set(meta.imported_by) if meta else set()

    def get_symbol_definitions(self) -> dict[str, set[str]]:
        """Return dictionary mapping discovered symbol names to defining relative file paths."""
        return {k: set(v) for k, v in self._symbols_to_files.items()}

    def find_symbol_files(self, symbol: str) -> set[str]:
        """Find relative file paths defining the given symbol name."""
        return set(self._symbols_to_files.get(symbol, ()))

    @staticmethod
    def extract_namespace_tokens(text: str) -> set[str]:
        """Extract normalized ENV, CLI flag, and HTTP route namespace tokens from text."""
        if not text:
            return set()
        tokens: set[str] = set()
        cli_stop = {"--help", "--version"}
        clause_split_re = re.compile(
            r"(?i)[;.]\s+|\.\s*$|"
            r",\s+(?=(?:but\s+)?(?:do\s+not|don't|must\s+not|never|avoid|"
            r"without\b|keep\b|leave\b|preserve\b|не\s+))|"
            r"\s+(?:(?:and|but|и|но)\s+)?(?=(?:do\s+not|don't|must\s+not|never|avoid|"
            r"without\s+(?:changing|modifying|touching)|keep\s+existing|preserve|"
            r"не\s+менять|не\s+трогать|без\s+изменения|не\s+изменять|сохранить)\b)"
        )
        env_re = re.compile(
            r"\b([A-Z][A-Z0-9_]{2,}_(?:MODE|KEY|PORT|DIR|URL|ENV|HOST|TOKEN|TIMEOUT|PATH|FLAG))\b"
        )
        cli_re = re.compile(r"(?:^|[\s`'\"(])(--[a-z][a-z0-9-]+)\b")
        route_re = re.compile(r"\b(GET|POST|PUT|DELETE|PATCH)\s+(/[A-Za-z0-9_./{}:-]+)")

        in_readonly_section = False
        for line in text.splitlines():
            stripped = line.strip()
            if re.match(r"^#{1,6}\s+", stripped):
                in_readonly_section = bool(_READONLY_SECTION_HEADER_RE.match(stripped))
                if in_readonly_section:
                    continue
            if in_readonly_section:
                continue

            for clause in clause_split_re.split(line):
                if not clause.strip():
                    continue
                prose_only = env_re.sub(" ", route_re.sub(" ", cli_re.sub(" ", clause)))
                if _NEGATED_CONSTRAINT_RE.search(prose_only):
                    continue

                for m in env_re.finditer(clause):
                    tokens.add(f"ns:ENV:{m.group(1)}")

                for m in cli_re.finditer(clause):
                    cli_flag = m.group(1)
                    if cli_flag not in cli_stop:
                        tokens.add(f"ns:CLI:{cli_flag}")

                for m in route_re.finditer(clause):
                    method, route_path = m.group(1), m.group(2)
                    cleaned_path = route_path.rstrip(".,;:)")
                    norm_path = re.sub(
                        r"\{[^}]+\}|:[A-Za-z_][A-Za-z0-9_]*|(?<=/)\d+(?=/|$)",
                        "{param}",
                        cleaned_path,
                    )
                    tokens.add(f"ns:ROUTE:{method.upper()} {norm_path}")

        return tokens

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
            existing_writes = {
                norm for wf in task.write_files if (norm := normalize_rel_path(wf))
            }
            for file_path in sorted(found_title_files):
                norm = normalize_rel_path(file_path)
                if norm and norm not in existing_writes:
                    task.write_files.append(norm)
                    existing_writes.add(norm)
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
        ns_tokens = self.extract_namespace_tokens(combined_text)
        for tok in sorted(ns_tokens):
            if tok not in task.namespace_tokens:
                task.namespace_tokens.append(tok)

        found_files = self.resolve_symbols_in_text(combined_text)
        is_write_empty = not task.write_files
        existing_writes = {
            norm for wf in task.write_files if (norm := normalize_rel_path(wf))
        }
        existing_impacts = {
            norm for imp in task.impact_files if (norm := normalize_rel_path(imp))
        }

        for file_path in sorted(found_files):
            norm = normalize_rel_path(file_path)
            if not norm:
                continue
            if is_write_empty:
                if norm not in existing_writes:
                    task.write_files.append(norm)
                    existing_writes.add(norm)
                    newly_added.append(norm)
            else:
                # Context-Read symbol: enrich impact_files for semantic collision checking
                if norm not in existing_writes and norm not in existing_impacts:
                    task.impact_files.append(norm)
                    existing_impacts.add(norm)
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
