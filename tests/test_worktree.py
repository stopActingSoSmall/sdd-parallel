"""Unit and integration tests for Zero-Pollution Environment Sync & Windows NTFS Junctions."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from sdd_parallel.models import EffortTier, Wave, WavePlan, WaveTrack
from sdd_parallel.worktree import (
    WorktreeManager,
    compute_track_port,
    create_directory_link,
    load_parallel_config,
    remove_directory_link,
    sync_environment,
)


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test Runner"], cwd=root, check=True)
    dummy_file = root / "README.md"
    dummy_file.write_text("# Test Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True)


def test_compute_track_port() -> None:
    # Standard letters: track-a -> 8101, track-b -> 8102, track-c -> 8103
    assert compute_track_port("track-a") == 8101
    assert compute_track_port("track-b") == 8102
    assert compute_track_port("track-c") == 8103

    # Numeric track suffixes
    assert compute_track_port("track-1") == 8101
    assert compute_track_port("track-2") == 8102

    # Custom base port
    assert compute_track_port("track-a", base_port=9000) == 9001
    assert compute_track_port("track-b", base_port=9000) == 9002

    # Fallback to track_index for arbitrary names
    assert compute_track_port("custom-slot", track_index=3, base_port=8100) == 8103


def test_create_and_remove_directory_link(tmp_path: Path) -> None:
    target_dir = tmp_path / "target_pkg"
    target_dir.mkdir()
    (target_dir / "marker.txt").write_text("package-content", encoding="utf-8")

    link_dir = tmp_path / "slot" / "target_pkg"
    create_directory_link(target=target_dir, link=link_dir)

    assert link_dir.exists()
    assert (link_dir / "marker.txt").read_text(encoding="utf-8") == "package-content"

    if sys.platform == "win32":
        assert hasattr(link_dir, "is_junction") and link_dir.is_junction()
    else:
        assert link_dir.is_symlink()

    # Verify safe removal: removes link, target and target contents remain intact
    remove_directory_link(link_dir)
    assert not link_dir.exists()
    assert target_dir.exists()
    assert (target_dir / "marker.txt").exists()


def test_sync_environment_files_and_directories(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".venv").mkdir()
    (repo / ".venv" / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "pkg.json").write_text('{"name": "test"}', encoding="utf-8")
    (repo / ".cache").mkdir()
    (repo / ".env").write_text("DATABASE_URL=sqlite:///app.db\n", encoding="utf-8")
    (repo / ".env.local").write_text("LOCAL_SECRET=xyz\n", encoding="utf-8")

    slot = tmp_path / "slot"
    slot.mkdir()

    synced = sync_environment(repo, slot)
    assert set(synced) == {".venv", "node_modules", ".cache", ".env", ".env.local"}

    # Validate contents in slot
    assert (slot / ".venv" / "pyvenv.cfg").is_file()
    assert (slot / "node_modules" / "pkg.json").is_file()
    assert (slot / ".cache").is_dir()
    assert (slot / ".env").read_text(encoding="utf-8") == "DATABASE_URL=sqlite:///app.db\n"
    assert (slot / ".env.local").read_text(encoding="utf-8") == "LOCAL_SECRET=xyz\n"


def test_load_parallel_config(tmp_path: Path) -> None:
    # 1. pyproject.toml [tool.sdd-parallel]
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        """
[tool.sdd-parallel]
base_port = 8500
checkpoint_gates = ["pytest"]

