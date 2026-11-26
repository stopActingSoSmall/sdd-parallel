"""Unit tests for ASTHotspotScanner spine buckets and namespace token extraction."""

from __future__ import annotations

from pathlib import Path

from sdd_parallel.ast_diff_gate import check_virtual_import_cycles
from sdd_parallel.ast_scanner import ASTHotspotScanner
from sdd_parallel.models import (
    FileRole,
    TaskNode,
    extract_spine_bucket,
    matches_path_pattern,
    normalize_rel_path,
)


def test_extract_spine_bucket() -> None:
    """Verify spine bucket extraction for migrations, versions, ADRs, and standard files."""
    assert extract_spine_bucket("src/db/migrations/005_a.sql") == "src/db/migrations"
    assert extract_spine_bucket("src/db/migrations/v2/005_a.sql") == "src/db/migrations"
    assert extract_spine_bucket(r"src\db\versions\001_init.py") == "src/db/versions"
    assert extract_spine_bucket("docs/adr/0014-test.md") == "docs/adr"
    assert extract_spine_bucket("src/core/service.py") is None
    assert extract_spine_bucket("README.md") is None
    assert extract_spine_bucket("") is None


def test_extract_namespace_tokens() -> None:
    """Verify extraction and normalization of ENV, CLI flag, and HTTP route tokens."""
    text = (
        "Add support for APP_STRICT_MODE and --strict-schema flag. "
        "Ignore --help and --version meta options. "
        "Expose POST /v1/users/42, GET /v1/users/{id}, and DELETE /v1/items/:item_id."
    )
    tokens = ASTHotspotScanner.extract_namespace_tokens(text)

    assert "ns:ENV:APP_STRICT_MODE" in tokens
    assert "ns:CLI:--strict-schema" in tokens
    assert "ns:CLI:--help" not in tokens
    assert "ns:CLI:--version" not in tokens
    assert "ns:ROUTE:POST /v1/users/{param}" in tokens
    assert "ns:ROUTE:GET /v1/users/{param}" in tokens
    assert "ns:ROUTE:DELETE /v1/items/{param}" in tokens
    assert ASTHotspotScanner.extract_namespace_tokens("") == set()


def test_link_task_symbols_populates_namespace_tokens(tmp_path: Path) -> None:
    """Verify link_task_symbols populates namespace_tokens without breaking newly_added."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "sync_worker.py").write_text(
        "def run_sync() -> None:\n    pass\n",
        encoding="utf-8",
    )

    scanner = ASTHotspotScanner(tmp_path)
    scanner.scan()

    task_with_symbol = TaskNode(
        id="T1",
        title="Sync CLI",
        description="Update run_sync to support --strict-sync and SYNC_MODE.",
        write_files=[],
    )
    added = scanner.link_task_symbols(task_with_symbol)
    assert added == ["src/sync_worker.py"]
    assert task_with_symbol.write_files == ["src/sync_worker.py"]
    assert task_with_symbol.namespace_tokens == [
        "ns:CLI:--strict-sync",
        "ns:ENV:SYNC_MODE",
    ]

    task_tokens_only = TaskNode(
        id="T2",
        title="Add endpoint",
        description="Expose POST /v1/sync/99 with SYNC_TOKEN.",
        write_files=["src/routes.py"],
    )
    added_only = scanner.link_task_symbols(task_tokens_only)
    assert added_only == []
    assert task_tokens_only.namespace_tokens == [
        "ns:ENV:SYNC_TOKEN",
        "ns:ROUTE:POST /v1/sync/{param}",
    ]


def test_bare_get_or_register_decorator_keeps_standard_role(tmp_path: Path) -> None:
    """Bare decorators or non-router receivers stay STANDARD; router receivers are ADDITIVE."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "cache_service.py").write_text(
        "def register(fn):\n    return fn\n\n"
        "@register\n"
        "def build_cache() -> None:\n    pass\n\n"
        "@cache_client.get('k')\n"
        "def fetch_item() -> None:\n    pass\n\n"
        "@serializer.register\n"
        "def serialize_item() -> None:\n    pass\n",
        encoding="utf-8",
    )
    (src_dir / "routes.py").write_text(
        "@router.get('/items')\n"
        "def list_items() -> None:\n    pass\n\n"
        "@app.command('sync')\n"
        "def sync_cmd() -> None:\n    pass\n\n"
        "@mcp_server.tool()\n"
        "def tool_fn() -> None:\n    pass\n",
        encoding="utf-8",
    )

    scanner = ASTHotspotScanner(tmp_path)
    meta_map = scanner.scan()

    assert meta_map["src/cache_service.py"].role == FileRole.STANDARD
    assert meta_map["src/routes.py"].role == FileRole.ADDITIVE_REGISTRY


