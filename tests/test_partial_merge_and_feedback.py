"""Tests for Task 45: Partial Wave Commit, Culprit Bisection, DAG deferral, and BarrierFeedback."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from sdd_parallel.autopilot import AutopilotLauncher
from sdd_parallel.cli import main as cli_main
from sdd_parallel.models import EffortTier, TaskNode, Wave, WavePlan, WaveTrack
from sdd_parallel.runner import WaveRunner
from sdd_parallel.worktree import WorktreeManager, _restore_os_writable, load_barrier_feedback


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-b", "master"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test Runner"], cwd=root, check=True)
    (root / "README.md").write_text("# Test Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True)


def _git_head(cwd: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def test_partial_merge_commits_healthy_tracks_on_git_conflict(tmp_path: Path) -> None:
    """Healthy tracks commit to master while conflicting track gets backup, feedback & Wave 2+."""
    repo = tmp_path / "partial_conflict_repo"
    repo.mkdir()
    _init_git_repo(repo)

    (repo / "src").mkdir()
    (repo / "src" / "shared.py").write_text("x = 0\n", encoding="utf-8")
    (repo / "src" / "a.py").write_text("a = 0\n", encoding="utf-8")
    (repo / "src" / "b.py").write_text("b = 0\n", encoding="utf-8")

    # BMAD status file to verify selective update_state_on_merge + reset_tasks_status
    bmad_dir = repo / "_bmad-output" / "implementation-artifacts"
    bmad_dir.mkdir(parents=True)
    status_file = bmad_dir / "sprint-status.yaml"
    status_file.write_text(
        'last_updated: "2026-11-23"\n'
        "development_status:\n"
        "  11-1-alpha: ready-for-dev\n"
        "  11-2-beta: ready-for-dev\n"
        "  11-3-gamma: in-progress\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=repo, check=True, capture_output=True)

    plan = WavePlan(
        sdd_kit="bmad",
        tasks=[
            TaskNode(id="11-1", title="Alpha", write_files=["src/shared.py", "src/a.py"]),
            TaskNode(id="11-2", title="Beta", write_files=["src/b.py"]),
            TaskNode(id="11-3", title="Gamma", write_files=["src/c.py"]),
        ],
        waves=[
            Wave(
                wave_number=1,
                tracks=[
                    WaveTrack(
                        track_id="track-a",
                        task_ids=["11-1"],
                        effort=EffortTier.LOW,
                        exclusive_write_files=["src/shared.py", "src/a.py"],
                    ),
                    WaveTrack(
                        track_id="track-b",
                        task_ids=["11-2"],
                        effort=EffortTier.LOW,
                        exclusive_write_files=["src/b.py"],
                    ),
                    WaveTrack(
                        track_id="track-c",
                        task_ids=["11-3"],
                        effort=EffortTier.LOW,
                        exclusive_write_files=["src/c.py"],
                    ),
                ],
            )
        ],
        edges=[],
    )

    wm = WorktreeManager(repo, fixed_slots=True)
    wt_a, wt_b, wt_c = wm.prepare_wave(plan, wave_number=1, headless=True)

    (wt_a / "src" / "shared.py").write_text("x = 1\n", encoding="utf-8")
    (wt_a / "src" / "a.py").write_text("a = 1\n", encoding="utf-8")
    wt_a_status = wt_a / "_bmad-output" / "implementation-artifacts" / "sprint-status.yaml"
    wt_a_status.write_text(
        wt_a_status.read_text(encoding="utf-8").replace(
            "11-1-alpha: ready-for-dev", "11-1-alpha: done"
        ),
        encoding="utf-8",
    )
    subprocess.run(
        [
            "git",
            "add",
            "src/shared.py",
            "src/a.py",
            "_bmad-output/implementation-artifacts/sprint-status.yaml",
        ],
        cwd=wt_a,
        check=True,
    )
    subprocess.run(["git", "commit", "-m", "track-a work"], cwd=wt_a, check=True)

    (wt_b / "src" / "b.py").write_text("b = 1\n", encoding="utf-8")
    wt_b_status = wt_b / "_bmad-output" / "implementation-artifacts" / "sprint-status.yaml"
    wt_b_status.write_text(
        wt_b_status.read_text(encoding="utf-8").replace(
            "11-2-beta: ready-for-dev", "11-2-beta: done"
        ),
        encoding="utf-8",
    )
    subprocess.run(
        [
            "git",
            "add",
            "src/b.py",
            "_bmad-output/implementation-artifacts/sprint-status.yaml",
        ],
        cwd=wt_b,
        check=True,
    )
    subprocess.run(["git", "commit", "-m", "track-b work"], cwd=wt_b, check=True)

    _restore_os_writable(wt_c, ["src/shared.py"])
    (wt_c / "src" / "shared.py").write_text("x = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/shared.py"], cwd=wt_c, check=True)
    subprocess.run(["git", "commit", "-m", "track-c conflict"], cwd=wt_c, check=True)

    merged = wm.merge_wave(
        plan,
        wave_number=1,
        run_quality_gates=False,
        cleanup_worktrees=True,
        partial_merge=True,
    )

    assert merged == ["parallel/track-a", "parallel/track-b"]
    assert (repo / "src" / "a.py").read_text(encoding="utf-8") == "a = 1\n"
    assert (repo / "src" / "b.py").read_text(encoding="utf-8") == "b = 1\n"
    assert (repo / "src" / "shared.py").read_text(encoding="utf-8") == "x = 1\n"
    assert not (repo / ".git" / "MERGE_HEAD").exists()

    outcome = wm.last_merge_outcome
    assert outcome is not None
    assert "track-c" in outcome.rejected_tracks
    fb = outcome.rejected_tracks["track-c"]
    assert fb.failed_stage == "git_merge"
    assert "src/shared.py" in fb.conflicting_files
    assert fb.prior_branch_ref.startswith("backup/wave-1-track-c-failed-")

    # Backup branch exists in git
    branch_list = subprocess.run(
        ["git", "branch", "--list", "backup/wave-1-track-c-failed-*"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "backup/wave-1-track-c-failed-" in branch_list

    # Feedback persisted for rejected story, cleared for merged stories
    loaded = load_barrier_feedback(repo, ["11-3"])
    assert len(loaded) == 1
    assert loaded[0].failed_stage == "git_merge"
    assert not (repo / ".parallel" / "barrier-feedback-11-1.json").exists()
    assert not (repo / ".parallel" / "barrier-feedback-11-2.json").exists()

    # Status updated for healthy stories and reset to ready-for-dev for rejected story
    status_text = status_file.read_text(encoding="utf-8")
    assert "11-1-alpha: done" in status_text
    assert "11-2-beta: done" in status_text
    assert "11-3-gamma: ready-for-dev" in status_text

    # State history records only healthy task_ids
    state_data = json.loads((repo / ".parallel" / "state.json").read_text(encoding="utf-8"))
    assert state_data["history"][-1]["task_ids"] == ["11-1", "11-2"]

    # Rescheduled plan persisted to .parallel/wave-plan.json with 11-3 moved to Wave 2+
    plan_file = repo / ".parallel" / "wave-plan.json"
    assert plan_file.is_file()
    rescheduled = WavePlan.from_dict(json.loads(plan_file.read_text(encoding="utf-8")))
    w2_plus_ids = [tid for w in rescheduled.waves if w.wave_number >= 2 for tid in w.all_task_ids]
    assert "11-3" in w2_plus_ids


def test_partial_merge_bisects_culprit_on_checkpoint_gate_failure(tmp_path: Path) -> None:
    """Type-2 collision breaking pytest bisects culprit track-b, preserves track-a, injects NACK."""
    repo = tmp_path / "bisection_repo"
    repo.mkdir()
    _init_git_repo(repo)

    (repo / "src").mkdir()
    (repo / "src" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "src" / "api.py").write_text(
        'def greet(name: str) -> str:\n    return f"hi {name}"\n',
        encoding="utf-8",
    )
    (repo / "tests").mkdir()
    (repo / "tests" / "test_api.py").write_text(
        "from src.api import greet\n\n"
        "def test_greet() -> None:\n"
        '    assert greet("world") == "hi world"\n',
        encoding="utf-8",
    )
    (repo / "specs" / "001-feat").mkdir(parents=True)
    (repo / "specs" / "001-feat" / "tasks.md").write_text(
        "- [ ] T001 Update greet signature\n- [ ] T002 Add consumer\n",
        encoding="utf-8",
    )
    py_exe = Path(sys.executable).as_posix()
    (repo / ".parallel-config.json").write_text(
        json.dumps({"checkpoint_gates": [f"{py_exe} -m pytest -q"]}),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=repo, check=True, capture_output=True)

    plan = WavePlan(
        sdd_kit="spec-kit",
        tasks=[
            TaskNode(
                id="T001",
                title="Update greet",
                write_files=["src/api.py", "tests/test_api.py"],
            ),
            TaskNode(
                id="T002",
                title="Add consumer",
                write_files=["src/consumer.py", "tests/test_consumer.py"],
            ),
        ],
        waves=[
            Wave(
                wave_number=1,
                checkpoint_gates=[f"{py_exe} -m pytest -q"],
                tracks=[
                    WaveTrack(
                        track_id="track-a",
                        task_ids=["T001"],
                        effort=EffortTier.LOW,
                        exclusive_write_files=["src/api.py", "tests/test_api.py"],
                    ),
                    WaveTrack(
                        track_id="track-b",
                        task_ids=["T002"],
                        effort=EffortTier.LOW,
                        exclusive_write_files=["src/consumer.py", "tests/test_consumer.py"],
                    ),
                ],
            )
        ],
        edges=[],
    )

    wm = WorktreeManager(repo, fixed_slots=True)
    wt_a, wt_b = wm.prepare_wave(plan, wave_number=1, headless=True)

    # Track A changes greet() return text & updates its own test -> clean individually
    (wt_a / "src" / "api.py").write_text(
        'def greet(name: str) -> str:\n    return f"hello {name}"\n',
        encoding="utf-8",
    )
    (wt_a / "tests" / "test_api.py").write_text(
        "from src.api import greet\n\n"
        "def test_greet() -> None:\n"
        '    assert greet("world") == "hello world"\n',
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "src/api.py", "tests/test_api.py"], cwd=wt_a, check=True)
    subprocess.run(["git", "commit", "-m", "track-a api update"], cwd=wt_a, check=True)

    # Track B adds consumer expecting old "hi ..." contract -> passes alone, breaks with Track A
    (wt_b / "src" / "consumer.py").write_text(
        "from src.api import greet\n\ndef welcome(u: str) -> str:\n    return greet(u)\n",
        encoding="utf-8",
    )
    (wt_b / "tests" / "test_consumer.py").write_text(
        "from src.consumer import welcome\n\n"
        "def test_welcome() -> None:\n"
        '    assert welcome("bob") == "hi bob"\n',
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "add", "src/consumer.py", "tests/test_consumer.py"], cwd=wt_b, check=True
    )
    subprocess.run(["git", "commit", "-m", "track-b consumer"], cwd=wt_b, check=True)

    merged = wm.merge_wave(
        plan,
        wave_number=1,
        run_quality_gates=True,
        cleanup_worktrees=True,
        partial_merge=True,
    )

    assert merged == ["parallel/track-a"]
    assert "hello" in (repo / "src" / "api.py").read_text(encoding="utf-8")
    assert not (repo / "src" / "consumer.py").exists()

    outcome = wm.last_merge_outcome
    assert outcome is not None
    assert list(outcome.rejected_tracks.keys()) == ["track-b"]
    fb = outcome.rejected_tracks["track-b"]
    assert fb.failed_stage == "checkpoint_gate"
    assert "pytest" in fb.failed_command
    assert "test_welcome" in fb.diagnostic_output or "FAILED" in fb.diagnostic_output
    assert len(fb.merged_sibling_commits) == 1
    assert fb.prior_branch_ref.startswith("backup/wave-1-track-b-failed-")

    # Prepare Wave 2 and verify NACK injection in .tmp/track-context.md & Autopilot prompt
    w2_paths = wm.prepare_wave(plan, wave_number=2, headless=True)
    assert len(w2_paths) == 1
    wt_w2 = w2_paths[0]
    ctx_md = (wt_w2 / ".tmp" / "track-context.md").read_text(encoding="utf-8")
    assert "PREVIOUS WAVE BARRIER NACK (ADAPT TO UPDATED MASTER)" in ctx_md
    assert fb.prior_branch_ref in ctx_md
    assert fb.merged_sibling_commits[0] in ctx_md
    assert (wt_w2 / ".tmp" / "barrier-feedback.json").is_file()

    w2_track = next(w.tracks[0] for w in plan.waves if w.wave_number == 2)
    launcher = AutopilotLauncher(repo)
    prompt = launcher._build_track_prompt(2, w2_track, wt_w2, sdd_kit="spec-kit")
    assert "PREVIOUS ATTEMPT FAILED BARRIER GATE" in prompt


def test_partial_merge_raises_when_all_tracks_fail(tmp_path: Path) -> None:
    """If every track fails checkpoint gates under partial_merge=True, master rolls back."""
    repo = tmp_path / "all_fail_repo"
    repo.mkdir()
    _init_git_repo(repo)
    (repo / "src").mkdir()
    (repo / "src" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_ok.py").write_text("def test_ok() -> None:\n    assert True\n")
    (repo / "specs" / "001-feat").mkdir(parents=True)
    (repo / "specs" / "001-feat" / "tasks.md").write_text(
        "- [ ] T001 One\n- [ ] T002 Two\n", encoding="utf-8"
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=repo, check=True, capture_output=True)
    pre_head = _git_head(repo)

    py_exe = Path(sys.executable).as_posix()
    plan = WavePlan(
        sdd_kit="spec-kit",
        tasks=[
            TaskNode(id="T001", title="One", write_files=["tests/test_a.py"]),
            TaskNode(id="T002", title="Two", write_files=["tests/test_b.py"]),
        ],
        waves=[
            Wave(
                wave_number=1,
                checkpoint_gates=[f"{py_exe} -m pytest -q"],
                tracks=[
                    WaveTrack(
                        track_id="track-a",
                        task_ids=["T001"],
                        effort=EffortTier.LOW,
                        exclusive_write_files=["tests/test_a.py"],
                    ),
                    WaveTrack(
                        track_id="track-b",
                        task_ids=["T002"],
                        effort=EffortTier.LOW,
                        exclusive_write_files=["tests/test_b.py"],
                    ),
                ],
            )
        ],
        edges=[],
    )

    wm = WorktreeManager(repo, fixed_slots=True)
    wt_a, wt_b = wm.prepare_wave(plan, wave_number=1, headless=True)

    (wt_a / "tests" / "test_a.py").write_text("def test_fail_a() -> None:\n    assert False\n")
    subprocess.run(["git", "add", "tests/test_a.py"], cwd=wt_a, check=True)
    subprocess.run(["git", "commit", "-m", "bad a"], cwd=wt_a, check=True)

    (wt_b / "tests" / "test_b.py").write_text("def test_fail_b() -> None:\n    assert False\n")
    subprocess.run(["git", "add", "tests/test_b.py"], cwd=wt_b, check=True)
    subprocess.run(["git", "commit", "-m", "bad b"], cwd=wt_b, check=True)

    with pytest.raises(RuntimeError, match="All tracks in Wave 1 failed"):
        wm.merge_wave(plan, wave_number=1, run_quality_gates=True, partial_merge=True)

    assert _git_head(repo) == pre_head
    assert len(load_barrier_feedback(repo, ["T001", "T002"])) == 2


def test_defer_escalated_task_uses_dag_rescheduler(tmp_path: Path) -> None:
    """Escalating a Wave 1 task to a file owned by Wave 2 pushes it to Wave 3 via DAG."""
    repo = tmp_path / "dag_defer_repo"
    repo.mkdir()
    _init_git_repo(repo)
    (repo / "src").mkdir()
    for fname in ("base.py", "a.py", "shared.py"):
        (repo / "src" / fname).write_text(f"# {fname}\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=repo, check=True, capture_output=True)

    plan = WavePlan(
        sdd_kit="spec-kit",
        tasks=[
            TaskNode(id="T0", title="Base", write_files=["src/base.py"]),
            TaskNode(id="T1", title="First", write_files=["src/a.py"]),
            TaskNode(id="T2", title="Second", write_files=["src/shared.py"]),
        ],
        waves=[
            Wave(
                wave_number=1,
                tracks=[
                    WaveTrack(
                        track_id="track-a",
                        task_ids=["T0"],
                        effort=EffortTier.LOW,
                        exclusive_write_files=["src/base.py"],
                    ),
                    WaveTrack(
                        track_id="track-b",
                        task_ids=["T1"],
                        effort=EffortTier.LOW,
                        exclusive_write_files=["src/a.py"],
                    ),
                ],
            ),
            Wave(
                wave_number=2,
                tracks=[
                    WaveTrack(
                        track_id="track-a",
                        task_ids=["T2"],
                        effort=EffortTier.LOW,
                        exclusive_write_files=["src/shared.py"],
                    )
                ],
            ),
        ],
        edges=[],
    )

    wm = WorktreeManager(repo, fixed_slots=True)
    updated = wm.defer_escalated_task(
        plan=plan,
        story_id="T1",
        requested_file="src/shared.py",
        current_wave=1,
    )

    w1 = next(w for w in updated.waves if w.wave_number == 1)
    w2 = next(w for w in updated.waves if w.wave_number == 2)
    w3 = next(w for w in updated.waves if w.wave_number == 3)
    assert w1.all_task_ids == ["T0"]
    assert w2.all_task_ids == ["T2"]
    assert w3.all_task_ids == ["T1"]
    assert "src/shared.py" in w3.tracks[0].exclusive_write_files


def test_strict_backward_compatibility_default_partial_merge_false(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Default partial_merge=False rolls back all tracks while still saving BarrierFeedback."""
    repo = tmp_path / "strict_compat_repo"
    repo.mkdir()
    _init_git_repo(repo)
    (repo / "src").mkdir()
    (repo / "src" / "core.py").write_text(
        "def fn(limit: int = 1) -> int:\n    return limit\n", encoding="utf-8"
    )
    (repo / "src" / "consumer.py").write_text(
        "from src.core import fn\n\ndef call_fn() -> int:\n    return fn()\n",
        encoding="utf-8",
    )
    (repo / "specs" / "001-feat").mkdir(parents=True)
    (repo / "specs" / "001-feat" / "tasks.md").write_text(
        "- [ ] T001 Core\n- [ ] T002 Consumer\n", encoding="utf-8"
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=repo, check=True, capture_output=True)
    pre_head = _git_head(repo)

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

    # 1. verify_wave saves BarrierFeedback on ASTGateFinding
    (wt_a / "src" / "core.py").write_text(
        "def fn(limit: int = 99) -> int:\n    return limit\n", encoding="utf-8"
    )
    subprocess.run(["git", "add", "src/core.py"], cwd=wt_a, check=True)
    subprocess.run(["git", "commit", "-m", "default drift"], cwd=wt_a, check=True)

    (wt_b / "src" / "consumer.py").write_text(
        "from src.core import fn\n\ndef call_fn() -> int:\n    return fn() + 1\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "src/consumer.py"], cwd=wt_b, check=True)
    subprocess.run(["git", "commit", "-m", "consumer"], cwd=wt_b, check=True)

    violations = wm.verify_wave(plan, wave_number=1)
    assert any("[SIGNATURE_DEFAULT_DRIFT]" in v for v in violations)
    ast_fb = load_barrier_feedback(repo, ["T002"])
    assert len(ast_fb) == 1
    assert ast_fb[0].failed_stage == "ast_diff_gate"

    # 2. Conflicting track-b under default partial_merge=False rolls back all tracks & saves NACK
    _restore_os_writable(wt_b, ["src/core.py"])
    (wt_b / "src" / "core.py").write_text(
        "def fn(limit: int = 5) -> int:\n    return limit\n", encoding="utf-8"
    )
    subprocess.run(["git", "add", "src/core.py"], cwd=wt_b, check=True)
    subprocess.run(["git", "commit", "-m", "conflict core"], cwd=wt_b, check=True)

    with pytest.raises(RuntimeError, match="transactional auto-rollback"):
        wm.merge_wave(plan, wave_number=1, run_quality_gates=False)

    assert _git_head(repo) == pre_head
    merge_fb = load_barrier_feedback(repo, ["T002"])
    assert len(merge_fb) == 1
    assert merge_fb[0].failed_stage == "git_merge"

    # 3. Verify CLI merge --partial-merge exposes structured partial merge keys
    (repo / ".parallel" / "wave-plan.json").write_text(
        json.dumps(plan.to_dict(), indent=2), encoding="utf-8"
    )
    capsys.readouterr()
    rc = cli_main(
        [
            "merge",
            "--repo",
            str(repo),
            "--wave",
            "1",
            "--skip-gates",
            "--partial-merge",
        ]
    )
    assert rc == 0
    out_json = json.loads(capsys.readouterr().out)
    assert out_json["partial_merge"] is True
    assert out_json["merged_branches"] == ["parallel/track-a"]
    assert len(out_json["rejected_tracks"]) == 1
    assert "track-b" in out_json["backup_branches"]
    assert out_json["rescheduled_plan"].endswith(".parallel/wave-plan.json")


def test_wave_runner_auto_merge_and_config_partial_merge(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify WaveRunner.run_wave(auto_merge=True) and .parallel-config.json partial_merge."""
    repo = tmp_path / "runner_partial_repo"
    repo.mkdir()
    _init_git_repo(repo)
    (repo / "src").mkdir()
    (repo / "src" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "src" / "a.py").write_text("VAL = 1\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_a.py").write_text(
        "from src.a import VAL\n\ndef test_a() -> None:\n    assert VAL == 1\n",
        encoding="utf-8",
    )
    (repo / "specs" / "001-feat").mkdir(parents=True)
    (repo / "specs" / "001-feat" / "tasks.md").write_text(
        "- [ ] T001 Alpha\n- [ ] T002 Beta\n", encoding="utf-8"
    )
    py_exe = Path(sys.executable).as_posix()
    (repo / ".parallel-config.json").write_text(
        json.dumps({"partial_merge": True, "checkpoint_gates": [f"{py_exe} -m pytest -q"]}),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=repo, check=True, capture_output=True)

    plan = WavePlan(
        sdd_kit="spec-kit",
        tasks=[
            TaskNode(id="T001", title="Alpha", write_files=["src/b.py"]),
            TaskNode(id="T002", title="Beta", write_files=["tests/test_bad.py"]),
        ],
        waves=[
            Wave(
                wave_number=1,
                checkpoint_gates=[f"{py_exe} -m pytest -q"],
                tracks=[
                    WaveTrack(
                        track_id="track-a",
                        task_ids=["T001"],
                        effort=EffortTier.LOW,
                        exclusive_write_files=["src/b.py"],
                    ),
                    WaveTrack(
                        track_id="track-b",
                        task_ids=["T002"],
                        effort=EffortTier.LOW,
                        exclusive_write_files=["tests/test_bad.py"],
                    ),
                ],
            )
        ],
        edges=[],
    )

    wm = WorktreeManager(repo, fixed_slots=True)
    wm.prepare_wave(plan, wave_number=1, headless=True)

    def mock_invoker(
        prompt: str,
        model: str | None,
        effort: EffortTier | None,
        wt: Path,
    ) -> subprocess.CompletedProcess[str]:
        if wt.name == "track-a":
            (wt / "src" / "b.py").write_text("B_VAL = 2\n", encoding="utf-8")
            subprocess.run(["git", "add", "src/b.py"], cwd=wt, check=True)
            subprocess.run(["git", "commit", "-m", "add b"], cwd=wt, check=True)
        else:
            (wt / "tests" / "test_bad.py").write_text(
                "def test_bad() -> None:\n    assert False\n", encoding="utf-8"
            )
            subprocess.run(["git", "add", "tests/test_bad.py"], cwd=wt, check=True)
            subprocess.run(["git", "commit", "-m", "add bad test"], cwd=wt, check=True)
        return subprocess.CompletedProcess(
            args=["mock"], returncode=0, stdout='{"session_id": "sess-1"}', stderr=""
        )

    runner = WaveRunner(repo, invoker=mock_invoker)
    summary = runner.run_wave(plan, wave_number=1, auto_merge=True)
    assert summary.all_succeeded is True
    assert summary.auto_merged is True
    assert (repo / "src" / "b.py").is_file()
    assert not (repo / "tests" / "test_bad.py").exists()
    assert len(load_barrier_feedback(repo, ["T002"])) == 1

    # Verify CLI picks up partial_merge: true from .parallel-config.json without flag
    repo2 = tmp_path / "cli_cfg_repo"
    repo2.mkdir()
    _init_git_repo(repo2)
    (repo2 / "src").mkdir()
    (repo2 / "src" / "x.py").write_text("X = 0\n", encoding="utf-8")
    (repo2 / "specs" / "001-feat").mkdir(parents=True)
    (repo2 / "specs" / "001-feat" / "tasks.md").write_text(
        "- [ ] T001 One\n- [ ] T002 Two\n", encoding="utf-8"
    )
    (repo2 / ".parallel-config.json").write_text(
        json.dumps({"partial_merge": True}), encoding="utf-8"
    )
    subprocess.run(["git", "add", "."], cwd=repo2, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=repo2, check=True, capture_output=True)

    plan2 = WavePlan(
        sdd_kit="spec-kit",
        tasks=[
            TaskNode(id="T001", title="One", write_files=["src/x.py"]),
            TaskNode(id="T002", title="Two", write_files=["src/y.py"]),
        ],
        waves=[
            Wave(
                wave_number=1,
                tracks=[
                    WaveTrack(
                        track_id="track-a",
                        task_ids=["T001"],
                        effort=EffortTier.LOW,
                        exclusive_write_files=["src/x.py"],
                    ),
                    WaveTrack(
                        track_id="track-b",
                        task_ids=["T002"],
                        effort=EffortTier.LOW,
                        exclusive_write_files=["src/y.py"],
                    ),
                ],
            )
        ],
        edges=[],
    )
    wm2 = WorktreeManager(repo2, fixed_slots=True)
    w2a, w2b = wm2.prepare_wave(plan2, wave_number=1, headless=True)
    (w2a / "src" / "x.py").write_text("X = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/x.py"], cwd=w2a, check=True)
    subprocess.run(["git", "commit", "-m", "a"], cwd=w2a, check=True)

    _restore_os_writable(w2b, ["src/x.py"])
    (w2b / "src" / "x.py").write_text("X = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/x.py"], cwd=w2b, check=True)
    subprocess.run(["git", "commit", "-m", "b conflict"], cwd=w2b, check=True)

    (repo2 / ".parallel").mkdir(parents=True, exist_ok=True)
    (repo2 / ".parallel" / "wave-plan.json").write_text(
        json.dumps(plan2.to_dict(), indent=2), encoding="utf-8"
    )
    capsys.readouterr()
    rc = cli_main(["merge", "--repo", str(repo2), "--wave", "1", "--skip-gates"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["partial_merge"] is True
    assert payload["merged_branches"] == ["parallel/track-a"]
    assert len(payload["rejected_tracks"]) == 1

