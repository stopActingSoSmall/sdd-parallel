"""Tests for Pre-Flight Epic Context Gate & Antigravity Worktree Layout."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from sdd_parallel.adapters.bmad import BMADAdapter
from sdd_parallel.cli import main
from sdd_parallel.hooks.blast_radius_guard import evaluate_tool_access
from sdd_parallel.models import EffortTier, FileRole, TaskNode, Wave, WaveTrack
from sdd_parallel.scheduler import BSPWaveScheduler
from sdd_parallel.worktree import (
    WorktreeManager,
    default_antigravity_worktrees_dir,
    read_parallel_state,
    resolve_checkpoint_gates,
)


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test Runner"], cwd=root, check=True)


def test_default_antigravity_worktrees_dir() -> None:
    expected = Path.home() / ".gemini" / "antigravity" / "worktrees" / "AgentLens"
    assert default_antigravity_worktrees_dir("AgentLens") == expected


def test_bmad_get_epics_for_wave_and_check_epic_contexts(tmp_path: Path) -> None:
    plan_dir = tmp_path / "_bmad-output" / "planning-artifacts"
    plan_dir.mkdir(parents=True)
    impl_dir = tmp_path / "_bmad-output" / "implementation-artifacts"
    impl_dir.mkdir(parents=True)

    (impl_dir / "sprint-status.yaml").write_text(
        "development_status:\n  11-1-auth: ready-for-dev\n", encoding="utf-8"
    )

    adapter = BMADAdapter(tmp_path)
    wave = Wave(
        wave_number=1,
        tracks=[
            WaveTrack(track_id="track-a", task_ids=["11-1", "11-2"], effort=EffortTier.MEDIUM),
            WaveTrack(track_id="track-b", task_ids=["12-1"], effort=EffortTier.LOW),
        ],
    )

    # 1. Verify get_epics_for_wave extracts {"11", "12"}
    epics = adapter.get_epics_for_wave(wave)
    assert epics == {"11", "12"}

    # 2. Both epic contexts missing
    missing = adapter.check_epic_contexts(wave)
    assert len(missing) == 2
    assert "_bmad-output/planning-artifacts/epic-11-context.md" in missing
    assert "_bmad-output/planning-artifacts/epic-12-context.md" in missing

    # 3. Verify check_wave_zero_blockers includes both
    blockers = adapter.check_wave_zero_blockers(wave)
    assert len(blockers) == 2
    assert any("epic-11-context.md" in b for b in blockers)
    assert any("epic-12-context.md" in b for b in blockers)
    assert any("Zero Cache Invalidation" in b or "Compile it on master" in b for b in blockers)

    # 4. Create epic-11-context.md
    (plan_dir / "epic-11-context.md").write_text("# Epic 11 Context\n", encoding="utf-8")
    missing_after_11 = adapter.check_epic_contexts(wave)
    assert missing_after_11 == ["_bmad-output/planning-artifacts/epic-12-context.md"]

    # 5. Create epic-12-context.md in implementation-artifacts (dual-path discovery)
    (impl_dir / "epic-12-context.md").write_text("# Epic 12 Context\n", encoding="utf-8")
    assert adapter.find_epic_context("12") == (impl_dir / "epic-12-context.md")
    assert adapter.check_epic_contexts(wave) == []
    assert adapter.check_wave_zero_blockers(wave) == []


def test_cli_prepare_blocks_when_epic_context_missing(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)

    plan_dir = tmp_path / "_bmad-output" / "planning-artifacts"
    plan_dir.mkdir(parents=True)
    impl_dir = tmp_path / "_bmad-output" / "implementation-artifacts"
    impl_dir.mkdir(parents=True)

    (impl_dir / "sprint-status.yaml").write_text(
        'last_updated: "10-20-2026 12:00"\n'
        "development_status:\n"
        "  11-1-auth: ready-for-dev\n"
        "  11-2-session: ready-for-dev\n",
        encoding="utf-8",
    )
    (plan_dir / "epics.md").write_text(
        "## Epic 11: Auth\n### Story 11-1: Auth\n### Story 11-2: Session\n",
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir(parents=True)
    (tmp_path / "src" / "auth.py").write_text("x = 1\n", encoding="utf-8")

    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=tmp_path, check=True)

    # 1. Run prepare without epic-11-context.md -> must block with exit code 1
    rc = main(["prepare", "--repo", str(tmp_path), "--wave", "1"])
    assert rc == 1
    assert not (tmp_path / ".worktrees").exists()

    # 2. Compile epic-11-context.md on master and commit
    (plan_dir / "epic-11-context.md").write_text(
        "# Pre-compiled Epic 11 Context\n", encoding="utf-8"
    )
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "docs: compile epic 11 context"],
        cwd=tmp_path,
        check=True,
    )

    # 3. Run prepare dry-run -> must succeed with exit code 0
    rc_dry = main(["prepare", "--repo", str(tmp_path), "--wave", "1", "--dry-run"])
    assert rc_dry == 0


def test_lock_injection_for_epic_contexts_and_hook_denial(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)

    plan_dir = tmp_path / "_bmad-output" / "planning-artifacts"
    plan_dir.mkdir(parents=True)
    impl_dir = tmp_path / "_bmad-output" / "implementation-artifacts"
    impl_dir.mkdir(parents=True)

    (impl_dir / "sprint-status.yaml").write_text(
        "development_status:\n  11-1-auth: ready-for-dev\n", encoding="utf-8"
    )
    (plan_dir / "epic-11-context.md").write_text("# Context 11\n", encoding="utf-8")
    (tmp_path / "src").mkdir(parents=True)
    (tmp_path / "src" / "auth.py").write_text("x = 1\n", encoding="utf-8")

    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True)

    tasks = [
        TaskNode(
            id="11-1",
            title="Auth",
            epic_id="epic-11",
            write_files=["src/auth.py"],
        )
    ]
    scheduler = BSPWaveScheduler(max_tracks=1)
    plan = scheduler.schedule(tasks)

    wm = WorktreeManager(tmp_path)
    wt_paths = wm.prepare_wave(plan, wave_number=1)
    assert len(wt_paths) == 1
    wt = wt_paths[0]

    # Verify .parallel-lock.json contains forbidden epic context
    lock_data = json.loads((wt / ".parallel-lock.json").read_text(encoding="utf-8"))
    forbidden = lock_data["forbidden_files"]
    assert "_bmad-output/planning-artifacts/epic-11-context.md" in forbidden
    assert (
        "Zero Cache Invalidation invariant"
        in forbidden["_bmad-output/planning-artifacts/epic-11-context.md"]
    )

    # Test PreToolUse blast-radius hook denies modification to epic-11-context.md
    decision = evaluate_tool_access(
        tool_name="write_to_file",
        tool_input={"TargetFile": "_bmad-output/planning-artifacts/epic-11-context.md"},
        cwd=wt,
    )
    assert decision.decision == "deny"
    assert decision.reason is not None
    assert "Zero Cache Invalidation invariant" in decision.reason

    # Clean up
    wm.cleanup_wave(plan, wave_number=1)


def test_antigravity_worktree_layout_and_cleanup(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_git_repo(repo_root)

    (repo_root / "src").mkdir()
    (repo_root / "src" / "app.py").write_text("x = 10\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo_root, check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=repo_root, check=True)

    custom_ag_dir = tmp_path / "ag_worktrees"
    wm = WorktreeManager(
        repo_root=repo_root,
        layout="antigravity",
        worktrees_dir=custom_ag_dir,
        fixed_slots=False,
    )

    tasks = [
        TaskNode(id="1-1", title="App Core", write_files=["src/app.py"]),
    ]
    scheduler = BSPWaveScheduler(max_tracks=1)
    plan = scheduler.schedule(tasks)

    # 1. Prepare wave with antigravity layout
    created_paths = wm.prepare_wave(plan, wave_number=1)
    assert len(created_paths) == 1
    wt_path = created_paths[0]

    # Must be placed under custom_ag_dir
    assert wt_path.parent == custom_ag_dir
    assert wt_path.is_dir()
    assert (wt_path / ".parallel-lock.json").is_file()
    assert (wt_path / ".tmp" / "track-context.md").is_file()
    assert (wt_path / "ACTIVE_TRACK.md").is_file()

    # 2. Local link in repo / .worktrees must exist and resolve to wt_path
    local_link = repo_root / ".worktrees" / "wave-1-track-a"
    assert local_link.exists()
    assert local_link.resolve() == wt_path.resolve()

    # 3. Clean up wave
    cleaned = wm.cleanup_wave(plan, wave_number=1)
    assert len(cleaned) > 0
    assert not local_link.exists()
    assert not wt_path.exists()

    # Repo root should remain healthy and intact
    assert repo_root.is_dir()
    proc = subprocess.run(["git", "status"], cwd=repo_root, capture_output=True, text=True)
    assert proc.returncode == 0


def test_cli_prepare_antigravity_layout_flag(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    plan_dir = tmp_path / "_bmad-output" / "planning-artifacts"
    plan_dir.mkdir(parents=True)
    impl_dir = tmp_path / "_bmad-output" / "implementation-artifacts"
    impl_dir.mkdir(parents=True)

    (impl_dir / "sprint-status.yaml").write_text(
        "development_status:\n  1-1-test: ready-for-dev\n", encoding="utf-8"
    )
    (plan_dir / "epics.md").write_text("## Epic 1: Test\n### Story 1-1: Test\n", encoding="utf-8")
    (plan_dir / "epic-1-context.md").write_text("# Context 1\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "test.py").write_text("a = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True)

    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(
            [
                "prepare",
                "--repo",
                str(tmp_path),
                "--wave",
                "1",
                "--dry-run",
                "--antigravity-layout",
            ]
        )

    assert rc == 0
    payload = json.loads(buf.getvalue())
    assert payload["wave"] == 1
    assert payload["dry_run"] is True
    assert payload["layout"] == "antigravity"
    assert any(".gemini/antigravity/worktrees" in p for p in payload["worktrees"])


def test_cli_autopilot_antigravity_layout_flag(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    plan_dir = tmp_path / "_bmad-output" / "planning-artifacts"
    plan_dir.mkdir(parents=True)
    impl_dir = tmp_path / "_bmad-output" / "implementation-artifacts"
    impl_dir.mkdir(parents=True)

    (impl_dir / "sprint-status.yaml").write_text(
        "development_status:\n  1-1-test: ready-for-dev\n", encoding="utf-8"
    )
    (plan_dir / "epics.md").write_text("## Epic 1: Test\n### Story 1-1: Test\n", encoding="utf-8")
    (plan_dir / "epic-1-context.md").write_text("# Context 1\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "test.py").write_text("a = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True)

    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(
            [
                "autopilot",
                "--repo",
                str(tmp_path),
                "--wave",
                "1",
                "--antigravity-layout",
            ]
        )

    assert rc == 0
    specs = json.loads(buf.getvalue())
    assert len(specs) > 0
    assert any(".gemini/antigravity/worktrees" in spec["worktree"] for spec in specs)


def test_fixed_track_slots_in_place_reset_and_read_only_locks(tmp_path: Path) -> None:
    """Test fixed track slots and OS read-only locks:
    - Fixed track slots (track-a/b/c) are preserved with in-place reset between waves.
    - Forbidden files are locked at OS level (chmod a-w -> PermissionError).
    - Previous locks are unlocked before reset, and git status --porcelain is 100% clean.
    """
    _init_git_repo(tmp_path)
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "alpha.py").write_text("a = 1\n", encoding="utf-8")
    (src_dir / "beta.py").write_text("b = 1\n", encoding="utf-8")
    (src_dir / "schema.py").write_text("SCHEMA_VERSION = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init master"], cwd=tmp_path, check=True)

    tasks_w1 = [
        TaskNode(id="1-1", title="Task Alpha", write_files=["src/alpha.py"]),
        TaskNode(id="1-2", title="Task Beta", write_files=["src/beta.py"]),
        TaskNode(id="2-1", title="Task Spine", write_files=["src/schema.py"], depends_on=["1-1"]),
        TaskNode(id="2-2", title="Task Beta 2", write_files=["src/beta.py"], depends_on=["1-2"]),
    ]
    scheduler = BSPWaveScheduler(
        max_tracks=2,
        file_roles={"src/schema.py": FileRole.SPINE},
    )
    plan_w1 = scheduler.schedule(tasks_w1)
    assert len(plan_w1.waves) == 2

    wm = WorktreeManager(tmp_path, fixed_slots=True)

    # 1. Prepare Wave 1
    wt_paths_w1 = wm.prepare_wave(plan_w1, wave_number=1)
    assert len(wt_paths_w1) == 2
    wt_a, wt_b = wt_paths_w1[0], wt_paths_w1[1]

    # Verify slot folder names are track-a, track-b
    assert wt_a.name == "track-a"
    assert wt_b.name == "track-b"

    # Verify 100% clean git status inside both slots
    for wt in (wt_a, wt_b):
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=wt,
            capture_output=True,
            text=True,
            check=True,
        )
        assert proc.stdout.strip() == ""

    # Verify OS Read-Only lock inside wt_a:
    # src/beta.py (owned by track-b) and src/schema.py (SPINE) must be read-only
    target_beta_in_a = wt_a / "src" / "beta.py"
    target_schema_in_a = wt_a / "src" / "schema.py"
    try:
        target_beta_in_a.write_text("hack beta\n", encoding="utf-8")
        assert False, "Expected PermissionError when writing to forbidden sibling file in track-a"
    except PermissionError:
        pass

    try:
        target_schema_in_a.write_text("hack schema\n", encoding="utf-8")
        assert False, "Expected PermissionError when writing to spine file in track-a"
    except PermissionError:
        pass

    # Exclusive write to src/alpha.py in wt_a must succeed
    (wt_a / "src" / "alpha.py").write_text("a = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/alpha.py"], cwd=wt_a, check=True)
    subprocess.run(["git", "commit", "-m", "feat(1-1): update alpha"], cwd=wt_a, check=True)

    # 2. Prepare Wave 2 using the same slots
    wt_paths_w2 = wm.prepare_wave(plan_w1, wave_number=2, base_branch="master")
    assert wt_paths_w2 == wt_paths_w1  # Paths stay identical without deletion

    # Both directories must still exist on disk (no Missing Folder)
    assert wt_a.is_dir()
    assert wt_b.is_dir()

    # In-place reset must make status 100% clean and reset to master
    for wt in (wt_a, wt_b):
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=wt,
            capture_output=True,
            text=True,
            check=True,
        )
        assert proc.stdout.strip() == ""

    # Verify that in-place reset restored original master content
    assert (wt_a / "src" / "alpha.py").read_text(encoding="utf-8") == "a = 1\n"


def test_verify_wave_defense_in_depth_working_tree_and_diff_gate(tmp_path: Path) -> None:
    """Verify that verify_wave audits working tree (git status) and committed diff against locks."""
    _init_git_repo(tmp_path)
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "alpha.py").write_text("a = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init master"], cwd=tmp_path, check=True)

    tasks = [TaskNode(id="1-1", title="Alpha", write_files=["src/alpha.py"])]
    scheduler = BSPWaveScheduler(max_tracks=1)
    plan = scheduler.schedule(tasks)

    wm = WorktreeManager(tmp_path, fixed_slots=True)
    wt_paths = wm.prepare_wave(plan, wave_number=1)
    wt = wt_paths[0]

    # Verify initially compliant
    violations = wm.verify_wave(plan, wave_number=1)
    assert violations == []

    # Modify unlisted file in working tree outside Allowed Write Scope
    (wt / "src" / "unlisted_rogue.py").write_text("rogue = True\n", encoding="utf-8")
    violations_dirty = wm.verify_wave(plan, wave_number=1)
    assert len(violations_dirty) > 0
    assert any("outside declared Allowed Write Scope" in v for v in violations_dirty)

    # Clean unlisted file -> compliant again
    (wt / "src" / "unlisted_rogue.py").unlink()
    assert wm.verify_wave(plan, wave_number=1) == []


def test_fixed_slots_parametric_pool_k_scalable(tmp_path: Path) -> None:
    """Test that fixed slots dynamically scale to K > 3 tracks (Invariant 7: Anti-Overfitting)."""
    _init_git_repo(tmp_path)
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True)
    for name in ("a", "b", "c", "d"):
        (src_dir / f"{name}.py").write_text(f"{name} = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init master"], cwd=tmp_path, check=True)

    tasks = [
        TaskNode(id="1-1", title="A", write_files=["src/a.py"]),
        TaskNode(id="1-2", title="B", write_files=["src/b.py"]),
        TaskNode(id="1-3", title="C", write_files=["src/c.py"]),
        TaskNode(id="1-4", title="D", write_files=["src/d.py"]),
    ]
    scheduler = BSPWaveScheduler(max_tracks=4)
    plan = scheduler.schedule(tasks)

    wm = WorktreeManager(tmp_path, fixed_slots=True)
    wt_paths = wm.prepare_wave(plan, wave_number=1)
    assert len(wt_paths) == 4

    # Slot names must be track-a, track-b, track-c, track-d
    slot_names = [p.name for p in wt_paths]
    assert slot_names == ["track-a", "track-b", "track-c", "track-d"]
    for p in wt_paths:
        assert p.is_dir()
        assert (p / ".parallel-lock.json").is_file()


def test_verify_wave_detects_violation_in_earlier_commit_of_multi_commit_track(
    tmp_path: Path,
) -> None:
    """Verify that verify_wave audits all commits against merge-base."""
    _init_git_repo(tmp_path)
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "alpha.py").write_text("a = 1\n", encoding="utf-8")
    (src_dir / "forbidden.py").write_text("f = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init master"], cwd=tmp_path, check=True)

    tasks = [TaskNode(id="1-1", title="Alpha", write_files=["src/alpha.py"])]
    scheduler = BSPWaveScheduler(max_tracks=1)
    plan = scheduler.schedule(tasks)

    wm = WorktreeManager(tmp_path, fixed_slots=True)
    wt_paths = wm.prepare_wave(plan, wave_number=1)
    wt = wt_paths[0]

    # Commit 1: Modify a forbidden file (outside allowed write globs)
    f_path = wt / "src" / "forbidden.py"
    f_path.chmod(0o777)
    f_path.write_text("f = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/forbidden.py"], cwd=wt, check=True)
    subprocess.run(["git", "commit", "-m", "commit 1 rogue change"], cwd=wt, check=True)

    # Commit 2: Modify only allowed file
    a_path = wt / "src" / "alpha.py"
    a_path.chmod(0o777)
    a_path.write_text("a = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/alpha.py"], cwd=wt, check=True)
    subprocess.run(["git", "commit", "-m", "commit 2 compliant change"], cwd=wt, check=True)

    violations = wm.verify_wave(plan, wave_number=1)
    assert len(violations) > 0
    assert any("forbidden.py" in v for v in violations)


def test_resolve_checkpoint_gates_priorities(tmp_path: Path) -> None:
    """Verify that resolve_checkpoint_gates respects priority order."""
    # Priority 3 fallback (empty repo)
    assert resolve_checkpoint_gates(tmp_path) == ["uv run pytest"]

    # Priority 3 Rust
    (tmp_path / "Cargo.toml").write_text("[package]\nname = 'test'\n", encoding="utf-8")
    assert resolve_checkpoint_gates(tmp_path) == ["cargo test"]
    (tmp_path / "Cargo.toml").unlink()

    # Priority 3 Node.js
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "jest"}}),
        encoding="utf-8",
    )
    assert resolve_checkpoint_gates(tmp_path) == ["npm test"]
    (tmp_path / "package.json").unlink()

    # Priority 2 pyproject.toml [tool.sdd-parallel]
    (tmp_path / "pyproject.toml").write_text(
        '[tool.sdd-parallel]\ncheckpoint_gates = ["my custom gate"]\n',
        encoding="utf-8",
    )
    assert resolve_checkpoint_gates(tmp_path) == ["my custom gate"]

    # Priority 1 .parallel-config.json overrides pyproject.toml
    (tmp_path / ".parallel-config.json").write_text(
        json.dumps({"checkpoint_gates": ["p1 gate"]}),
        encoding="utf-8",
    )
    assert resolve_checkpoint_gates(tmp_path) == ["p1 gate"]


def test_merge_wave_gracefully_skips_pytest_exit_code_5_when_no_tests_exist(
    tmp_path: Path,
) -> None:
    """Verify that merge_wave skips exit code 5 (no tests found) unless strict_gates."""
    _init_git_repo(tmp_path)
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "foo.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init master"], cwd=tmp_path, check=True)

    tasks = [TaskNode(id="1-1", title="Foo", write_files=["src/foo.py"])]
    scheduler = BSPWaveScheduler(max_tracks=1)
    plan = scheduler.schedule(tasks)
    plan.waves[0].checkpoint_gates = [f"{sys.executable} -m pytest"]

    wm = WorktreeManager(tmp_path, fixed_slots=True)
    wt_paths = wm.prepare_wave(plan, wave_number=1)
    wt = wt_paths[0]

    (wt / "src" / "foo.py").write_text("x = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/foo.py"], cwd=wt, check=True)
    subprocess.run(["git", "commit", "-m", "update foo"], cwd=wt, check=True)

    # 1. With strict_gates=False: graceful skip
    merged = wm.merge_wave(plan, wave_number=1, strict_gates=False)
    assert len(merged) == 1
    state = read_parallel_state(tmp_path)
    assert state["last_merged_wave_id"] == 1

    # 2. With strict_gates=True: must raise
    tasks_w2 = [TaskNode(id="2-1", title="Bar", write_files=["src/foo.py"])]
    plan_w2 = scheduler.schedule(tasks_w2)
    plan_w2.waves[0].wave_number = 2
    plan_w2.waves[0].checkpoint_gates = [f"{sys.executable} -m pytest"]
    wt_paths_w2 = wm.prepare_wave(plan_w2, wave_number=2)
    (wt_paths_w2[0] / "src" / "foo.py").write_text("x = 3\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/foo.py"], cwd=wt_paths_w2[0], check=True)
    subprocess.run(["git", "commit", "-m", "update foo w2"], cwd=wt_paths_w2[0], check=True)

    with pytest.raises(RuntimeError, match="Checkpoint quality gate"):
        wm.merge_wave(plan_w2, wave_number=2, strict_gates=True)


def test_merge_wave_transactional_auto_rollback_on_real_gate_failure(
    tmp_path: Path,
) -> None:
    """Verify that on quality gate failure, master is automatically rolled back."""
    _init_git_repo(tmp_path)
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "target.py").write_text("orig = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init master"], cwd=tmp_path, check=True)

    pre_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    tasks = [TaskNode(id="1-1", title="Target", write_files=["src/target.py"])]
    scheduler = BSPWaveScheduler(max_tracks=1)
    plan = scheduler.schedule(tasks)
    plan.waves[0].checkpoint_gates = [f'{sys.executable} -c "import sys; sys.exit(1)"']

    wm = WorktreeManager(tmp_path, fixed_slots=True)
    wt_paths = wm.prepare_wave(plan, wave_number=1)
    wt = wt_paths[0]

    (wt / "src" / "target.py").write_text("modified = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/target.py"], cwd=wt, check=True)
    subprocess.run(["git", "commit", "-m", "feature change"], cwd=wt, check=True)

    with pytest.raises(RuntimeError) as exc_info:
        wm.merge_wave(plan, wave_number=1)

    assert f"transactional auto-rollback to {pre_head[:7]} completed" in str(exc_info.value)

    # Verify master HEAD is restored to pre_head
    post_fail_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert post_fail_head == pre_head
    assert (tmp_path / "src" / "target.py").read_text(encoding="utf-8") == "orig = 1\n"