def test_extract_namespace_tokens_ignores_negated_constraints() -> None:
    """Negated clauses and read-only Markdown sections do not emit namespace tokens."""
    negated_text = (
        "Implement export. Do not touch --dry-run or GET /health. "
        "Keep APP_MODE unchanged.\n"
        "## Forbidden / Read-Only\n"
        "- POST /legacy/admin with LEGACY_TOKEN and --legacy-flag\n"
        "## Active Scope\n"
        "Add --export-json flag and POST /exports with EXPORT_DIR; "
        "не менять --keep-old и OLD_MODE."
    )
    tokens = ASTHotspotScanner.extract_namespace_tokens(negated_text)

    assert "ns:CLI:--dry-run" not in tokens
    assert "ns:ROUTE:GET /health" not in tokens
    assert "ns:ENV:APP_MODE" not in tokens
    assert "ns:ROUTE:POST /legacy/admin" not in tokens
    assert "ns:ENV:LEGACY_TOKEN" not in tokens
    assert "ns:CLI:--legacy-flag" not in tokens
    assert "ns:CLI:--keep-old" not in tokens
    assert "ns:ENV:OLD_MODE" not in tokens
    assert tokens == {
        "ns:CLI:--export-json",
        "ns:ROUTE:POST /exports",
        "ns:ENV:EXPORT_DIR",
    }


def test_extract_namespace_tokens_handles_headings_literals_and_inline_constraints() -> None:
    """Active headings, literal --read-only flags, and pre-constraint tokens are preserved."""
    text = (
        "## Add --export-json and POST /v2/reports\n"
        "Add --read-only.\n"
        "Add --strict-mode without changing --legacy-flag.\n"
        "Do not touch --read-only or GET /legacy.\n"
        "## Forbidden / Read-Only\n"
        "- Add --blocked-flag\n"
    )
    tokens = ASTHotspotScanner.extract_namespace_tokens(text)

    assert tokens == {
        "ns:CLI:--export-json",
        "ns:ROUTE:POST /v2/reports",
        "ns:CLI:--read-only",
        "ns:CLI:--strict-mode",
    }