[tool.sdd-parallel.hooks]
post_provision = "echo from_toml"
""",
        encoding="utf-8",
    )

    cfg = load_parallel_config(tmp_path)
    assert cfg["base_port"] == 8500
    assert cfg["checkpoint_gates"] == ["pytest"]
    assert cfg["hooks"]["post_provision"] == "echo from_toml"

    # 2. .parallel-config.json overrides and merges hooks
    cfg_json = tmp_path / ".parallel-config.json"
    cfg_json.write_text(
        json.dumps(
            {
                "base_port": 8900,
                "hooks": {
                    "pre_verify": "python check.py",
                },
            }
        ),
        encoding="utf-8",
    )

    merged = load_parallel_config(tmp_path)
    assert merged["base_port"] == 8900
    assert merged["hooks"]["post_provision"] == "echo from_toml"
    assert merged["hooks"]["pre_verify"] == "python check.py"


def test_prepare_wave_zero_pollution_and_clean_git_status(tmp_path: Path) -> None:
    repo = tmp_path / "clean_repo"
    repo.mkdir()
    _init_git_repo(repo)

    # Create dummy .venv and .env in main repo
    (repo / ".venv").mkdir()
    (repo / ".venv" / "pyvenv.cfg").write_text("home = test\n", encoding="utf-8")
    (repo / ".env").write_text("SECRET=42\n", encoding="utf-8")

    plan = WavePlan(
        sdd_kit="bmad",
        waves=[
            Wave(
                wave_number=1,
                tracks=[
                    WaveTrack(track_id="track-a", task_ids=["1-1"], effort=EffortTier.LOW),
                    WaveTrack(track_id="track-b", task_ids=["1-2"], effort=EffortTier.LOW),
                ],
            )
        ],
        edges=[],
    )

    wm = WorktreeManager(repo, fixed_slots=True)
    paths = wm.prepare_wave(plan, wave_number=1)
    assert len(paths) == 2

    wt_a = paths[0]
    wt_b = paths[1]

    # Verify dependency and env files exist in track-a
    assert (wt_a / ".venv" / "pyvenv.cfg").is_file()
    assert (wt_a / ".env").is_file()

    # Invariant: Zero Worktree Pollution -> `git status --porcelain` MUST be empty
    st_proc = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=wt_a,
        capture_output=True,
        text=True,
        check=True,
    )
    assert st_proc.stdout.strip() == "", f"Pollution detected in worktree: {st_proc.stdout}"

    # Verify dynamic port isolation
    lock_a = json.loads((wt_a / ".parallel-lock.json").read_text(encoding="utf-8"))
    lock_b = json.loads((wt_b / ".parallel-lock.json").read_text(encoding="utf-8"))
    assert lock_a["env_vars"]["PORT"] == "8101"
    assert lock_b["env_vars"]["PORT"] == "8102"

    # Verify PORT is reflected in track-context.md and ACTIVE_TRACK.md
    context_a = (wt_a / ".tmp" / "track-context.md").read_text(encoding="utf-8")
    active_a = (wt_a / "ACTIVE_TRACK.md").read_text(encoding="utf-8")
    assert "PORT=8101" in context_a
    assert "PORT=8101" in active_a

    # Cleanup without damaging repo's .venv
    wm.cleanup_wave(plan, wave_number=1, force=True)
    assert (repo / ".venv" / "pyvenv.cfg").is_file()


def test_prepare_wave_lifecycle_hook_post_provision(tmp_path: Path) -> None:
    repo = tmp_path / "hook_repo"
    repo.mkdir()
    _init_git_repo(repo)

    # Configure post_provision hook to write a file in the slot
    cfg_json = repo / ".parallel-config.json"
    cfg_json.write_text(
        json.dumps(
            {
                "hooks": {
                    "post_provision": (
                        f'"{sys.executable}" -c '
                        '"import pathlib; pathlib.Path(\'hook_ran.txt\').write_text(\'yes\')"'
                    )
                }
            }
        ),
        encoding="utf-8",
    )

    plan = WavePlan(
        sdd_kit="bmad",
        waves=[
            Wave(
                wave_number=1,
                tracks=[WaveTrack(track_id="track-a", task_ids=["1-1"], effort=EffortTier.LOW)],
            )
        ],
        edges=[],
    )

    wm = WorktreeManager(repo, fixed_slots=True)
    paths = wm.prepare_wave(plan, wave_number=1)
    wt_a = paths[0]

    assert (wt_a / "hook_ran.txt").is_file()
    assert (wt_a / "hook_ran.txt").read_text(encoding="utf-8") == "yes"

    # Test failing post_provision hook raises RuntimeError
    cfg_json.write_text(
        json.dumps(
            {
                "hooks": {
                    "post_provision": f'"{sys.executable}" -c "import sys; sys.exit(42)"',
                }
            }
        ),
        encoding="utf-8",
    )
    wm_failing = WorktreeManager(repo, fixed_slots=True)
    with pytest.raises(RuntimeError, match="Lifecycle hook 'post_provision' failed"):
        wm_failing.prepare_wave(plan, wave_number=1)


def test_verify_wave_lifecycle_hook_pre_verify(tmp_path: Path) -> None:
    repo = tmp_path / "pre_verify_repo"
    repo.mkdir()
    _init_git_repo(repo)

    plan = WavePlan(
        sdd_kit="bmad",
        waves=[
            Wave(
                wave_number=1,
                tracks=[WaveTrack(track_id="track-a", task_ids=["1-1"], effort=EffortTier.LOW)],
            )
        ],
        edges=[],
    )

    wm = WorktreeManager(repo, fixed_slots=True)
    wm.prepare_wave(plan, wave_number=1)

    # 1. Successful pre_verify hook
    cfg_json = repo / ".parallel-config.json"
    cfg_json.write_text(
        json.dumps(
            {
                "hooks": {
                    "pre_verify": f'"{sys.executable}" -c "import sys; sys.exit(0)"',
                }
            }
        ),
        encoding="utf-8",
    )
    violations = wm.verify_wave(plan, wave_number=1)
    assert not any("lifecycle hook 'pre_verify'" in v for v in violations)

    # 2. Failing pre_verify hook records violation
    cfg_json.write_text(
        json.dumps(
            {
                "hooks": {
                    "pre_verify": (
                        f'"{sys.executable}" -c '
                        '"import sys; sys.stderr.write(\'lint failure\\n\'); sys.exit(7)"'
                    ),
                }
            }
        ),
        encoding="utf-8",
    )
    wm_failing = WorktreeManager(repo, fixed_slots=True)
    violations_failing = wm_failing.verify_wave(plan, wave_number=1)
    expected_err = "lifecycle hook 'pre_verify' failed with exit code 7"
    assert any(expected_err in v for v in violations_failing)


def test_prepare_wave_relative_worktrees_guard_and_claude_settings(tmp_path: Path) -> None:
    repo = tmp_path / "guard_repo"
    repo.mkdir()
    _init_git_repo(repo)

    # Simulate git config worktree.useRelativePaths=true
    subprocess.run(
        ["git", "config", "worktree.useRelativePaths", "true"],
        cwd=repo,
        check=True,
    )

    plan = WavePlan(
        sdd_kit="bmad",
        waves=[
            Wave(
                wave_number=1,
                tracks=[WaveTrack(track_id="track-a", task_ids=["1-1"], effort=EffortTier.LOW)],
            )
        ],
        edges=[],
    )

    wm = WorktreeManager(repo, fixed_slots=True)
    paths = wm.prepare_wave(plan, wave_number=1)
    wt_a = paths[0]

    # Verify extensions.relativeWorktrees is NOT present
    proc = subprocess.run(
        ["git", "config", "--get", "extensions.relativeWorktrees"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0 or proc.stdout.strip() != "true"

    # Verify Claude Code settings.local.json in worktree
    claude_settings = wt_a / ".claude" / "settings.local.json"
    assert claude_settings.is_file()
    data = json.loads(claude_settings.read_text(encoding="utf-8"))
    assert data["permissions"]["blockWritesOutsideWorkingDirectories"] is True

    # Invariant 1: Clean worktree (git status --porcelain is empty)
    status_proc = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=wt_a,
        capture_output=True,
        text=True,
        check=True,
    )
    assert status_proc.stdout.strip() == "", f"Worktree dirty: {status_proc.stdout}"


def test_prepare_wave_git_worktree_add_arguments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = tmp_path / "mock_repo"
    repo.mkdir()
    _init_git_repo(repo)

    plan = WavePlan(
        sdd_kit="bmad",
        waves=[
            Wave(
                wave_number=1,
                tracks=[WaveTrack(track_id="track-a", task_ids=["1-1"], effort=EffortTier.LOW)],
            )
        ],
        edges=[],
    )

    captured_cmds: list[list[str]] = []
    orig_run = subprocess.run

    def mock_run(cmd: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(cmd, list) and len(cmd) >= 4 and cmd[0] == "git" and "worktree" in cmd:
            captured_cmds.append(cmd)
        return orig_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", mock_run)

    wm = WorktreeManager(repo, fixed_slots=True)
    wm.prepare_wave(plan, wave_number=1)

    assert len(captured_cmds) == 1
    cmd = captured_cmds[0]
    assert cmd[0] == "git"
    assert cmd[1] == "-c"
    assert cmd[2] == "worktree.useRelativePaths=false"
    assert cmd[3] == "worktree"
    assert cmd[4] == "add"
    assert str(wm.worktree_path_for_track(1, "track-a").resolve()) in cmd


def test_merge_wave_atomic_rollback_on_second_branch_conflict(tmp_path: Path) -> None:
    """Verify merge_wave rolls back ALL merged branches if branch #2 hits a merge conflict."""
    repo = tmp_path / "conflict_repo"
    repo.mkdir()
    _init_git_repo(repo)
    (repo / "src").mkdir()
    (repo / "src" / "shared.py").write_text("x = 0\n", encoding="utf-8")
    (repo / "src" / "a.py").write_text("a = 0\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "seed src"], cwd=repo, check=True, capture_output=True)

    pre_merge_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    plan = WavePlan(
        sdd_kit="spec-kit",
        waves=[
            Wave(
                wave_number=1,
                tracks=[
                    WaveTrack(
                        track_id="track-a",
                        task_ids=["T001"],
                        effort=EffortTier.LOW,
                        exclusive_write_files=["src/shared.py", "src/a.py"],
                    ),
                    WaveTrack(
                        track_id="track-b",
                        task_ids=["T002"],
                        effort=EffortTier.LOW,
                        exclusive_write_files=["src/shared.py"],
                    ),
                ],
            )
        ],
        edges=[],
    )

    wm = WorktreeManager(repo, fixed_slots=True)
    wt_a, wt_b = wm.prepare_wave(plan, wave_number=1, headless=True)

    # Track A edits shared.py + a.py and commits cleanly
    (wt_a / "src" / "shared.py").write_text("x = 1\n", encoding="utf-8")
    (wt_a / "src" / "a.py").write_text("a = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/shared.py", "src/a.py"], cwd=wt_a, check=True)
    subprocess.run(["git", "commit", "-m", "track-a changes"], cwd=wt_a, check=True)
    wt_a_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=wt_a,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    # Track B makes a conflicting edit on shared.py
    (wt_b / "src" / "shared.py").write_text("x = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/shared.py"], cwd=wt_b, check=True)
    subprocess.run(["git", "commit", "-m", "track-b conflict"], cwd=wt_b, check=True)

    with pytest.raises(RuntimeError, match="transactional auto-rollback"):
        wm.merge_wave(
            plan,
            wave_number=1,
            run_quality_gates=False,
            cleanup_worktrees=True,
        )

    # Main repo HEAD and working files must be atomically rolled back to pre_merge_head
    head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert head_after == pre_merge_head
    assert (repo / "src" / "shared.py").read_text(encoding="utf-8") == "x = 0\n"
    assert (repo / "src" / "a.py").read_text(encoding="utf-8") == "a = 0\n"
    assert not (repo / ".git" / "MERGE_HEAD").exists()

    # Cleanup must NOT have fired before wave integrity succeeded (wt_a keeps its commit & lock)
    wt_a_head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=wt_a,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert wt_a_head_after == wt_a_head
    assert (wt_a / ".parallel-lock.json").is_file()


