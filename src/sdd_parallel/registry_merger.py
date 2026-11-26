"""Deterministic fail-closed 3-way AST merger for Python ADDITIVE_REGISTRY files."""

from __future__ import annotations

import ast
import json
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

from sdd_parallel.models import matches_path_pattern

_DEFAULT_REGISTRY_FILENAMES = frozenset(
    {
        "__init__.py",
        "cli.py",
    }
)
_MAX_LINE_LENGTH = 88


def is_python_registry_candidate(
    rel_path: str,
    additive_files: Collection[str] | None = None,
) -> bool:
    """Return True if rel_path is a .py file marked as ADDITIVE_REGISTRY."""
    norm = rel_path.replace("\\", "/").strip("/")
    if not norm.endswith(".py"):
        return False

    if additive_files is not None:
        for entry in additive_files:
            norm_entry = entry.replace("\\", "/").strip("/")
            if not norm_entry:
                continue
            if norm == norm_entry or matches_path_pattern(norm, norm_entry):
                return True
        return False

    return Path(norm).name in _DEFAULT_REGISTRY_FILENAMES or matches_path_pattern(
        norm, "*/mcp/server.py"
    )


@dataclass(frozen=True)
class _ImportItem:
    is_from: bool
    level: int
    module: str
    name: str
    asname: str | None

    @property
    def is_future(self) -> bool:
        return self.is_from and self.level == 0 and self.module == "__future__"

    @property
    def group_key(self) -> tuple[bool, int, str]:
        return (self.is_from, self.level, self.module)

    @property
    def local_name(self) -> str | None:
        if self.name == "*":
            return None
        if self.asname:
            return self.asname
        if not self.is_from and "." in self.name:
            return self.name.split(".", 1)[0]
        return self.name

    @property
    def binding_target(self) -> tuple[str, ...]:
        if self.is_from:
            return ("from", str(self.level), self.module, self.name)
        if self.asname:
            return ("import_as", self.name)
        top_pkg = self.name.split(".", 1)[0]
        return ("pkg", top_pkg)

    def format_alias(self) -> str:
        if self.asname:
            return f"{self.name} as {self.asname}"
        return self.name


@dataclass(frozen=True)
class _ParsedStmt:
    stmt: ast.stmt
    start_lineno: int
    end_lineno: int
    raw_slice: str
    ast_dump: str


@dataclass(frozen=True)
class _ParsedModule:
    leading_comments: str
    docstring: _ParsedStmt | None
    header_imports: list[_ParsedStmt]
    header_import_items: list[_ImportItem]
    stmt_import_items: list[list[_ImportItem]]
    multiline_from_groups: set[tuple[int, str]]
    dunder_all: _ParsedStmt | None
    body_stmts: list[_ParsedStmt]
    body_keys: list[tuple[str, str]]
    body_by_key: dict[tuple[str, str], _ParsedStmt]
    all_stmts_non_import_order: list[tuple[str, tuple[str, str] | None]]


def _normalize_text(text: str) -> str:
    return text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")


def _stmt_span(stmt: ast.stmt) -> tuple[int, int] | None:
    decorators = getattr(stmt, "decorator_list", ())
    start_lineno = min([stmt.lineno, *(d.lineno for d in decorators)])
    end_lineno = stmt.end_lineno
    if end_lineno is None:
        return None
    return start_lineno, end_lineno


def _slice_lines(lines: list[str], start_lineno: int, end_lineno: int) -> str:
    return "\n".join(lines[start_lineno - 1 : end_lineno])


def _is_module_docstring(idx: int, stmt: ast.stmt) -> bool:
    return (
        idx == 0
        and isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


def _is_dunder_all_stmt(stmt: ast.stmt) -> bool:
    if isinstance(stmt, ast.Assign):
        return (
            len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)
            and stmt.targets[0].id == "__all__"
        )
    if isinstance(stmt, ast.AnnAssign):
        return isinstance(stmt.target, ast.Name) and stmt.target.id == "__all__"
    if isinstance(stmt, ast.AugAssign):
        return isinstance(stmt.target, ast.Name) and stmt.target.id == "__all__"
    return False


def _extract_dunder_all_strings(
    stmt: ast.stmt,
) -> tuple[str, list[str], str] | None:
    """Return (container_kind, elements, lhs_prefix) if literal list/tuple of strings."""
    value: ast.expr | None = None
    lhs_prefix = "__all__ = "
    if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
        value = stmt.value
    elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
        value = stmt.value
        ann_str = ast.unparse(stmt.annotation)
        lhs_prefix = f"__all__: {ann_str} = "

    if isinstance(value, ast.List):
        kind = "list"
    elif isinstance(value, ast.Tuple):
        kind = "tuple"
    else:
        return None

    elements: list[str] = []
    for elt in value.elts:
        if not isinstance(elt, ast.Constant) or not isinstance(elt.value, str):
            return None
        elements.append(elt.value)
    return kind, elements, lhs_prefix


def _is_main_guard(stmt: ast.stmt) -> bool:
    if not isinstance(stmt, ast.If) or stmt.orelse:
        return False
    test = stmt.test
    if (
        not isinstance(test, ast.Compare)
        or len(test.ops) != 1
        or len(test.comparators) != 1
        or not isinstance(test.ops[0], ast.Eq)
    ):
        return False
    left, right = test.left, test.comparators[0]
    return (
        isinstance(left, ast.Name)
        and left.id == "__name__"
        and isinstance(right, ast.Constant)
        and right.value == "__main__"
    ) or (
        isinstance(right, ast.Name)
        and right.id == "__name__"
        and isinstance(left, ast.Constant)
        and left.value == "__main__"
    )


