"""Pre-merge AST diff gate for cross-track cycles, drift, helpers, and lockfiles."""

from __future__ import annotations

import ast
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sdd_parallel.ast_scanner import ASTHotspotScanner, resolve_import_from_targets
from sdd_parallel.models import TaskNode, WaveTrack, normalize_rel_path

_EXCLUDED_HELPER_NAMES: frozenset[str] = frozenset(
    {
        "__init__",
        "__str__",
        "__repr__",
        "__eq__",
        "__hash__",
        "__call__",
        "__enter__",
        "__exit__",
        "__len__",
        "__iter__",
        "__next__",
        "__getitem__",
        "__setitem__",
        "__contains__",
        "setUp",
        "tearDown",
        "setUpClass",
        "tearDownClass",
    }
)

_DEPENDENCY_MANIFEST_NAMES: frozenset[str] = frozenset(
    {
        "pyproject.toml",
        "uv.lock",
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "Cargo.toml",
        "Cargo.lock",
        "go.mod",
        "go.sum",
        "requirements.txt",
    }
)

_STRUCTURAL_SHINGLE_MARKERS: tuple[str, ...] = (
    "CALL:",
    "ATTR:",
    "SUBSCRIPT",
    "SLICE",
    "COMPARE",
    "BINOP",
    "TRY",
    "FOR",
    "ASYNCFOR",
    "WHILE",
    "LISTCOMP",
    "DICTCOMP",
    "SETCOMP",
)

_STRUCTURAL_NODE_TYPES: tuple[type[ast.AST], ...] = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.Try,
    ast.With,
    ast.AsyncWith,
    ast.Return,
    ast.Raise,
    ast.BinOp,
    ast.BoolOp,
    ast.UnaryOp,
    ast.Compare,
    ast.Subscript,
    ast.Slice,
    ast.ListComp,
    ast.DictComp,
    ast.SetComp,
    ast.GeneratorExp,
    ast.IfExp,
)


@dataclass(frozen=True)
class ASTGateFinding:
    """Deterministic pre-merge conflict finding detected across track diffs."""

    # Categories: IMPORT_CYCLE | SIGNATURE_DEFAULT_DRIFT | DIVERGENT_HELPER | DEPENDENCY_DRIFT
    category: str
    severity: Literal["ERROR", "WARNING"]
    tracks: tuple[str, ...]
    files: tuple[str, ...]
    symbols: tuple[str, ...] = ()
    message: str = ""

    def to_violation_str(self, wave_number: int) -> str:
        """Render a Wave-prefixed violation message compatible with verify_wave."""
        tracks_str = ", ".join(self.tracks)
        files_str = ", ".join(self.files)
        return (
            f"Wave {wave_number} / [{tracks_str}] [{self.category}] "
            f"({files_str}): {self.message}"
        )


def _normalize_rel_path(path: str) -> str:
    return normalize_rel_path(path)