def test_merge_wave_atomic_rollback_on_state_update_failure(tmp_path: Path) -> None:
    """Verify exception in update_state_on_merge triggers full rollback instead of silent pass."""
    repo = tmp_path / "state_fail_repo"
    repo.mkdir()
    _init_git_repo(repo)
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("a = 0\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=repo, check=True, capture_output=True)

    pre_merge_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    plan = WavePlan(
        sdd_kit="spec-kit",
        waves=[
            Wave(
                wave_number=1,
                tracks=[
                    WaveTrack(
                        track_id="track-a",
                        task_ids=["T001"],
                        effort=EffortTier.LOW,
                        exclusive_write_files=["src/a.py"],
                    )
                ],
            )
        ],
        edges=[],
    )
    wm = WorktreeManager(repo, fixed_slots=True)
    (wt_a,) = wm.prepare_wave(plan, wave_number=1, headless=True)
    (wt_a / "src" / "a.py").write_text("a = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/a.py"], cwd=wt_a, check=True)
    subprocess.run(["git", "commit", "-m", "feat"], cwd=wt_a, check=True)

    class _FailingAdapter:
        kit_id = "spec-kit"

        def get_status_files(self) -> list[str]:
            return []

        def get_forbidden_files(self, wave: Wave, track: WaveTrack) -> dict[str, str]:
            return {}

        def get_allowed_write_globs(self, track: WaveTrack) -> list[str]:
            return []

        def get_track_env_vars(self, track: WaveTrack) -> dict[str, str]:
            return {}

        def prepare_worktree_overlay(self, worktree_path: Path, track: WaveTrack) -> list[str]:
            return []

        def render_worker_command(self, track: WaveTrack) -> str:
            return ""

        def update_state_on_merge(
            self, wave_number: int, merged_task_ids: list[str]
        ) -> Path | None:
            raise OSError("simulated disk error during state update")

    with pytest.raises(RuntimeError, match="Post-merge state update failed"):
        wm.merge_wave(
            plan,
            wave_number=1,
            run_quality_gates=False,
            cleanup_worktrees=True,
            adapter=_FailingAdapter(),  # type: ignore[arg-type]
        )

    head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert head_after == pre_merge_head
    assert (repo / "src" / "a.py").read_text(encoding="utf-8") == "a = 0\n"