def _extract_import_items(stmt: ast.Import | ast.ImportFrom) -> list[_ImportItem]:
    if isinstance(stmt, ast.Import):
        return [
            _ImportItem(
                is_from=False,
                level=0,
                module="",
                name=alias.name,
                asname=alias.asname,
            )
            for alias in stmt.names
        ]
    module = stmt.module or ""
    return [
        _ImportItem(
            is_from=True,
            level=stmt.level,
            module=module,
            name=alias.name,
            asname=alias.asname,
        )
        for alias in stmt.names
    ]


def _stmt_key(stmt: ast.stmt) -> tuple[str, str]:
    if _is_main_guard(stmt):
        return ("main_guard", "__main__")
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return ("def", stmt.name)
    if (
        isinstance(stmt, ast.Assign)
        and len(stmt.targets) == 1
        and isinstance(stmt.targets[0], ast.Name)
    ):
        return ("var", stmt.targets[0].id)
    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
        return ("var", stmt.target.id)
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        return ("expr", ast.dump(stmt, include_attributes=False))
    return ("complex", ast.dump(stmt, include_attributes=False))


def _parse_module(text: str) -> _ParsedModule | None:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None

    lines = text.split("\n")
    prev_end = 0
    parsed_items: list[_ParsedStmt] = []
    for stmt in tree.body:
        span = _stmt_span(stmt)
        if span is None:
            return None
        start_lineno, end_lineno = span
        if prev_end >= start_lineno:
            return None
        prev_end = end_lineno
        parsed_items.append(
            _ParsedStmt(
                stmt=stmt,
                start_lineno=start_lineno,
                end_lineno=end_lineno,
                raw_slice=_slice_lines(lines, start_lineno, end_lineno),
                ast_dump=ast.dump(stmt, include_attributes=False),
            )
        )

    leading_comments = ""
    if parsed_items and parsed_items[0].start_lineno > 1:
        header_lines = lines[: parsed_items[0].start_lineno - 1]
        if any(line.strip().startswith("#") for line in header_lines):
            # Keep only leading comment/shebang/coding lines without trailing blank lines.
            trimmed = [line for line in header_lines if line.strip().startswith("#")]
            leading_comments = "\n".join(trimmed)

    docstring: _ParsedStmt | None = None
    header_imports: list[_ParsedStmt] = []
    header_import_items: list[_ImportItem] = []
    stmt_import_items: list[list[_ImportItem]] = []
    multiline_from_groups: set[tuple[int, str]] = set()
    dunder_all: _ParsedStmt | None = None
    body_stmts: list[_ParsedStmt] = []
    body_keys: list[tuple[str, str]] = []
    body_by_key: dict[tuple[str, str], _ParsedStmt] = {}
    non_import_order: list[tuple[str, tuple[str, str] | None]] = []

    seen_non_import = False
    for idx, ps in enumerate(parsed_items):
        if _is_module_docstring(idx, ps.stmt):
            docstring = ps
            continue

        if not seen_non_import and isinstance(ps.stmt, (ast.Import, ast.ImportFrom)):
            items = _extract_import_items(ps.stmt)
            header_imports.append(ps)
            stmt_import_items.append(items)
            header_import_items.extend(items)
            if isinstance(ps.stmt, ast.ImportFrom):
                if "(" in ps.raw_slice and "\n" in ps.raw_slice:
                    multiline_from_groups.add((ps.stmt.level, ps.stmt.module or ""))
            continue

        seen_non_import = True
        if _is_dunder_all_stmt(ps.stmt):
            if dunder_all is not None:
                return None
            dunder_all = ps
            non_import_order.append(("all", None))
            continue

        key = _stmt_key(ps.stmt)
        if key in body_by_key:
            return None
        body_stmts.append(ps)
        body_keys.append(key)
        body_by_key[key] = ps
        non_import_order.append(("body", key))

    return _ParsedModule(
        leading_comments=leading_comments,
        docstring=docstring,
        header_imports=header_imports,
        header_import_items=header_import_items,
        stmt_import_items=stmt_import_items,
        multiline_from_groups=multiline_from_groups,
        dunder_all=dunder_all,
        body_stmts=body_stmts,
        body_keys=body_keys,
        body_by_key=body_by_key,
        all_stmts_non_import_order=non_import_order,
    )


def _format_from_import(
    level: int,
    module: str,
    items: list[_ImportItem],
    force_multiline: bool,
) -> str:
    prefix = f"{'.' * level}{module}"
    sorted_items = sorted(items, key=lambda it: (it.name, it.asname or ""))
    alias_strs = [it.format_alias() for it in sorted_items]
    single_line = f"from {prefix} import {', '.join(alias_strs)}"
    if not force_multiline and len(single_line) <= _MAX_LINE_LENGTH:
        return single_line
    inner = "\n".join(f"    {alias}," for alias in alias_strs)
    return f"from {prefix} import (\n{inner}\n)"


def _format_plain_import(items: list[_ImportItem]) -> str:
    return "\n".join(f"import {it.format_alias()}" for it in items)


def _check_import_binding_collisions(items: Collection[_ImportItem]) -> bool:
    """Return True if any two distinct imports collide on the same local name."""
    bound: dict[str, tuple[str, ...]] = {}
    for item in items:
        local = item.local_name
        if local is None:
            continue
        target = item.binding_target
        prev = bound.get(local)
        if prev is not None and prev != target:
            return True
        bound[local] = target
    return False