def _read_base_file(
    repo_root: Path,
    merge_base: str,
    rel_f: str,
    worktree_root: Path | None = None,
) -> str | None:
    """Read file contents at merge_base (or fallback repo_root if non-git test fixture)."""
    norm = _normalize_rel_path(rel_f)
    if (repo_root / ".git").exists():
        rev = merge_base.strip() or "HEAD"
        proc = subprocess.run(
            ["git", "show", f"{rev}:{norm}"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if proc.returncode == 0:
            return proc.stdout
        return None
    if (
        worktree_root is not None
        and worktree_root.resolve() != repo_root.resolve()
        and (repo_root / norm).is_file()
    ):
        try:
            return (repo_root / norm).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
    return None


def _register_module_candidates(
    norm_f: str,
    src_dir: str,
    mod_to_path: dict[str, str],
) -> str:
    """Register dotted module candidates for a Python path and return canonical module."""
    stem_path = norm_f[:-3] if norm_f.endswith(".py") else norm_f
    parts = [p for p in stem_path.split("/") if p]
    if parts and parts[-1] == "__init__":
        parts.pop()
    full_mod = ".".join(parts)
    if full_mod:
        mod_to_path.setdefault(full_mod, norm_f)
    if src_dir and parts and parts[0] == src_dir:
        inner_mod = ".".join(parts[1:])
        if inner_mod:
            mod_to_path.setdefault(inner_mod, norm_f)
            return inner_mod
    return full_mod


def _resolve_module_path(mod_name: str, mod_to_path: Mapping[str, str]) -> str | None:
    if not mod_name:
        return None
    if mod_name in mod_to_path:
        return mod_to_path[mod_name]
    parts = mod_name.split(".")
    while parts:
        candidate = ".".join(parts)
        if candidate in mod_to_path:
            return mod_to_path[candidate]
        parts.pop()
    return None


def _extract_file_imports(
    source: str,
    rel_f: str,
    src_dir: str,
    mod_to_path: Mapping[str, str],
) -> set[str]:
    """Extract resolved internal relative paths imported by a Python source file."""
    norm_rel = _normalize_rel_path(rel_f)
    try:
        tree = ast.parse(source, filename=norm_rel)
    except SyntaxError:
        return set()

    def _resolve_mod(mod_name: str) -> str | None:
        return _resolve_module_path(mod_name, mod_to_path)

    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                resolved = _resolve_module_path(alias.name, mod_to_path)
                if (
                    resolved
                    and resolved != norm_rel
                    and not ASTHotspotScanner._is_test_path(resolved)
                ):
                    targets.add(resolved)
        elif isinstance(node, ast.ImportFrom):
            for resolved in resolve_import_from_targets(
                node=node,
                rel_path=norm_rel,
                src_dir=src_dir,
                resolve_module_fn=_resolve_mod,
            ):
                if not ASTHotspotScanner._is_test_path(resolved):
                    targets.add(resolved)
    return targets


def _tarjan_scc(adj: Mapping[str, set[str]]) -> list[list[str]]:
    """Find strongly connected components iteratively using Tarjan's algorithm."""
    all_nodes = sorted(set(adj.keys()) | {dst for targets in adj.values() for dst in targets})
    index_counter = 0
    indices: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    on_stack: set[str] = set()
    tarjan_stack: list[str] = []
    sccs: list[list[str]] = []

    for start in all_nodes:
        if start in indices:
            continue
        call_stack: list[tuple[str, list[str], int]] = [
            (start, sorted(adj.get(start, ())), 0)
        ]
        indices[start] = lowlink[start] = index_counter
        index_counter += 1
        tarjan_stack.append(start)
        on_stack.add(start)

        while call_stack:
            v, neighbors, idx = call_stack[-1]
            if idx < len(neighbors):
                w = neighbors[idx]
                call_stack[-1] = (v, neighbors, idx + 1)
                if w not in indices:
                    indices[w] = lowlink[w] = index_counter
                    index_counter += 1
                    tarjan_stack.append(w)
                    on_stack.add(w)
                    call_stack.append((w, sorted(adj.get(w, ())), 0))
                elif w in on_stack:
                    lowlink[v] = min(lowlink[v], indices[w])
            else:
                call_stack.pop()
                if call_stack:
                    parent = call_stack[-1][0]
                    lowlink[parent] = min(lowlink[parent], lowlink[v])
                if lowlink[v] == indices[v]:
                    comp: list[str] = []
                    while True:
                        node = tarjan_stack.pop()
                        on_stack.remove(node)
                        comp.append(node)
                        if node == v:
                            break
                    sccs.append(comp)
    return sccs


def check_virtual_import_cycles(
    repo_root: Path,
    base_scanner: ASTHotspotScanner,
    track_worktrees: Mapping[str, Path],
    track_changed_files: Mapping[str, Sequence[str]],
) -> list[ASTGateFinding]:
    """Detect cross-track circular imports via Tarjan SCC on merged graphs (Class G)."""
    if not base_scanner._files:
        base_scanner.scan()

    base_adj: dict[str, set[str]] = {}
    for rel_path, meta in base_scanner._files.items():
        if ASTHotspotScanner._is_test_path(rel_path):
            continue
        base_adj[rel_path] = {
            target
            for target in meta.imports
            if target != rel_path and not ASTHotspotScanner._is_test_path(target)
        }

    adj: dict[str, set[str]] = {k: set(v) for k, v in base_adj.items()}
    mod_to_path: dict[str, str] = dict(base_scanner._module_to_path)

    for rel_files in track_changed_files.values():
        for raw_f in rel_files:
            norm_f = _normalize_rel_path(raw_f)
            if norm_f.endswith(".py") and not ASTHotspotScanner._is_test_path(norm_f):
                _register_module_candidates(norm_f, base_scanner.src_dir, mod_to_path)

    new_edge_owner: dict[tuple[str, str], set[str]] = {}
    overwritten_nodes: set[str] = set()

    for track_id in sorted(track_changed_files):
        wt_root = track_worktrees.get(track_id, repo_root)
        for raw_f in track_changed_files[track_id]:
            norm_f = _normalize_rel_path(raw_f)
            if not norm_f.endswith(".py") or ASTHotspotScanner._is_test_path(norm_f):
                continue
            file_path = wt_root / norm_f
            if not file_path.is_file():
                if norm_f not in overwritten_nodes:
                    adj[norm_f] = set()
                    overwritten_nodes.add(norm_f)
                continue
            try:
                source = file_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            new_targets = _extract_file_imports(
                source=source,
                rel_f=norm_f,
                src_dir=base_scanner.src_dir,
                mod_to_path=mod_to_path,
            )
            if norm_f not in overwritten_nodes:
                adj[norm_f] = set(new_targets)
                overwritten_nodes.add(norm_f)
            else:
                adj.setdefault(norm_f, set()).update(new_targets)

            base_targets = base_adj.get(norm_f, set())
            for target in new_targets:
                if target not in base_targets:
                    new_edge_owner.setdefault((norm_f, target), set()).add(track_id)

    findings: list[ASTGateFinding] = []
    for comp in _tarjan_scc(adj):
        if len(comp) < 2:
            continue
        comp_set = set(comp)
        involved_tracks: set[str] = set()
        for u in comp_set:
            for v in adj.get(u, ()):
                if v in comp_set:
                    involved_tracks.update(new_edge_owner.get((u, v), ()))
        if not involved_tracks:
            continue
        cycle_files = tuple(sorted(comp_set))
        tracks_tuple = tuple(sorted(involved_tracks))
        findings.append(
            ASTGateFinding(
                category="IMPORT_CYCLE",
                severity="ERROR",
                tracks=tracks_tuple,
                files=cycle_files,
                message=(
                    f"Virtual circular import detected across merged track diffs: "
                    f"{' -> '.join(cycle_files)} -> {cycle_files[0]}"
                ),
            )
        )
    return findings


def _format_arg_spec(arg: ast.arg, default: ast.expr | None) -> str:
    ann = f": {ast.unparse(arg.annotation)}" if arg.annotation is not None else ""
    d_val = f" = {ast.unparse(default)}" if default is not None else ""
    return f"{arg.arg}{ann}{d_val}"


def _format_func_contract(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    kind = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    parts: list[str] = []

    positional = list(node.args.posonlyargs) + list(node.args.args)
    defaults = list(node.args.defaults)
    default_offset = len(positional) - len(defaults)
    posonly_count = len(node.args.posonlyargs)

    for idx, arg in enumerate(positional):
        default = defaults[idx - default_offset] if idx >= default_offset else None
        parts.append(_format_arg_spec(arg, default))
        if posonly_count > 0 and idx == posonly_count - 1:
            parts.append("/")

    if node.args.vararg is not None:
        parts.append(f"*{_format_arg_spec(node.args.vararg, None)}")
    elif node.args.kwonlyargs:
        parts.append("*")

    for kw_arg, kw_def in zip(node.args.kwonlyargs, node.args.kw_defaults, strict=True):
        parts.append(_format_arg_spec(kw_arg, kw_def))

    if node.args.kwarg is not None:
        parts.append(f"**{_format_arg_spec(node.args.kwarg, None)}")

    ret = ast.unparse(node.returns) if node.returns is not None else ""
    return f"{kind}({', '.join(parts)}) -> {ret}"


def _extract_module_contracts(source: str) -> dict[str, str]:
    """Extract canonical signature/default/constant contracts for module symbols."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}

    contracts: dict[str, str] = {}
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            contracts[stmt.name] = _format_func_contract(stmt)
        elif isinstance(stmt, ast.ClassDef):
            for item in stmt.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    contracts[f"{stmt.name}.{item.name}"] = _format_func_contract(item)
        elif isinstance(stmt, ast.Assign):
            val_repr = ast.unparse(stmt.value)
            for target in stmt.targets:
                if (
                    isinstance(target, ast.Name)
                    and len(target.id) >= 2
                    and target.id.isupper()
                ):
                    contracts[target.id] = val_repr
        elif isinstance(stmt, ast.AnnAssign):
            if (
                stmt.value is not None
                and isinstance(stmt.target, ast.Name)
                and len(stmt.target.id) >= 2
                and stmt.target.id.isupper()
            ):
                contracts[stmt.target.id] = ast.unparse(stmt.value)
    return contracts


def _extract_symbol_references(source: str) -> set[str]:
    """Extract referenced identifier, attribute, and imported symbol names from Python code."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()

    refs: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            refs.add(node.id)
        elif isinstance(node, ast.Attribute):
            refs.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                refs.add(alias.name)
    return refs


def check_signature_and_default_drift(
    repo_root: Path,
    merge_base_map: Mapping[str, str],
    tracks: Sequence[WaveTrack],
    track_worktrees: Mapping[str, Path],
    track_changed_files: Mapping[str, Sequence[str]],
    tasks_by_id: Mapping[str, TaskNode],
) -> list[ASTGateFinding]:
    """Detect drifted signatures, defaults, or constants impacting sibling tracks (Class A/B)."""
    track_deps: dict[str, set[str]] = {}
    track_symbol_refs: dict[str, set[str]] = {}

    for track in tracks:
        tid = track.track_id
        deps: set[str] = {
            _normalize_rel_path(f) for f in track_changed_files.get(tid, ())
        }
        deps.update(_normalize_rel_path(f) for f in track.exclusive_write_files)
        deps.update(_normalize_rel_path(f) for f in track.additive_shared_files)
        for task_id in track.task_ids:
            task = tasks_by_id.get(task_id)
            if task is not None:
                deps.update(_normalize_rel_path(f) for f in task.write_files)
                deps.update(_normalize_rel_path(f) for f in task.read_files)
                deps.update(_normalize_rel_path(f) for f in task.impact_files)
        track_deps[tid] = deps

        wt_root = track_worktrees.get(tid, repo_root)
        refs: set[str] = set()
        candidate_py_files = {
            _normalize_rel_path(f) for f in track_changed_files.get(tid, ())
        } | deps
        for rel_py in sorted(candidate_py_files):
            if not rel_py.endswith(".py"):
                continue
            py_path = wt_root / rel_py
            if not py_path.is_file():
                continue
            try:
                src = py_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            refs.update(_extract_symbol_references(src))
        track_symbol_refs[tid] = refs

    findings: list[ASTGateFinding] = []
    for track_a in tracks:
        tid_a = track_a.track_id
        wt_a = track_worktrees.get(tid_a, repo_root)
        mb_a = merge_base_map.get(tid_a, "HEAD")

        for raw_f in track_changed_files.get(tid_a, ()):
            norm_f = _normalize_rel_path(raw_f)
            if not norm_f.endswith(".py") or ASTHotspotScanner._is_test_path(norm_f):
                continue

            base_source = _read_base_file(repo_root, mb_a, norm_f, worktree_root=wt_a)
            if base_source is None:
                continue
            base_contracts = _extract_module_contracts(base_source)
            if not base_contracts:
                continue

            new_path = wt_a / norm_f
            if not new_path.is_file():
                new_contracts: dict[str, str] = {}
            else:
                try:
                    new_source = new_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                new_contracts = _extract_module_contracts(new_source)

            drifted_symbols = [
                sym
                for sym, base_sig in sorted(base_contracts.items())
                if new_contracts.get(sym) != base_sig
            ]
            if not drifted_symbols:
                continue

            for track_b in tracks:
                tid_b = track_b.track_id
                if tid_b == tid_a:
                    continue
                deps_b = track_deps.get(tid_b, set())
                refs_b = track_symbol_refs.get(tid_b, set())

                touches_file = norm_f in deps_b
                matching_syms: list[str] = []
                for sym in drifted_symbols:
                    short_sym = sym.split(".", 1)[-1]
                    if sym in refs_b or short_sym in refs_b:
                        matching_syms.append(sym)

                if touches_file or matching_syms:
                    reported_syms = tuple(matching_syms if matching_syms else drifted_symbols)
                    findings.append(
                        ASTGateFinding(
                            category="SIGNATURE_DEFAULT_DRIFT",
                            severity="ERROR",
                            tracks=(tid_a, tid_b),
                            files=(norm_f,),
                            symbols=reported_syms,
                            message=(
                                f"Contract drift in '{norm_f}' for symbol(s) "
                                f"{', '.join(reported_syms)} impacts track '{tid_b}'"
                            ),
                        )
                    )
    return findings


def _collect_local_vars(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    local_vars: set[str] = set()
    for arg in (
        list(func_node.args.posonlyargs)
        + list(func_node.args.args)
        + list(func_node.args.kwonlyargs)
    ):
        local_vars.add(arg.arg)
    if func_node.args.vararg is not None:
        local_vars.add(func_node.args.vararg.arg)
    if func_node.args.kwarg is not None:
        local_vars.add(func_node.args.kwarg.arg)

    for node in ast.walk(func_node):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            local_vars.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            local_vars.add(node.name)
    return local_vars


def _is_non_local_module_chain(expr: ast.expr, local_vars: set[str]) -> bool:
    cur: ast.expr = expr
    while isinstance(cur, ast.Attribute):
        cur = cur.value
    return isinstance(cur, ast.Name) and cur.id not in local_vars


def _emit_normalized_tokens(
    node: ast.AST,
    local_vars: set[str],
    tokens: list[str],
) -> None:
    if isinstance(node, (ast.Assign, ast.AugAssign)):
        tokens.append("ASSIGN")
        if isinstance(node, ast.Assign):
            for t in node.targets:
                _emit_normalized_tokens(t, local_vars, tokens)
            _emit_normalized_tokens(node.value, local_vars, tokens)
        else:
            _emit_normalized_tokens(node.target, local_vars, tokens)
            tokens.append(type(node.op).__name__.upper())
            _emit_normalized_tokens(node.value, local_vars, tokens)
        return

    if isinstance(node, ast.AnnAssign):
        tokens.append("ASSIGN")
        _emit_normalized_tokens(node.target, local_vars, tokens)
        if node.value is not None:
            _emit_normalized_tokens(node.value, local_vars, tokens)
        return

    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Attribute):
            tokens.append(f"CALL:{node.func.attr}")
            if not _is_non_local_module_chain(node.func.value, local_vars):
                _emit_normalized_tokens(node.func.value, local_vars, tokens)
        elif isinstance(node.func, ast.Name):
            callee = "VAR" if node.func.id in local_vars else node.func.id
            tokens.append(f"CALL:{callee}")
        else:
            tokens.append("CALL:EXPR")
            _emit_normalized_tokens(node.func, local_vars, tokens)
        for arg in node.args:
            _emit_normalized_tokens(arg, local_vars, tokens)
        for kw in node.keywords:
            _emit_normalized_tokens(kw.value, local_vars, tokens)
        return

    if isinstance(node, ast.Attribute):
        _emit_normalized_tokens(node.value, local_vars, tokens)
        tokens.append(f"ATTR:{node.attr}")
        return

    if isinstance(node, ast.Name):
        tokens.append("VAR" if node.id in local_vars else f"NAME:{node.id}")
        return

    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool):
            tokens.append("CONST:BOOL")
        elif isinstance(node.value, str):
            tokens.append("CONST:STR")
        elif isinstance(node.value, (int, float, complex)):
            tokens.append("CONST:NUM")
        elif node.value is None:
            tokens.append("CONST:NONE")
        else:
            tokens.append("CONST:OTHER")
        return

    if isinstance(node, _STRUCTURAL_NODE_TYPES):
        tokens.append(type(node).__name__.upper())
        if isinstance(node, ast.Compare):
            _emit_normalized_tokens(node.left, local_vars, tokens)
            for op, comp in zip(node.ops, node.comparators, strict=True):
                tokens.append(type(op).__name__.upper())
                _emit_normalized_tokens(comp, local_vars, tokens)
            return
        if isinstance(node, ast.BinOp):
            _emit_normalized_tokens(node.left, local_vars, tokens)
            tokens.append(type(node.op).__name__.upper())
            _emit_normalized_tokens(node.right, local_vars, tokens)
            return
        if isinstance(node, ast.BoolOp):
            tokens.append(type(node.op).__name__.upper())
            for val in node.values:
                _emit_normalized_tokens(val, local_vars, tokens)
            return
        if isinstance(node, ast.UnaryOp):
            tokens.append(type(node.op).__name__.upper())
            _emit_normalized_tokens(node.operand, local_vars, tokens)
            return

    for child in ast.iter_child_nodes(node):
        _emit_normalized_tokens(child, local_vars, tokens)


def compute_function_ast_shingles(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> frozenset[str]:
    """Build normalized structural AST token bigrams for a function body."""
    local_vars = _collect_local_vars(func_node)
    tokens: list[str] = []
    for idx, stmt in enumerate(func_node.body):
        if (
            idx == 0
            and isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Constant)
            and isinstance(stmt.value.value, str)
        ):
            continue
        _emit_normalized_tokens(stmt, local_vars, tokens)

    if len(tokens) < 2:
        return frozenset()
    return frozenset(f"{tokens[i]}->{tokens[i + 1]}" for i in range(len(tokens) - 1))


def _count_body_stmts(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    total = 0
    for idx, stmt in enumerate(func_node.body):
        if (
            idx == 0
            and isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Constant)
            and isinstance(stmt.value.value, str)
        ):
            continue
        total += sum(1 for child in ast.walk(stmt) if isinstance(child, ast.stmt))
    return total


def _extract_functions(
    source: str,
) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    funcs: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.append((stmt.name, stmt))
        elif isinstance(stmt, ast.ClassDef):
            for item in stmt.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    funcs.append((f"{stmt.name}.{item.name}", item))
    return funcs


@dataclass(frozen=True)
class _HelperCandidate:
    track_id: str
    rel_file: str
    func_name: str
    shingles: frozenset[str]


def check_divergent_helper_duplication(
    repo_root: Path,
    merge_base_map: Mapping[str, str],
    track_worktrees: Mapping[str, Path],
    track_changed_files: Mapping[str, Sequence[str]],
    similarity_threshold: float = 0.75,
    min_stmts: int = 4,
) -> list[ASTGateFinding]:
    """Detect semantically duplicated new helpers across tracks via AST shingles (Class F)."""
    candidates: list[_HelperCandidate] = []

    for track_id in sorted(track_changed_files):
        wt_root = track_worktrees.get(track_id, repo_root)
        mb = merge_base_map.get(track_id, "HEAD")
        for raw_f in track_changed_files[track_id]:
            norm_f = _normalize_rel_path(raw_f)
            if not norm_f.endswith(".py") or ASTHotspotScanner._is_test_path(norm_f):
                continue
            new_path = wt_root / norm_f
            if not new_path.is_file():
                continue
            try:
                new_source = new_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            base_source = _read_base_file(repo_root, mb, norm_f, worktree_root=wt_root)
            base_names = (
                {name for name, _ in _extract_functions(base_source)}
                if base_source is not None
                else set()
            )

            for qual_name, fn_node in _extract_functions(new_source):
                short_name = qual_name.split(".", 1)[-1]
                if (
                    qual_name in base_names
                    or short_name.startswith("test_")
                    or short_name in _EXCLUDED_HELPER_NAMES
                ):
                    continue
                if _count_body_stmts(fn_node) < min_stmts:
                    continue
                shingles = compute_function_ast_shingles(fn_node)
                if len(shingles) < 4:
                    continue
                candidates.append(
                    _HelperCandidate(
                        track_id=track_id,
                        rel_file=norm_f,
                        func_name=qual_name,
                        shingles=shingles,
                    )
                )

    findings: list[ASTGateFinding] = []
    for idx_a, cand_a in enumerate(candidates):
        for cand_b in candidates[idx_a + 1 :]:
            if cand_a.track_id == cand_b.track_id:
                continue
            union = cand_a.shingles | cand_b.shingles
            if not union:
                continue
            shared = cand_a.shingles & cand_b.shingles
            if not any(
                marker in bigram
                for bigram in shared
                for marker in _STRUCTURAL_SHINGLE_MARKERS
            ):
                continue
            score = len(shared) / len(union)
            if score >= similarity_threshold:
                findings.append(
                    ASTGateFinding(
                        category="DIVERGENT_HELPER",
                        severity="ERROR",
                        tracks=(cand_a.track_id, cand_b.track_id),
                        files=(cand_a.rel_file, cand_b.rel_file),
                        symbols=(cand_a.func_name, cand_b.func_name),
                        message=(
                            f"Semantic duplicate helper detected: '{cand_a.func_name}' "
                            f"({cand_a.track_id}) vs '{cand_b.func_name}' ({cand_b.track_id}), "
                            f"AST similarity={score:.2f}"
                        ),
                    )
                )
    return findings


def check_dependency_manifest_collision(
    track_changed_files: Mapping[str, Sequence[str]],
) -> list[ASTGateFinding]:
    """Detect concurrent edits to dependency manifests or lockfiles across >= 2 tracks (Class H)."""
    manifest_owners: dict[str, set[str]] = {}
    for track_id, rel_files in track_changed_files.items():
        for raw_f in rel_files:
            norm_f = _normalize_rel_path(raw_f)
            if Path(norm_f).name in _DEPENDENCY_MANIFEST_NAMES:
                manifest_owners.setdefault(norm_f, set()).add(track_id)

    findings: list[ASTGateFinding] = []
    for norm_f, owners in sorted(manifest_owners.items()):
        if len(owners) >= 2:
            sorted_tracks = tuple(sorted(owners))
            findings.append(
                ASTGateFinding(
                    category="DEPENDENCY_DRIFT",
                    severity="ERROR",
                    tracks=sorted_tracks,
                    files=(norm_f,),
                    message=(
                        f"Dependency manifest or lockfile '{norm_f}' modified by "
                        f"multiple tracks: {', '.join(sorted_tracks)}"
                    ),
                )
            )
    return findings


def run_pre_merge_ast_diff_gate(
    repo_root: Path,
    tracks: Sequence[WaveTrack],
    track_worktrees: Mapping[str, Path],
    track_changed_files: Mapping[str, Sequence[str]],
    merge_base_map: Mapping[str, str],
    tasks_by_id: Mapping[str, TaskNode],
    base_scanner: ASTHotspotScanner | None = None,
    similarity_threshold: float = 0.75,
) -> list[ASTGateFinding]:
    """Run all 4 deterministic pre-merge AST diff detectors (Classes B, F, G, H) across tracks."""
    scanner = base_scanner if base_scanner is not None else ASTHotspotScanner(repo_root)
    if not scanner._files:
        scanner.scan()

    findings: list[ASTGateFinding] = []
    findings.extend(
        check_virtual_import_cycles(
            repo_root=repo_root,
            base_scanner=scanner,
            track_worktrees=track_worktrees,
            track_changed_files=track_changed_files,
        )
    )
    findings.extend(
        check_signature_and_default_drift(
            repo_root=repo_root,
            merge_base_map=merge_base_map,
            tracks=tracks,
            track_worktrees=track_worktrees,
            track_changed_files=track_changed_files,
            tasks_by_id=tasks_by_id,
        )
    )
    findings.extend(
        check_divergent_helper_duplication(
            repo_root=repo_root,
            merge_base_map=merge_base_map,
            track_worktrees=track_worktrees,
            track_changed_files=track_changed_files,
            similarity_threshold=similarity_threshold,
        )
    )
    findings.extend(
        check_dependency_manifest_collision(
            track_changed_files=track_changed_files,
        )
    )
    return findings