def test_are_functions_isolated_ast_verification(tmp_path: Path) -> None:
    """Verify AST intra-file call graph checks existence, direct, transitive, and method calls."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "service.py").write_text(
        "def independent_a() -> int:\n"
        "    return 1\n\n"
        "def independent_b() -> int:\n"
        "    return 2\n\n"
        "def helper_c() -> int:\n"
        "    return 3\n\n"
        "def middle_b() -> int:\n"
        "    return helper_c()\n\n"
        "def caller_a() -> int:\n"
        "    return middle_b()\n\n"
        "def build_worker():\n"
        "    return Worker()\n\n"
        "def run_local_worker() -> int:\n"
        "    worker = Worker()\n"
        "    return worker.step_two()\n\n"
        "def run_dynamic_worker(factory) -> int:\n"
        "    obj = factory()\n"
        "    return obj.step_two()\n\n"
        "class Worker:\n"
        "    def __init__(self) -> None:\n"
        "        self.ready = True\n\n"
        "    def step_one(self) -> int:\n"
        "        return self.step_two()\n\n"
        "    def step_two(self) -> int:\n"
        "        return 42\n\n"
        "    def step_isolated(self) -> int:\n"
        "        return 99\n",
        encoding="utf-8",
    )

    scanner = ASTHotspotScanner(tmp_path)
    scanner.scan()

    assert scanner.are_functions_isolated("src/service.py", "independent_a", "independent_b")
    assert not scanner.are_functions_isolated("src/service.py", "caller_a", "middle_b")
    assert not scanner.are_functions_isolated("src/service.py", "caller_a", "helper_c")
    assert not scanner.are_functions_isolated("src/service.py", "step_one", "step_two")
    assert scanner.are_functions_isolated("src/service.py", "step_one", "step_isolated")
    assert not scanner.are_functions_isolated("src/service.py", "build_worker", "Worker.__init__")
    assert not scanner.are_functions_isolated(
        "src/service.py", "run_local_worker", "Worker.step_two"
    )
    assert not scanner.are_functions_isolated(
        "src/service.py", "run_dynamic_worker", "Worker.step_two"
    )
    assert scanner.are_functions_isolated("src/service.py", "run_local_worker", "step_isolated")
    assert not scanner.are_functions_isolated("src/service.py", "independent_a", "missing_fn")
    assert not scanner.are_functions_isolated("src/unknown.py", "fn_a", "fn_b")


def test_are_functions_isolated_retains_all_branch_receiver_classes(tmp_path: Path) -> None:
    """Branch assignments retain every possible receiver class without losing isolation."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "dispatch.py").write_text(
        "class First:\n"
        "    def step(self) -> int:\n"
        "        return 1\n\n"
        "    def unrelated(self) -> int:\n"
        "        return 10\n\n"
        "class Second:\n"
        "    def step(self) -> int:\n"
        "        return 2\n\n"
        "def dispatch(flag: bool) -> int:\n"
        "    if flag:\n"
        "        worker = First()\n"
        "    else:\n"
        "        worker = Second()\n"
        "    return worker.step()\n",
        encoding="utf-8",
    )

    scanner = ASTHotspotScanner(tmp_path)
    scanner.scan()

    assert not scanner.are_functions_isolated("src/dispatch.py", "dispatch", "First.step")
    assert not scanner.are_functions_isolated("src/dispatch.py", "dispatch", "Second.step")
    assert scanner.are_functions_isolated("src/dispatch.py", "dispatch", "First.unrelated")


def test_are_functions_isolated_is_fail_closed_on_dynamic_calls(tmp_path: Path) -> None:
    """Aliases and unknown-receiver branches never yield a false 'isolated' verdict."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "dyn.py").write_text(
        "import os\nfrom plugins import factory\n\n"
        "class First:\n"
        "    def step(self) -> int:\n        return 1\n\n"
        "class Second:\n"
        "    def step(self) -> int:\n        return 2\n\n"
        "def helper() -> int:\n    return 1\n\n"
        "def pure() -> int:\n    return len(os.sep)\n\n"
        "def caller() -> int:\n    target = helper\n    return target()\n\n"
        "def dispatch(flag: bool) -> int:\n"
        "    if flag:\n        worker = First()\n"
        "    else:\n        worker = factory()\n"
        "    return worker.step()\n",
        encoding="utf-8",
    )
    scanner = ASTHotspotScanner(tmp_path)
    scanner.scan()

    assert not scanner.are_functions_isolated("src/dyn.py", "caller", "helper")
    assert not scanner.are_functions_isolated("src/dyn.py", "dispatch", "Second.step")
    assert scanner.are_functions_isolated("src/dyn.py", "pure", "helper")


def test_are_functions_isolated_handles_subscript_callee_and_inheritance(tmp_path: Path) -> None:
    """Subscript callees fail closed; inherited/overridden methods keep call edges."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "inh.py").write_text(
        "def helper() -> int:\n    return 1\n\n"
        "def caller(callbacks) -> int:\n    return callbacks[0]()\n\n"
        "def other() -> int:\n    return 2\n\n"
        "class Base:\n"
        "    def step(self) -> int:\n        return 1\n\n"
        "    def solo(self) -> int:\n        return 3\n\n"
        "class Child(Base):\n"
        "    def run(self) -> int:\n        return self.step()\n",
        encoding="utf-8",
    )
    scanner = ASTHotspotScanner(tmp_path)
    scanner.scan()

    assert not scanner.are_functions_isolated("src/inh.py", "caller", "helper")
    assert not scanner.are_functions_isolated("src/inh.py", "Child.run", "Base.step")
    assert scanner.are_functions_isolated("src/inh.py", "Child.run", "Base.solo")
    assert scanner.are_functions_isolated("src/inh.py", "other", "helper")