def _merge_header_imports(
    base: _ParsedModule,
    ours: _ParsedModule,
    theirs: _ParsedModule,
) -> tuple[list[str], list[str], set[_ImportItem]] | None:
    """Return (future_lines, regular_import_blocks, merged_item_set) or None."""
    base_set = set(base.header_import_items)
    ours_set = set(ours.header_import_items)
    theirs_set = set(theirs.header_import_items)

    removed_ours = base_set - ours_set
    removed_theirs = base_set - theirs_set
    added_ours = ours_set - base_set
    added_theirs = theirs_set - base_set

    merged_set = (base_set - removed_ours - removed_theirs) | added_ours | added_theirs
    if _check_import_binding_collisions(merged_set):
        return None

    multiline_groups = (
        base.multiline_from_groups
        | ours.multiline_from_groups
        | theirs.multiline_from_groups
    )

    future_items = [it for it in merged_set if it.is_future]
    future_blocks: list[str] = []
    if future_items:
        ours_future_stmts = [
            (ps, items)
            for ps, items in zip(ours.header_imports, ours.stmt_import_items, strict=True)
            if any(it.is_future for it in items)
        ]
        ours_future_set = {it for _, items in ours_future_stmts for it in items}
        if len(ours_future_stmts) == 1 and ours_future_set == set(future_items):
            future_blocks.append(ours_future_stmts[0][0].raw_slice)
        else:
            future_blocks.append(
                _format_from_import(
                    0,
                    "__future__",
                    future_items,
                    force_multiline=(0, "__future__") in multiline_groups,
                )
            )

    regular_merged = {it for it in merged_set if not it.is_future}
    ours_regular_set = {it for it in ours_set if not it.is_future}
    emitted: set[_ImportItem] = set()

    # Map each (level, module) to new items introduced solely by theirs.
    new_from_theirs_by_group: dict[tuple[int, str], list[_ImportItem]] = {}
    for it in theirs.header_import_items:
        if (
            not it.is_future
            and it.is_from
            and it in regular_merged
            and it not in ours_regular_set
        ):
            g = (it.level, it.module)
            if g not in new_from_theirs_by_group:
                new_from_theirs_by_group[g] = []
            if it not in new_from_theirs_by_group[g]:
                new_from_theirs_by_group[g].append(it)

    regular_blocks: list[str] = []
    for ps, items in zip(ours.header_imports, ours.stmt_import_items, strict=True):
        if any(it.is_future for it in items):
            continue
        if isinstance(ps.stmt, ast.Import):
            kept = [it for it in items if it in regular_merged and it not in emitted]
            if not kept:
                continue
            if len(kept) == len(items):
                regular_blocks.append(ps.raw_slice)
            else:
                regular_blocks.append(_format_plain_import(kept))
            emitted.update(kept)
            continue

        if not isinstance(ps.stmt, ast.ImportFrom):
            continue
        g = (ps.stmt.level, ps.stmt.module or "")
        kept = [it for it in items if it in regular_merged and it not in emitted]
        extra = [it for it in new_from_theirs_by_group.pop(g, []) if it not in emitted]
        if not kept and not extra:
            continue
        if len(kept) == len(items) and not extra:
            regular_blocks.append(ps.raw_slice)
            emitted.update(kept)
        else:
            combined = list(dict.fromkeys([*kept, *extra]))
            regular_blocks.append(
                _format_from_import(
                    g[0],
                    g[1],
                    combined,
                    force_multiline=g in multiline_groups,
                )
            )
            emitted.update(combined)

    # Append brand-new imports introduced by theirs in theirs order.
    for ps, items in zip(theirs.header_imports, theirs.stmt_import_items, strict=True):
        if any(it.is_future for it in items):
            continue
        if isinstance(ps.stmt, ast.Import):
            remaining = [it for it in items if it in regular_merged and it not in emitted]
            if not remaining:
                continue
            if len(remaining) == len(items):
                regular_blocks.append(ps.raw_slice)
            else:
                regular_blocks.append(_format_plain_import(remaining))
            emitted.update(remaining)
            continue

        if not isinstance(ps.stmt, ast.ImportFrom):
            continue
        g = (ps.stmt.level, ps.stmt.module or "")
        remaining = [it for it in items if it in regular_merged and it not in emitted]
        extra = [it for it in new_from_theirs_by_group.pop(g, []) if it not in emitted]
        combined = list(dict.fromkeys([*remaining, *extra]))
        if not combined:
            continue
        if len(combined) == len(items) and len(remaining) == len(items):
            regular_blocks.append(ps.raw_slice)
        else:
            regular_blocks.append(
                _format_from_import(
                    g[0],
                    g[1],
                    combined,
                    force_multiline=g in multiline_groups,
                )
            )
        emitted.update(combined)

    return future_blocks, regular_blocks, merged_set


def _format_dunder_all(
    kind: str,
    elements: list[str],
    lhs_prefix: str,
    force_multiline: bool,
) -> str:
    open_b, close_b = ("[", "]") if kind == "list" else ("(", ")")
    quoted = [json.dumps(el, ensure_ascii=False) for el in elements]
    if kind == "tuple" and len(quoted) == 1:
        single_line = f"{lhs_prefix}{open_b}{quoted[0]},{close_b}"
    else:
        single_line = f"{lhs_prefix}{open_b}{', '.join(quoted)}{close_b}"

    if not force_multiline and len(single_line) <= _MAX_LINE_LENGTH:
        return single_line

    if not quoted:
        return f"{lhs_prefix}{open_b}{close_b}"
    inner = "\n".join(f"    {q}," for q in quoted)
    return f"{lhs_prefix}{open_b}\n{inner}\n{close_b}"


