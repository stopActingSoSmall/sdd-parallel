"""End-to-end tests for the standalone Merge-Bomb demo (`sdd-parallel demo`)."""

from __future__ import annotations

import io
import shutil
from pathlib import Path

from sdd_parallel.cli import main
from sdd_parallel.demo import run_demo
from sdd_parallel.models import FileRole
from sdd_parallel.worktree import _restore_os_writable


def test_run_demo_verifies_all_four_steps_and_cleans_temp() -> None:
    """Verify all 4 demo steps and automatic cleanup of the temporary directory."""
    buf = io.StringIO()
    result = run_demo(keep_temp=False, output=buf)
    output_text = buf.getvalue()

    # Step 1: AST & Hotspot Scan
    assert result.file_roles["src/demo_app/schema.py"] == FileRole.SPINE
    assert result.file_roles["src/demo_app/cli.py"] == FileRole.ADDITIVE_REGISTRY
    assert result.file_roles["src/demo_app/normalizer.py"] == FileRole.STANDARD
    assert result.file_roles["src/demo_app/api.py"] == FileRole.STANDARD
    assert "[Step 1/4]" in output_text

    # Step 2: Type-2 Semantic Merge-Bomb Detection & Schedule
    assert result.semantic_collision_detected is True
    assert set(result.plan.scheduled_task_ids) == {"1-1", "1-2", "1-3", "2-1", "2-2"}
    wave_1_tasks = set(result.plan.waves[0].all_task_ids)
    wave_2_tasks = set(result.plan.waves[1].all_task_ids)
    assert wave_1_tasks == {"1-2", "1-3"}
    assert "2-1" in wave_2_tasks
    assert "[Step 2/4]" in output_text

    # Step 3: Fixed-Slot Worktree Provisioning & Scope Guard Check
    assert [p.name for p in result.worktree_paths] == ["track-a", "track-b"]
    assert result.clean_git_status is True
    assert result.scope_guard_decision.decision == "deny"
    assert "Spine" in result.scope_guard_decision.reason
    assert "[Step 3/4]" in output_text

    # Step 4: Monotonic 3-Way Merge
    assert result.merged_branches == ["parallel/track-a", "parallel/track-b"]
    assert "<<<<<<<" not in result.merged_status_yaml
    assert "1-2-normalize-telemetry-payload: done" in result.merged_status_yaml
    assert "1-3-cli-batch-export: done" in result.merged_status_yaml
    assert "[Step 4/4]" in output_text
    assert "ALL 5 CORE INVARIANTS VERIFIED IN" in output_text

    # Automatic temporary directory cleanup when keep_temp=False
    assert not result.demo_repo.exists()


def test_cli_demo_keep_temp_preserves_directory(capsys) -> None:
    """Verify CLI `sdd-parallel demo` and `--keep-temp` flag."""
    buf = io.StringIO()
    result = run_demo(keep_temp=True, output=buf)
    try:
        assert result.demo_repo.exists()
        assert (result.demo_repo / ".git").exists()
    finally:
        _restore_os_writable(result.demo_repo)
        shutil.rmtree(result.demo_repo, ignore_errors=True)

    rc = main(["demo"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "ALL 5 CORE INVARIANTS VERIFIED IN" in captured.out


def test_demo_runs_for_all_supported_sdd_kits() -> None:
    """Verify `sdd-parallel demo --kit all` passes across all 5 frameworks."""
    buf = io.StringIO()
    result = run_demo(keep_temp=False, output=buf, kit="all")
    output_text = buf.getvalue()

    assert "5/5 SDD KITS VERIFIED" in output_text
    assert "bmad" in output_text
    assert "spec-kit" in output_text
    assert "gsd" in output_text
    assert "openspec" in output_text
    assert "beads" in output_text
    assert result.semantic_collision_detected is True
    assert result.clean_git_status is True
    assert result.scope_guard_decision.decision == "deny"


def test_demo_scaffold_only_flag(tmp_path: Path) -> None:
    """Verify demo --scaffold-only generates valid sandbox without running steps."""
    target_dir = tmp_path / "sandbox-spec-kit"
    buf = io.StringIO()
    res = run_demo(
        keep_temp=True,
        output=buf,
        kit="spec-kit",
        scaffold_only=True,
        output_dir=target_dir,
    )
    assert res.demo_repo.resolve() == target_dir.resolve()
    assert (target_dir / ".git").is_dir()
    assert (target_dir / "specs" / "001-telemetry" / "tasks.md").is_file()
    out = buf.getvalue()
    assert "SDD-PARALLEL DEMO SANDBOX CREATED (SPEC-KIT)" in out
    assert "Next steps for manual walkthrough:" in out
    assert "sdd-parallel plan --kit spec-kit" in out