def test_matches_path_pattern_zero_and_multi_segments() -> None:
    """Verify normalize_rel_path and 0/1/N directory matching for /**/ glob patterns."""
    assert normalize_rel_path("./pkg//sub/./models.py") == "pkg/sub/models.py"
    assert matches_path_pattern("pkg/contracts/service.py", "pkg/contracts/**/service.py") is True
    assert (
        matches_path_pattern(
            "pkg/contracts/v1/deep/service.py",
            "pkg/contracts/**/service.py",
        )
        is True
    )
    assert matches_path_pattern("_bmad-output/stories.yaml", "_bmad-output/**/stories.yaml") is True
    assert matches_path_pattern("pkg/contracts/other.py", "pkg/contracts/**/service.py") is False
    assert matches_path_pattern("pkg/service.py", "pkg/contracts/**/service.py") is False


def test_ast_scanner_resolves_relative_imports_and_virtual_cycles(tmp_path: Path) -> None:
    """Unchanged relative ImportFrom edges participate in get_importers and virtual cycle checks."""
    repo = tmp_path / "repo"
    (repo / "src" / "pkg").mkdir(parents=True)
    (repo / "src" / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "src" / "pkg" / "a.py").write_text("VAL_A = 1\n", encoding="utf-8")
    (repo / "src" / "pkg" / "b.py").write_text(
        "from . import c\n\nVAL_B = c.VAL_C + 1\n",
        encoding="utf-8",
    )
    (repo / "src" / "pkg" / "c.py").write_text("VAL_C = 3\n", encoding="utf-8")

    scanner = ASTHotspotScanner(repo)
    scanner.scan()

    assert scanner.get_importers("src/pkg/c.py") == {"src/pkg/b.py"}
    assert scanner.get_importers("./src/pkg/c.py") == {"src/pkg/b.py"}

    wt_a = tmp_path / "wt_a"
    (wt_a / "src" / "pkg").mkdir(parents=True)
    (wt_a / "src" / "pkg" / "a.py").write_text(
        "from . import b\n\nVAL_A = b.VAL_B + 1\n",
        encoding="utf-8",
    )

    wt_b = tmp_path / "wt_b"
    (wt_b / "src" / "pkg").mkdir(parents=True)
    (wt_b / "src" / "pkg" / "c.py").write_text(
        "from . import a\n\nVAL_C = a.VAL_A + 1\n",
        encoding="utf-8",
    )

    findings = check_virtual_import_cycles(
        repo_root=repo,
        base_scanner=scanner,
        track_worktrees={"track-a": wt_a, "track-b": wt_b},
        track_changed_files={
            "track-a": ["src/pkg/a.py"],
            "track-b": ["src/pkg/c.py"],
        },
    )
    assert len(findings) == 1
    finding = findings[0]
    assert finding.category == "IMPORT_CYCLE"
    assert set(finding.tracks) == {"track-a", "track-b"}
    assert set(finding.files) == {"src/pkg/a.py", "src/pkg/b.py", "src/pkg/c.py"}


def test_extract_namespace_tokens_short_cli_flags() -> None:
    """Short 2-char long CLI flags like --id and --db are captured; --, ---, --help ignored."""
    text = (
        "Add --id and --db flags to query command. "
        "Pass -- separator and --- divider, ignore --help and --version."
    )
    tokens = ASTHotspotScanner.extract_namespace_tokens(text)
    assert tokens == {"ns:CLI:--id", "ns:CLI:--db"}