def _merge_dunder_all(
    base: _ParsedModule,
    ours: _ParsedModule,
    theirs: _ParsedModule,
) -> str | None | _Sentinel:
    b_stmt = base.dunder_all
    o_stmt = ours.dunder_all
    t_stmt = theirs.dunder_all

    if b_stmt is None and o_stmt is None and t_stmt is None:
        return None

    b_dump = b_stmt.ast_dump if b_stmt is not None else None
    o_dump = o_stmt.ast_dump if o_stmt is not None else None
    t_dump = t_stmt.ast_dump if t_stmt is not None else None

    if o_dump == b_dump and t_dump != b_dump:
        return t_stmt.raw_slice if t_stmt is not None else None
    if t_dump == b_dump and o_dump != b_dump:
        return o_stmt.raw_slice if o_stmt is not None else None
    if o_dump == t_dump:
        return o_stmt.raw_slice if o_stmt is not None else None

    # Both sides modified/added __all__ differently.
    if o_stmt is None or t_stmt is None:
        return _FAIL_SENTINEL

    o_parsed = _extract_dunder_all_strings(o_stmt.stmt)
    t_parsed = _extract_dunder_all_strings(t_stmt.stmt)
    if o_parsed is None or t_parsed is None:
        return _FAIL_SENTINEL

    o_kind, o_elems, o_lhs = o_parsed
    t_kind, t_elems, t_lhs = t_parsed
    if o_lhs != t_lhs:
        return _FAIL_SENTINEL

    if b_stmt is not None:
        b_parsed = _extract_dunder_all_strings(b_stmt.stmt)
        if b_parsed is None:
            return _FAIL_SENTINEL
        b_elems = b_parsed[1]
    else:
        b_elems = []

    b_set = set(b_elems)
    removed_by_theirs = b_set - set(t_elems)
    merged_elems = [e for e in o_elems if e not in removed_by_theirs]
    for e in t_elems:
        if e not in b_set and e not in merged_elems:
            merged_elems.append(e)

    force_multiline = ("\n" in o_stmt.raw_slice) or ("\n" in t_stmt.raw_slice)
    if o_kind != t_kind:
        if b_stmt is None:
            return _FAIL_SENTINEL
        b_kind = _extract_dunder_all_strings(b_stmt.stmt)
        if b_kind is not None and o_kind == b_kind[0]:
            o_kind = t_kind

    return _format_dunder_all(o_kind, merged_elems, o_lhs, force_multiline=force_multiline)


class _Sentinel:
    pass


_FAIL_SENTINEL = _Sentinel()


def _merge_docstring(
    base: _ParsedModule,
    ours: _ParsedModule,
    theirs: _ParsedModule,
) -> str | None | _Sentinel:
    b_doc = base.docstring
    o_doc = ours.docstring
    t_doc = theirs.docstring

    b_dump = b_doc.ast_dump if b_doc is not None else None
    o_dump = o_doc.ast_dump if o_doc is not None else None
    t_dump = t_doc.ast_dump if t_doc is not None else None

    if o_dump == b_dump and t_dump != b_dump:
        return t_doc.raw_slice if t_doc is not None else None
    if t_dump == b_dump and o_dump != b_dump:
        return o_doc.raw_slice if o_doc is not None else None
    if o_dump == t_dump:
        return o_doc.raw_slice if o_doc is not None else None
    return _FAIL_SENTINEL


def _block_kind(stmt: ast.stmt) -> str:
    if _is_main_guard(stmt):
        return "main_guard"
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return "def_or_class"
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        return "call"
    return "stmt"


def _join_blocks(blocks: list[tuple[str, str]]) -> str:
    if not blocks:
        return ""
    parts: list[str] = [blocks[0][1]]
    for idx in range(1, len(blocks)):
        prev_kind = blocks[idx - 1][0]
        curr_kind = blocks[idx][0]
        if prev_kind == "header_comment":
            sep = "\n"
        elif prev_kind == "docstring":
            sep = "\n\n\n" if curr_kind in {"def_or_class", "main_guard"} else "\n\n"
        elif prev_kind == "future_import":
            if curr_kind == "import":
                sep = "\n\n"
            elif curr_kind in {"def_or_class", "main_guard"}:
                sep = "\n\n\n"
            else:
                sep = "\n\n"
        elif prev_kind == "import" and curr_kind == "import":
            sep = "\n"
        elif curr_kind in {"def_or_class", "main_guard"} or prev_kind == "def_or_class":
            sep = "\n\n\n"
        elif prev_kind == "call" and curr_kind == "call":
            sep = "\n"
        else:
            sep = "\n\n"
        parts.append(sep + blocks[idx][1])
    return "".join(parts)


def _new_bound_symbols(
    base: _ParsedModule,
    branch: _ParsedModule,
    import_items: set[_ImportItem],
) -> dict[str, str]:
    """Return newly bound symbol names -> AST dump/target signature added by branch."""
    base_imports = {
        it.local_name: it.binding_target
        for it in base.header_import_items
        if it.local_name is not None
    }
    symbols: dict[str, str] = {}
    for it in import_items:
        local = it.local_name
        if local is None:
            continue
        if local not in base_imports or base_imports[local] != it.binding_target:
            symbols[local] = f"import:{it.binding_target}"
    for key, ps in branch.body_by_key.items():
        if key[0] in {"def", "var"} and key not in base.body_by_key:
            symbols[key[1]] = f"{key[0]}:{ps.ast_dump}"
    return symbols


