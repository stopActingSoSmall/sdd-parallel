"""Unit tests for scripts/sync_docs_mirror.py."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.sync_docs_mirror import (  # noqa: E402
    MANIFEST_FILENAME,
    SyncResult,
    calculate_sha256,
    collect_whitelisted_files,
    is_file_allowed,
    resolve_target_dir,
    sync_docs_mirror,
    validate_target_safety,
)


def test_is_file_allowed_blacklist() -> None:
    """Verify blacklist prevents code, bytecode, virtualenvs, and junk."""
    assert is_file_allowed(Path("README.md"))
    assert is_file_allowed(Path("phases/task_01.md"))
    assert is_file_allowed(Path("docs/marketing/MASTER_GTM_INDEX.md"))
    assert is_file_allowed(Path("assets/inspector.html"))

    # Blacklisted extensions
    assert not is_file_allowed(Path("src/sdd_parallel/cli.py"))
    assert not is_file_allowed(Path("scripts/sync_docs_mirror.py"))
    assert not is_file_allowed(Path("module.pyc"))
    assert not is_file_allowed(Path("temp.tmp"))
    assert not is_file_allowed(Path("phase.bak"))

    # Blacklisted directories
    assert not is_file_allowed(Path("__pycache__/cache.pyc"))
    assert not is_file_allowed(Path(".venv/lib/site.md"))
    assert not is_file_allowed(Path(".git/config"))
    assert not is_file_allowed(Path(".worktrees/wt1/README.md"))
    assert not is_file_allowed(Path("node_modules/pkg/README.md"))

    # Blacklisted filenames
    assert not is_file_allowed(Path("Thumbs.db"))
    assert not is_file_allowed(Path(".DS_Store"))
    assert not is_file_allowed(Path("desktop.ini"))


def test_resolve_target_dir_cli_precedence(tmp_path: Path) -> None:
    """Explicit CLI target directory overrides environment variable and default."""
    custom_dir = tmp_path / "custom-mirror"
    with patch.dict(os.environ, {"SDD_DRIVE_MIRROR_PATH": str(tmp_path / "env-mirror")}):
        resolved, reason = resolve_target_dir(custom_dir)
        assert resolved == custom_dir.resolve()
        assert reason == "cli_argument"


def test_resolve_target_dir_env_precedence(tmp_path: Path) -> None:
    """Environment variable overrides default Google Drive probing."""
    env_dir = tmp_path / "env-mirror"
    with patch.dict(os.environ, {"SDD_DRIVE_MIRROR_PATH": str(env_dir)}):
        resolved, reason = resolve_target_dir(None)
        assert resolved == env_dir.resolve()
        assert "env_variable" in reason


def test_resolve_target_dir_fallback() -> None:
    """When no CLI or env or G: drive, fall back to home _MIRRORS directory."""
    with patch.dict(os.environ, {}):
        os.environ.pop("SDD_DRIVE_MIRROR_PATH", None)
        with patch.object(Path, "is_dir", return_value=False):
            resolved, reason = resolve_target_dir(None)
            expected_fallback = Path.home() / "_MIRRORS" / "sdd-parallel-docs"
            assert resolved == expected_fallback.resolve()
            assert reason == "local_fallback"


def test_validate_target_safety(tmp_path: Path) -> None:
    """Ensure target path safety checks prevent catastrophic deletes."""
    repo = tmp_path / "repo"
    repo.mkdir()

    # Target equals repo
    with pytest.raises(ValueError, match="identical to repository root"):
        validate_target_safety(repo, repo)

    # Target inside repo
    target_inside = repo / "mirror"
    target_inside.mkdir()
    with pytest.raises(ValueError, match="cannot reside inside repository root"):
        validate_target_safety(target_inside, repo)

    # Target is parent of repo
    with pytest.raises(ValueError, match="cannot be a parent of repository root"):
        validate_target_safety(tmp_path, repo)

    # Target is an existing non-empty directory with unrelated files without manifest
    foreign_dir = tmp_path / "unrelated_dir"
    foreign_dir.mkdir()
    (foreign_dir / "random.txt").write_text("hello", encoding="utf-8")
    with pytest.raises(ValueError, match="Refusing to sync to existing non-empty directory"):
        validate_target_safety(foreign_dir, repo, force=False)

    # Overridden with force
    validate_target_safety(foreign_dir, repo, force=True)


def test_collect_whitelisted_files(tmp_path: Path) -> None:
    """Ensure whitelisted collector captures docs, specs, and inspector while ignoring code."""
    repo = tmp_path / "test_repo"
    repo.mkdir()

    # Create root markdown files
    (repo / "README.md").write_text("# Readme", encoding="utf-8")
    (repo / "MASTER_INDEX.md").write_text("# Master Index", encoding="utf-8")
    (repo / "llms.txt").write_text("llms context", encoding="utf-8")
    (repo / "main.py").write_text("print(1)", encoding="utf-8")  # should be excluded

    # Create phases
    phases = repo / "phases"
    phases.mkdir()
    (phases / "task_01.md").write_text("# Spec 1", encoding="utf-8")
    (phases / "helper.py").write_text("pass", encoding="utf-8")  # should be excluded

    # Create docs
    docs = repo / "docs" / "marketing"
    docs.mkdir(parents=True)
    (docs / "MASTER_GTM_INDEX.md").write_text("# GTM", encoding="utf-8")

    # Create assets
    assets = repo / "assets"
    assets.mkdir()
    (assets / "inspector.html").write_text("<html></html>", encoding="utf-8")
    (assets / "demo.tape").write_text("Type demo", encoding="utf-8")  # not in whitelist

    whitelisted = collect_whitelisted_files(repo)
    keys = set(whitelisted.keys())

    assert "README.md" in keys
    assert "MASTER_INDEX.md" in keys
    assert "llms.txt" in keys
    assert "phases/task_01.md" in keys
    assert "docs/marketing/MASTER_GTM_INDEX.md" in keys
    assert "assets/inspector.html" in keys

    # Exclusions
    assert "main.py" not in keys
    assert "phases/helper.py" not in keys
    assert "assets/demo.tape" not in keys


def test_sync_docs_mirror_full_lifecycle(tmp_path: Path) -> None:
    """Test full copy, idempotent rerun, update on edit, and prune on deletion."""
    repo = tmp_path / "mock_repo"
    repo.mkdir()
    target = tmp_path / "sdd-parallel-docs"

    (repo / "README.md").write_text("# Project", encoding="utf-8")
    phases = repo / "phases"
    phases.mkdir()
    task1 = phases / "task_01.md"
    task1.write_text("# Task 1", encoding="utf-8")

    # 1. Initial sync
    res1: SyncResult = sync_docs_mirror(repo_root=repo, target_dir=target)
    assert res1.copied == 2
    assert res1.updated == 0
    assert res1.deleted == 0
    assert res1.unchanged == 0
    assert (target / "README.md").is_file()
    assert (target / "phases" / "task_01.md").is_file()
    assert (target / MANIFEST_FILENAME).is_file()

    # 2. Idempotent second sync (no changes)
    res2 = sync_docs_mirror(repo_root=repo, target_dir=target)
    assert res2.copied == 0
    assert res2.updated == 0
    assert res2.deleted == 0
    assert res2.unchanged == 2

    # 3. Modify task_01.md
    task1.write_text("# Task 1 Modified", encoding="utf-8")
    res3 = sync_docs_mirror(repo_root=repo, target_dir=target)
    assert res3.copied == 0
    assert res3.updated == 1
    assert res3.deleted == 0
    assert res3.unchanged == 1
    assert (target / "phases" / "task_01.md").read_text(encoding="utf-8") == "# Task 1 Modified"

    # 4. Mirror purity: delete task_01.md in repo
    task1.unlink()
    res4 = sync_docs_mirror(repo_root=repo, target_dir=target)
    assert res4.deleted == 1
    assert not (target / "phases" / "task_01.md").exists()
    # Empty phases directory should be pruned
    assert not (target / "phases").exists()


def test_sync_docs_mirror_dry_run_and_check(tmp_path: Path) -> None:
    """Verify dry-run does not write and check mode detects drift."""
    repo = tmp_path / "mock_repo"
    repo.mkdir()
    target = tmp_path / "sdd-parallel-docs"

    (repo / "README.md").write_text("# Project", encoding="utf-8")

    # Check mode before sync: reports drift
    check_res = sync_docs_mirror(repo_root=repo, target_dir=target, check_only=True)
    assert check_res.has_drift is True
    assert check_res.copied == 1
    assert not target.exists()

    # Dry-run sync: does not create target
    dry_res = sync_docs_mirror(repo_root=repo, target_dir=target, dry_run=True)
    assert dry_res.copied == 1
    assert not target.exists()

    # Real sync
    sync_docs_mirror(repo_root=repo, target_dir=target)
    assert target.exists()

    # Check mode after sync: clean
    check_clean = sync_docs_mirror(repo_root=repo, target_dir=target, check_only=True)
    assert check_clean.has_drift is False
    assert check_clean.unchanged == 1


def test_calculate_sha256(tmp_path: Path) -> None:
    """Verify streaming SHA256 matches hashlib standard."""
    test_file = tmp_path / "test.txt"
    test_file.write_text("sdd-parallel docs mirror hash test", encoding="utf-8")
    digest = calculate_sha256(test_file)
    assert len(digest) == 64
