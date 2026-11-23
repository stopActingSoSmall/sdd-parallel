"""Unit tests for ASTHotspotScanner spine buckets and namespace token extraction."""

from __future__ import annotations

from pathlib import Path

from sdd_parallel.ast_scanner import ASTHotspotScanner
from sdd_parallel.models import TaskNode, extract_spine_bucket


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