def _has_both_sides_new_stmt_of_kind(
    kind: str,
    base_mod: _ParsedModule,
    ours_mod: _ParsedModule,
    theirs_mod: _ParsedModule,
) -> bool:
    return (
        kind in {"expr", "complex"}
        and any(k[0] == kind and k not in base_mod.body_by_key for k in theirs_mod.body_by_key)
        and any(k[0] == kind and k not in base_mod.body_by_key for k in ours_mod.body_by_key)
    )


def _is_literal_key(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, (str, int, float, bool))


def _extract_expr_registration_keys(stmt: ast.stmt) -> set[tuple[str, str]]:
    """Extract (target_chain, literal_key) pairs from top-level registry calls."""
    if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Call):
        return set()
    call = stmt.value
    if not isinstance(call.func, ast.Attribute):
        return set()
    target_chain = ast.dump(call.func.value, include_attributes=False)
    method = call.func.attr
    keys: set[tuple[str, str]] = set()

    if method in {"register", "setdefault"}:
        if call.args and _is_literal_key(call.args[0]):
            assert isinstance(call.args[0], ast.Constant)
            keys.add((target_chain, repr(call.args[0].value)))
        elif method == "register":
            for kw in call.keywords:
                if kw.arg in {"name", "key"} and _is_literal_key(kw.value):
                    assert isinstance(kw.value, ast.Constant)
                    keys.add((target_chain, repr(kw.value.value)))
    elif method == "add_command":
        if len(call.args) >= 2 and _is_literal_key(call.args[1]):
            assert isinstance(call.args[1], ast.Constant)
            keys.add((target_chain, repr(call.args[1].value)))
        for kw in call.keywords:
            if kw.arg == "name" and _is_literal_key(kw.value):
                assert isinstance(kw.value, ast.Constant)
                keys.add((target_chain, repr(kw.value.value)))
    elif method == "update":
        for kw in call.keywords:
            if kw.arg is not None:
                keys.add((target_chain, repr(kw.arg)))
        for arg in call.args:
            if isinstance(arg, ast.Dict):
                for k in arg.keys:
                    if k is not None and _is_literal_key(k):
                        assert isinstance(k, ast.Constant)
                        keys.add((target_chain, repr(k.value)))
    return keys


def _collect_load_names(stmt: ast.stmt) -> set[str]:
    return {
        node.id
        for node in ast.walk(stmt)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }


def _extract_target_names(target: ast.AST) -> set[str]:
    return {
        node.id
        for node in ast.walk(target)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    }


def _extract_stmt_bound_names(stmt: ast.stmt) -> set[str]:
    if isinstance(stmt, ast.Import):
        return {alias.asname or alias.name.split(".", 1)[0] for alias in stmt.names}
    if isinstance(stmt, ast.ImportFrom):
        return {
            alias.asname or alias.name
            for alias in stmt.names
            if alias.name != "*"
        }
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {stmt.name}
    if isinstance(stmt, ast.Assign):
        names: set[str] = set()
        for tgt in stmt.targets:
            names.update(_extract_target_names(tgt))
        return names
    if isinstance(stmt, ast.AnnAssign):
        if stmt.value is not None and isinstance(stmt.target, ast.Name):
            return {stmt.target.id}
        return set()
    if isinstance(stmt, (ast.For, ast.AsyncFor)):
        return _extract_target_names(stmt.target)
    if isinstance(stmt, (ast.With, ast.AsyncWith)):
        names = set()
        for item in stmt.items:
            if item.optional_vars is not None:
                names.update(_extract_target_names(item.optional_vars))
        return names
    if isinstance(stmt, ast.Try):
        names = set()
        for handler in stmt.handlers:
            if handler.name is not None:
                names.add(handler.name)
        return names
    return set()


