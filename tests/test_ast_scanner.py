"""Unit tests for ASTHotspotScanner spine buckets and namespace token extraction."""

from __future__ import annotations

from pathlib import Path

from sdd_parallel.ast_scanner import ASTHotspotScanner
from sdd_parallel.models import FileRole, TaskNode, extract_spine_bucket


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
        "class Worker:\n"
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
    assert not scanner.are_functions_isolated("src/service.py", "independent_a", "missing_fn")
    assert not scanner.are_functions_isolated("src/unknown.py", "fn_a", "fn_b")
