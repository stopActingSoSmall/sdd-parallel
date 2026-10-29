"""Unit and integration tests for Wave Lifecycle & Rollback Engine."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from sdd_parallel.adapters.bmad import BMADAdapter
from sdd_parallel.adapters.gsd_hijack import GSDHijackAdapter
from sdd_parallel.adapters.spec_kit import SpecKitAdapter
from sdd_parallel.cli import main
from sdd_parallel.models import EffortTier, Wave, WaveTrack
from sdd_parallel.worktree import (
    LIFORollbackViolationError,
    WorktreeManager,
    WorktreeRollbackManager,
    read_parallel_state,
    write_parallel_state,
)


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test Runner"], cwd=root, check=True)


def test_bmad_adapter_reset_tasks_status_and_removes_specs(tmp_path: Path) -> None:
    """Verify BMADAdapter.reset_tasks_status updates YAML and unlinks spec-*.md."""
    impl_dir = tmp_path / "_bmad-output" / "implementation-artifacts"
    impl_dir.mkdir(parents=True)

    status_file = impl_dir / "sprint-status.yaml"
    status_file.write_text(
        'last_updated: "10-21-2026 10:00"\n'
        "development_status:\n"
        "  11-1-auth: done\n"
        "  11-2-session: in-progress\n"
        "  12-1-token: done\n",
        encoding="utf-8",
    )
    spec_11_1 = impl_dir / "spec-11-1-auth.md"
    spec_11_1.write_text("# Spec 11-1\n", encoding="utf-8")
    spec_11_2 = impl_dir / "spec-11-2-session.md"
    spec_11_2.write_text("# Spec 11-2\n", encoding="utf-8")
    spec_12_1 = impl_dir / "spec-12-1-token.md"
    spec_12_1.write_text("# Spec 12-1\n", encoding="utf-8")

    adapter = BMADAdapter(tmp_path)
    adapter.reset_tasks_status(["11-1", "11-2"], target_status="ready-for-dev")

    content = status_file.read_text(encoding="utf-8")
    assert "  11-1-auth: ready-for-dev" in content
    assert "  11-2-session: ready-for-dev" in content
    assert "  12-1-token: done" in content

    assert not spec_11_1.exists()
    assert not spec_11_2.exists()
    assert spec_12_1.exists()


def test_spec_kit_adapter_reset_tasks_status(tmp_path: Path) -> None:
    """Verify SpecKitAdapter.reset_tasks_status clears checkboxes to [ ]."""
    spec_dir = tmp_path / ".specify" / "specs" / "feature-a"
    spec_dir.mkdir(parents=True)
    tasks_file = spec_dir / "tasks.md"
    tasks_file.write_text(
        "- [x] T001 [P] Setup backend\n- [X] T002 Configure DB\n- [x] T003 Frontend layout\n",
        encoding="utf-8",
    )

    adapter = SpecKitAdapter(tmp_path)
    adapter.reset_tasks_status(["T001", "T002"])

    content = tasks_file.read_text(encoding="utf-8")
    assert "- [ ] T001 [P] Setup backend" in content
    assert "- [ ] T002 Configure DB" in content
    assert "- [x] T003 Frontend layout" in content


def test_gsd_hijack_adapter_reset_tasks_status(tmp_path: Path) -> None:
    """Verify GSDHijackAdapter.reset_tasks_status unchecks STATE.md and unlinks SUMMARY.md."""
    planning = tmp_path / ".planning"
    phases = planning / "phases" / "01-core"
    phases.mkdir(parents=True)

    state_file = planning / "STATE.md"
    state_file.write_text(
        "- [x] 01-01 (Completed in Wave 1)\n"
        "- [x] 01-02 (Completed in Wave 1)\n"
        "- [x] 02-01 (Completed in Wave 2)\n",
        encoding="utf-8",
    )
    summary_file = phases / "01-01-SUMMARY.md"
    summary_file.write_text("# Summary 01-01\n", encoding="utf-8")

    adapter = GSDHijackAdapter(tmp_path)
    adapter.reset_tasks_status(["01-01"])

    content = state_file.read_text(encoding="utf-8")
    assert "- [ ] 01-01 (Completed in Wave 1)" in content
    assert "- [x] 01-02 (Completed in Wave 1)" in content
    assert not summary_file.exists()


def test_abort_wave_removes_worktrees_and_parallel_branches(tmp_path: Path) -> None:
    """Verify abort_wave cleans all worktrees and parallel/wave-K-* git branches."""
    _init_git_repo(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True)

    # Create dummy branch parallel/wave-1-track-a and parallel/wave-1-track-b
    subprocess.run(["git", "branch", "parallel/wave-1-track-a"], cwd=tmp_path, check=True)
    subprocess.run(["git", "branch", "parallel/wave-1-track-b"], cwd=tmp_path, check=True)

    # Create dummy worktree
    wt_dir = tmp_path / ".worktrees" / "wave-1-track-a"
    subprocess.run(
        ["git", "worktree", "add", str(wt_dir), "parallel/wave-1-track-a"],
        cwd=tmp_path,
        check=True,
    )
    assert wt_dir.is_dir()

    rm = WorktreeRollbackManager(tmp_path)
    deleted = rm.abort_wave(wave_id=1)

    assert "parallel/wave-1-track-a" in deleted
    assert "parallel/wave-1-track-b" in deleted
    assert not wt_dir.exists()

    # Check git branches
    branches = subprocess.run(
        ["git", "branch"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "parallel/wave-1-track-a" not in branches
    assert "parallel/wave-1-track-b" not in branches


def test_rollback_wave_enforces_lifo_order(tmp_path: Path) -> None:
    """Verify LIFORollbackViolationError if attempting to rollback wave K when K+1 merged."""
    _init_git_repo(tmp_path)
    (tmp_path / "file.txt").write_text("a", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True)

    # Fake state with Wave 1 and Wave 2 merged
    state = {
        "last_merged_wave_id": 2,
        "run_counter": 2,
        "history": [
            {"wave_number": 1, "pre_merge_commit": "c1", "task_ids": ["1-1"]},
            {"wave_number": 2, "pre_merge_commit": "c2", "task_ids": ["2-1"]},
        ],
    }
    write_parallel_state(tmp_path, state)

    rm = WorktreeRollbackManager(tmp_path)

    # Attempt to rollback Wave 1 while Wave 2 is latest
    with pytest.raises(LIFORollbackViolationError, match="LIFO rollback order violation"):
        rm.rollback_wave(wave_id=1)


def test_e2e_merge_and_rollback_wave_with_backup(tmp_path: Path) -> None:
    """E2E test: prepare wave, merge wave, verify state.json, and rollback with backup branch."""
    _init_git_repo(tmp_path)

    impl_dir = tmp_path / "_bmad-output" / "implementation-artifacts"
    impl_dir.mkdir(parents=True)
    status_file = impl_dir / "sprint-status.yaml"
    status_file.write_text(
        'last_updated: "10-21-2026 10:00"\ndevelopment_status:\n  1-1-first: ready-for-dev\n',
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "alpha.py").write_text("v = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init commit"], cwd=tmp_path, check=True)

    # Commit hash before wave merge
    init_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    # Plan with 1 wave, 1 track
    from sdd_parallel.models import WavePlan

    plan = WavePlan(
        sdd_kit="bmad",
        waves=[
            Wave(
                wave_number=1,
                tracks=[
                    WaveTrack(
                        track_id="track-a",
                        task_ids=["1-1"],
                        effort=EffortTier.LOW,
                        exclusive_write_files=["src/alpha.py"],
                    )
                ],
            )
        ],
        edges=[],
    )

    wm = WorktreeManager(tmp_path)
    wt_paths = wm.prepare_wave(plan, wave_number=1, headless=True)
    assert len(wt_paths) == 1
    wt_a = wt_paths[0]

    # Worker completes story in worktree
    (wt_a / "src" / "alpha.py").write_text("v = 2\n", encoding="utf-8")
    (wt_a / "_bmad-output" / "implementation-artifacts" / "sprint-status.yaml").write_text(
        'last_updated: "10-21-2026 11:00"\ndevelopment_status:\n  1-1-first: done\n',
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "add", "src/alpha.py", "_bmad-output/implementation-artifacts/sprint-status.yaml"],
        cwd=wt_a,
        check=True,
    )
    subprocess.run(["git", "commit", "-m", "feat(1-1): done"], cwd=wt_a, check=True)

    # Merge Wave 1
    merged = wm.merge_wave(plan, wave_number=1, run_quality_gates=False, cleanup_worktrees=True)
    assert len(merged) == 1

    # Verify state.json registered Wave 1
    state = read_parallel_state(tmp_path)
    assert state["last_merged_wave_id"] == 1
    assert len(state["history"]) == 1
    assert state["history"][0]["pre_merge_commit"] == init_head

    # Now execute rollback via CLI
    exit_code = main(["rollback-wave", "--repo", str(tmp_path), "--wave", "1", "--save-backup"])
    assert exit_code == 0

    # Verify HEAD is restored to init_head
    current_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert current_head == init_head
    assert (tmp_path / "src" / "alpha.py").read_text(encoding="utf-8") == "v = 1\n"

    # Verify sprint-status.yaml reset to ready-for-dev
    status_after_rollback = status_file.read_text(encoding="utf-8")
    assert "  1-1-first: ready-for-dev" in status_after_rollback

    # Verify backup branch exists
    branches = subprocess.run(
        ["git", "branch"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "backup/wave-1-before-rollback-" in branches

    # Verify state.json cleared
    state_after = read_parallel_state(tmp_path)
    assert state_after["last_merged_wave_id"] is None
    assert len(state_after["history"]) == 0