def _walk_eager_expr(
    node: ast.AST,
    out: set[str],
    *,
    has_future_annotations: bool,
) -> None:
    if isinstance(node, ast.Name):
        if isinstance(node.ctx, ast.Load):
            out.add(node.id)
        return
    if isinstance(node, ast.Lambda):
        for d in node.args.defaults:
            _walk_eager_expr(d, out, has_future_annotations=has_future_annotations)
        for kd in node.args.kw_defaults:
            if kd is not None:
                _walk_eager_expr(kd, out, has_future_annotations=has_future_annotations)
        return
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for dec in node.decorator_list:
            _walk_eager_expr(dec, out, has_future_annotations=has_future_annotations)
        for d in node.args.defaults:
            _walk_eager_expr(d, out, has_future_annotations=has_future_annotations)
        for kd in node.args.kw_defaults:
            if kd is not None:
                _walk_eager_expr(kd, out, has_future_annotations=has_future_annotations)
        if not has_future_annotations:
            if node.returns is not None:
                _walk_eager_expr(node.returns, out, has_future_annotations=has_future_annotations)
            for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
                if arg.annotation is not None:
                    _walk_eager_expr(
                        arg.annotation, out, has_future_annotations=has_future_annotations
                    )
            if node.args.vararg is not None and node.args.vararg.annotation is not None:
                _walk_eager_expr(
                    node.args.vararg.annotation,
                    out,
                    has_future_annotations=has_future_annotations,
                )
            if node.args.kwarg is not None and node.args.kwarg.annotation is not None:
                _walk_eager_expr(
                    node.args.kwarg.annotation,
                    out,
                    has_future_annotations=has_future_annotations,
                )
        return
    if isinstance(node, ast.ClassDef):
        for dec in node.decorator_list:
            _walk_eager_expr(dec, out, has_future_annotations=has_future_annotations)
        for b in node.bases:
            _walk_eager_expr(b, out, has_future_annotations=has_future_annotations)
        for kw in node.keywords:
            _walk_eager_expr(kw.value, out, has_future_annotations=has_future_annotations)
        class_bound: set[str] = set()
        for sub in node.body:
            sub_reads: set[str] = set()
            _walk_eager_expr(sub, sub_reads, has_future_annotations=has_future_annotations)
            out.update(sub_reads - class_bound)
            class_bound.update(_extract_stmt_bound_names(sub))
        return
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        comp_reads: set[str] = set()
        comp_bound: set[str] = set()
        for gen in node.generators:
            gen_iter_reads: set[str] = set()
            _walk_eager_expr(
                gen.iter, gen_iter_reads, has_future_annotations=has_future_annotations
            )
            out.update(gen_iter_reads - comp_bound)
            comp_bound.update(_extract_target_names(gen.target))
            for cond in gen.ifs:
                _walk_eager_expr(cond, comp_reads, has_future_annotations=has_future_annotations)
        if isinstance(node, ast.DictComp):
            _walk_eager_expr(node.key, comp_reads, has_future_annotations=has_future_annotations)
            _walk_eager_expr(node.value, comp_reads, has_future_annotations=has_future_annotations)
        else:
            _walk_eager_expr(node.elt, comp_reads, has_future_annotations=has_future_annotations)
        out.update(comp_reads - comp_bound)
        return
    for child in ast.iter_child_nodes(node):
        _walk_eager_expr(child, out, has_future_annotations=has_future_annotations)


def _collect_eager_load_names(stmt: ast.stmt, *, has_future_annotations: bool) -> set[str]:
    reads: set[str] = set()
    _walk_eager_expr(stmt, reads, has_future_annotations=has_future_annotations)
    return reads


def _find_top_level_forward_ref(
    stmts: list[ast.stmt],
    *,
    initial_bound: set[str],
    has_future_annotations: bool,
) -> tuple[int, str] | None:
    bound_per_stmt = [_extract_stmt_bound_names(s) for s in stmts]
    later_bound: list[set[str]] = [set() for _ in stmts]
    running: set[str] = set()
    for idx in range(len(stmts) - 1, -1, -1):
        later_bound[idx] = set(running)
        running |= bound_per_stmt[idx]

    bound_so_far = set(initial_bound)
    for idx, stmt in enumerate(stmts):
        eager_reads = _collect_eager_load_names(
            stmt, has_future_annotations=has_future_annotations
        )
        missing = sorted((eager_reads - bound_so_far) & later_bound[idx])
        if missing:
            return idx, missing[0]
        bound_so_far |= bound_per_stmt[idx]
    return None


@dataclass
class _AssembledBlock:
    kind: str
    text: str
    movable: bool = False