def test_verify_wave_blocks_on_ast_diff_gate_default_drift(tmp_path: Path) -> None:
    """Verify verify_wave blocks when a track drifts a default arg read by a sibling."""
    repo = tmp_path / "drift_verify_repo"
    repo.mkdir()
    _init_git_repo(repo)
    (repo / "src").mkdir()
    (repo / "src" / "core.py").write_text(
        "def filter_items(min_steps: int = 1) -> list[str]:\n"
        "    return [str(min_steps)]\n",
        encoding="utf-8",
    )
    (repo / "src" / "consumer.py").write_text(
        "from src.core import filter_items\n\n"
        "def run() -> list[str]:\n"
        "    return filter_items()\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=repo, check=True, capture_output=True)

    from sdd_parallel.models import TaskNode

    plan = WavePlan(
        sdd_kit="spec-kit",
        tasks=[
            TaskNode(id="T001", title="Core", write_files=["src/core.py"]),
            TaskNode(
                id="T002",
                title="Consumer",
                write_files=["src/consumer.py"],
                impact_files=["src/core.py"],
            ),
        ],
        waves=[
            Wave(
                wave_number=1,
                tracks=[
                    WaveTrack(
                        track_id="track-a",
                        task_ids=["T001"],
                        effort=EffortTier.LOW,
                        exclusive_write_files=["src/core.py"],
                    ),
                    WaveTrack(
                        track_id="track-b",
                        task_ids=["T002"],
                        effort=EffortTier.LOW,
                        exclusive_write_files=["src/consumer.py"],
                    ),
                ],
            )
        ],
        edges=[],
    )

    wm = WorktreeManager(repo, fixed_slots=True)
    wt_a, wt_b = wm.prepare_wave(plan, wave_number=1, headless=True)

    (wt_a / "src" / "core.py").write_text(
        "def filter_items(min_steps: int = 2) -> list[str]:\n"
        "    return [str(min_steps)]\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "src/core.py"], cwd=wt_a, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "drift default"], cwd=wt_a, check=True, capture_output=True
    )

    (wt_b / "src" / "consumer.py").write_text(
        "from src.core import filter_items\n\n"
        "def run() -> list[str]:\n"
        "    return [x.strip() for x in filter_items()]\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "src/consumer.py"], cwd=wt_b, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "update consumer"], cwd=wt_b, check=True, capture_output=True
    )

    violations = wm.verify_wave(plan, wave_number=1)
    assert any("[SIGNATURE_DEFAULT_DRIFT]" in v for v in violations)
    assert any("filter_items" in v for v in violations)