def merge_python_additive_registry(
    base_text: str,
    ours_text: str,
    theirs_text: str,
) -> str | None:
    """Perform deterministic 3-way AST additive merge for Python registry modules.

    Returns merged valid Python source string if changes are strictly compatible
    (orthogonal top-level additions, import additions, __all__ list additions, or
    one-sided edits to existing nodes). Returns None (fail-closed) whenever both
    branches mutated the same existing top-level statement/function or introduced
    conflicting symbol names.
    """
    base_norm = _normalize_text(base_text)
    ours_norm = _normalize_text(ours_text)
    theirs_norm = _normalize_text(theirs_text)

    if ours_norm == theirs_norm:
        return ours_norm
    if ours_norm == base_norm:
        return theirs_norm
    if theirs_norm == base_norm:
        return ours_norm

    base_mod = _parse_module(base_norm)
    ours_mod = _parse_module(ours_norm)
    theirs_mod = _parse_module(theirs_norm)
    if base_mod is None or ours_mod is None or theirs_mod is None:
        return None

    # 5e-1: main_guard must be the last non-import statement if present.
    for mod in (base_mod, ours_mod, theirs_mod):
        mg_indices = [
            idx
            for idx, (item_type, item_key) in enumerate(mod.all_stmts_non_import_order)
            if item_type == "body" and item_key == ("main_guard", "__main__")
        ]
        if mg_indices and mg_indices[-1] != len(mod.all_stmts_non_import_order) - 1:
            return None

    merged_doc = _merge_docstring(base_mod, ours_mod, theirs_mod)
    if isinstance(merged_doc, _Sentinel):
        return None

    import_res = _merge_header_imports(base_mod, ours_mod, theirs_mod)
    if import_res is None:
        return None
    future_blocks, regular_import_blocks, merged_import_set = import_res

    merged_all = _merge_dunder_all(base_mod, ours_mod, theirs_mod)
    if isinstance(merged_all, _Sentinel):
        return None

    # Check cross-kind symbol collisions across newly added imports, defs, and vars.
    ours_new_syms = _new_bound_symbols(base_mod, ours_mod, set(ours_mod.header_import_items))
    theirs_new_syms = _new_bound_symbols(base_mod, theirs_mod, set(theirs_mod.header_import_items))
    for sym, o_sig in ours_new_syms.items():
        t_sig = theirs_new_syms.get(sym)
        if t_sig is not None and o_sig != t_sig:
            return None

    # Validate newly added merged import names against newly added body defs/vars,
    # and protect existing base defs/vars/imports against cross-kind collisions (5b-1).
    base_import_set = set(base_mod.header_import_items)
    ours_import_set = set(ours_mod.header_import_items)
    theirs_import_set = set(theirs_mod.header_import_items)
    merged_import_names = {
        it.local_name for it in merged_import_set if it.local_name is not None
    }
    new_merged_import_names = {
        it.local_name
        for it in (merged_import_set - base_import_set)
        if it.local_name is not None
    }
    base_active_defs = {
        k[1]
        for k in base_mod.body_by_key
        if k[0] == "def" and (k in ours_mod.body_by_key or k in theirs_mod.body_by_key)
    }
    base_active_vars = {
        k[1]
        for k in base_mod.body_by_key
        if k[0] == "var" and (k in ours_mod.body_by_key or k in theirs_mod.body_by_key)
    }

    for new_imp_name in new_merged_import_names:
        if new_imp_name in base_active_defs or new_imp_name in base_active_vars:
            return None

    for branch_mod in (ours_mod, theirs_mod):
        for key, _ps in branch_mod.body_by_key.items():
            if key[0] in {"def", "var"} and key not in base_mod.body_by_key:
                sym_name = key[1]
                if sym_name in new_merged_import_names or sym_name in merged_import_names:
                    return None
                if key[0] == "def" and sym_name in base_active_vars:
                    return None
                if key[0] == "var" and sym_name in base_active_defs:
                    return None

    # Evaluate 3-way status of all base body keys.
    resolved_base_body: dict[tuple[str, str], tuple[str, str] | None] = {}
    for key, b_ps in base_mod.body_by_key.items():
        o_ps = ours_mod.body_by_key.get(key)
        t_ps = theirs_mod.body_by_key.get(key)
        o_dump = o_ps.ast_dump if o_ps is not None else None
        t_dump = t_ps.ast_dump if t_ps is not None else None

        if o_dump == b_ps.ast_dump and t_dump != b_ps.ast_dump:
            if t_ps is None:
                if _has_both_sides_new_stmt_of_kind(key[0], base_mod, ours_mod, theirs_mod):
                    return None
                resolved_base_body[key] = None
            else:
                resolved_base_body[key] = (_block_kind(t_ps.stmt), t_ps.raw_slice)
        elif t_dump == b_ps.ast_dump and o_dump != b_ps.ast_dump:
            if o_ps is None:
                if _has_both_sides_new_stmt_of_kind(key[0], base_mod, ours_mod, theirs_mod):
                    return None
                resolved_base_body[key] = None
            else:
                resolved_base_body[key] = (_block_kind(o_ps.stmt), o_ps.raw_slice)
        elif o_dump != b_ps.ast_dump and t_dump != b_ps.ast_dump:
            if o_dump != t_dump:
                return None
            if o_ps is None:
                resolved_base_body[key] = None
            else:
                resolved_base_body[key] = (_block_kind(o_ps.stmt), o_ps.raw_slice)
        else:
            assert o_ps is not None
            resolved_base_body[key] = (_block_kind(o_ps.stmt), o_ps.raw_slice)

    # Check expr/complex statements that disappeared from base in both ours and theirs.
    for key in base_mod.body_by_key:
        if key[0] in {"expr", "complex"}:
            if key not in ours_mod.body_by_key and key not in theirs_mod.body_by_key:
                ours_kind_set = {k for k in ours_mod.body_by_key if k[0] == key[0]}
                theirs_kind_set = {k for k in theirs_mod.body_by_key if k[0] == key[0]}
                if ours_kind_set != theirs_kind_set:
                    return None

    # Check newly added body keys in ours vs theirs.
    ours_added_keys = [k for k in ours_mod.body_keys if k not in base_mod.body_by_key]
    theirs_added_keys = [k for k in theirs_mod.body_keys if k not in base_mod.body_by_key]

    ours_new_complex = [k for k in ours_added_keys if k[0] == "complex"]
    theirs_new_complex = [k for k in theirs_added_keys if k[0] == "complex"]
    if ours_new_complex and theirs_new_complex and ours_new_complex != theirs_new_complex:
        return None

    ours_added_set = set(ours_added_keys)
    for k in theirs_added_keys:
        if k in ours_added_set:
            if ours_mod.body_by_key[k].ast_dump != theirs_mod.body_by_key[k].ast_dump:
                return None

    # 5d: Fail-closed on conflicting literal registration keys in newly added expr calls.
    ours_reg_calls: dict[tuple[str, str], str] = {}
    for k in ours_added_keys:
        if k[0] != "expr":
            continue
        ps = ours_mod.body_by_key[k]
        for reg_pair in _extract_expr_registration_keys(ps.stmt):
            prev_dump = ours_reg_calls.get(reg_pair)
            if prev_dump is not None and prev_dump != ps.ast_dump:
                return None
            ours_reg_calls[reg_pair] = ps.ast_dump
    for k in theirs_added_keys:
        if k[0] != "expr":
            continue
        ps = theirs_mod.body_by_key[k]
        for reg_pair in _extract_expr_registration_keys(ps.stmt):
            ours_dump = ours_reg_calls.get(reg_pair)
            if ours_dump is not None and ours_dump != ps.ast_dump:
                return None

    # 5b-2: Fail-closed if a removed base import is still loaded by surviving/new nodes.
    removed_ours_names = {
        it.local_name
        for it in (base_import_set - ours_import_set)
        if it.local_name is not None
    } - merged_import_names
    removed_theirs_names = {
        it.local_name
        for it in (base_import_set - theirs_import_set)
        if it.local_name is not None
    } - merged_import_names
    removed_import_names = removed_ours_names | removed_theirs_names

    # Assemble header, body, and tail blocks.
    header_blocks: list[tuple[str, str]] = []
    if ours_mod.leading_comments:
        header_blocks.append(("header_comment", ours_mod.leading_comments))
    elif theirs_mod.leading_comments:
        header_blocks.append(("header_comment", theirs_mod.leading_comments))

    if merged_doc is not None:
        header_blocks.append(("docstring", merged_doc))
    for fb in future_blocks:
        header_blocks.append(("future_import", fb))
    for rb in regular_import_blocks:
        header_blocks.append(("import", rb))

    theirs_extra_non_main: list[_AssembledBlock] = []
    theirs_extra_main: list[tuple[str, str]] = []
    for k in theirs_added_keys:
        if k in ours_added_set:
            continue
        ps = theirs_mod.body_by_key[k]
        if k[0] == "main_guard":
            theirs_extra_main.append((_block_kind(ps.stmt), ps.raw_slice))
        else:
            theirs_extra_non_main.append(
                _AssembledBlock(
                    kind=_block_kind(ps.stmt),
                    text=ps.raw_slice,
                    movable=k[0] in {"def", "var"},
                )
            )

    body_blocks: list[_AssembledBlock] = []
    emitted_all = False
    inserted_theirs_body = False
    main_guard_block: tuple[str, str] | None = None

    for item_type, item_key in ours_mod.all_stmts_non_import_order:
        if item_type == "all":
            emitted_all = True
            if merged_all is not None:
                body_blocks.append(_AssembledBlock(kind="stmt", text=merged_all))
            continue

        assert item_key is not None
        if item_key in resolved_base_body:
            entry = resolved_base_body[item_key]
            if entry is None:
                continue
        else:
            ps = ours_mod.body_by_key[item_key]
            entry = (_block_kind(ps.stmt), ps.raw_slice)

        if item_key[0] == "main_guard":
            if not inserted_theirs_body:
                body_blocks.extend(theirs_extra_non_main)
                inserted_theirs_body = True
            main_guard_block = entry
        else:
            body_blocks.append(_AssembledBlock(kind=entry[0], text=entry[1]))

    if not emitted_all and merged_all is not None:
        insert_pos = len(body_blocks)
        for idx, blk in enumerate(body_blocks):
            if blk.kind in {"def_or_class", "stmt", "call"}:
                insert_pos = idx
                break
        body_blocks.insert(insert_pos, _AssembledBlock(kind="stmt", text=merged_all))

    if not inserted_theirs_body:
        body_blocks.extend(theirs_extra_non_main)

    tail_blocks: list[tuple[str, str]] = []
    if main_guard_block is not None:
        tail_blocks.append(main_guard_block)
    elif theirs_extra_main:
        tail_blocks.extend(theirs_extra_main)

    # Check 5b-2 against all surviving body & tail blocks.
    if removed_import_names:
        for text_block in [b.text for b in body_blocks] + [t[1] for t in tail_blocks]:
            try:
                parsed_blk = ast.parse(text_block)
            except SyntaxError:
                return None
            for sub_stmt in parsed_blk.body:
                if _collect_load_names(sub_stmt) & removed_import_names:
                    return None

    # 5e-2: Relocate movable defs/classes/vars from theirs before first dependent stmt.
    has_future_annotations = any(
        it.is_future and it.name == "annotations" for it in merged_import_set
    )
    for _ in range(len(body_blocks) + 1):
        parsed_body_stmts: list[ast.stmt] = []
        for blk in body_blocks:
            try:
                blk_tree = ast.parse(blk.text)
            except SyntaxError:
                return None
            if len(blk_tree.body) != 1:
                return None
            parsed_body_stmts.append(blk_tree.body[0])

        fwd_ref = _find_top_level_forward_ref(
            parsed_body_stmts,
            initial_bound=merged_import_names,
            has_future_annotations=has_future_annotations,
        )
        if fwd_ref is None:
            break
        use_idx, needed_name = fwd_ref
        move_idx: int | None = None
        for cand_idx in range(use_idx + 1, len(body_blocks)):
            if body_blocks[cand_idx].movable and needed_name in _extract_stmt_bound_names(
                parsed_body_stmts[cand_idx]
            ):
                move_idx = cand_idx
                break
        if move_idx is None:
            break
        moved_block = body_blocks.pop(move_idx)
        body_blocks.insert(use_idx, moved_block)

    blocks: list[tuple[str, str]] = [
        *header_blocks,
        *((b.kind, b.text) for b in body_blocks),
        *tail_blocks,
    ]
    merged_text = _join_blocks(blocks)
    if merged_text and not merged_text.endswith("\n"):
        merged_text += "\n"

    try:
        merged_tree = ast.parse(merged_text)
    except SyntaxError:
        return None

    if (
        _find_top_level_forward_ref(
            merged_tree.body,
            initial_bound=set(),
            has_future_annotations=has_future_annotations,
        )
        is not None
    ):
        return None

    return merged_text


__all__ = [
    "is_python_registry_candidate",
    "merge_python_additive_registry",
]
