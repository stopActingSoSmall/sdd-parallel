"""Unit tests for Depth-0 WaveRunner, TrackExecutionPolicy protocol, and convergence FSM."""

from __future__ import annotations

import io
import json
import subprocess
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

import pytest

from sdd_parallel.cli import main
from sdd_parallel.models import (
    ConflictEdge,
    ConflictType,
    EffortTier,
    TaskNode,
    Wave,
    WavePlan,
    WaveTrack,
)
from sdd_parallel.runner import (
    BoundedCyclicReviewPolicy,
    IterationDirective,
    ReviewVerdict,
    SingleShotExecutionPolicy,
    TrackExecutionContext,
    WaveRunner,
)


def test_review_verdict_properties() -> None:
    # 1. Qualified when zero critical and high
    v_clean = ReviewVerdict(iteration=1, critical_count=0, high_count=0, medium_count=3)
    assert v_clean.effective_blockers == 0
    assert v_clean.is_qualified is True

    # 2. Blocked by critical
    v_crit = ReviewVerdict(iteration=1, critical_count=1, high_count=0)
    assert v_crit.effective_blockers == 1
    assert v_crit.is_qualified is False

    # 3. Blocked by high
    v_high = ReviewVerdict(iteration=1, critical_count=0, high_count=2)
    assert v_high.effective_blockers == 2
    assert v_high.is_qualified is False

    # 4. Local blockers override high_count
    v_local = ReviewVerdict(
        iteration=1,
        critical_count=1,
        high_count=5,
        local_blockers=0,
    )
    assert v_local.effective_blockers == 1  # 1 critical + 0 local
    assert v_local.is_qualified is False

    v_local_zero = ReviewVerdict(
        iteration=1,
        critical_count=0,
        high_count=5,
        local_blockers=0,
    )
    assert v_local_zero.effective_blockers == 0
    assert v_local_zero.is_qualified is True


def test_single_shot_policy_success(tmp_path: Path) -> None:
    wt_dir = tmp_path / "track-a"
    wt_dir.mkdir(parents=True)
    track = WaveTrack(track_id="track-a", task_ids=["T-1"], effort=EffortTier.MEDIUM)

    recorded_calls: list[dict[str, Any]] = []

    def mock_invoker(
        prompt: str,
        model: str | None,
        effort: EffortTier | None,
        cwd: Path,
    ) -> subprocess.CompletedProcess[str]:
        recorded_calls.append({"prompt": prompt, "model": model, "effort": effort, "cwd": cwd})
        stdout_json = json.dumps({"session_id": "sess-xyz-123", "status": "completed"})
        return subprocess.CompletedProcess(
            args=["mock"], returncode=0, stdout=stdout_json, stderr=""
        )

    ctx = TrackExecutionContext(
        repo_root=tmp_path,
        worktree_path=wt_dir,
        wave_number=1,
        track=track,
        harness="antigravity",
        invoker=mock_invoker,
    )

    policy = SingleShotExecutionPolicy()
    result = policy.execute(ctx)

    assert result.status == "SUCCEEDED"
    assert result.exit_code == 0
    assert result.session_id == "sess-xyz-123"
    assert result.iterations_used == 1
    assert len(recorded_calls) == 1

    session_file = wt_dir / ".tmp" / "runner-session.json"
    assert session_file.is_file()
    session_data = json.loads(session_file.read_text(encoding="utf-8"))
    assert session_data["session_id"] == "sess-xyz-123"
    assert session_data["exit_code"] == 0
    assert session_data["policy"] == "single-shot"


def test_single_shot_policy_failure_and_timeout(tmp_path: Path) -> None:
    wt_dir = tmp_path / "track-b"
    wt_dir.mkdir(parents=True)
    track = WaveTrack(track_id="track-b", task_ids=["T-2"], effort=EffortTier.HIGH)

    # 1. Non-zero exit code
    def mock_invoker_fail(
        prompt: str,
        model: str | None,
        effort: EffortTier | None,
        cwd: Path,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["mock"], returncode=1, stdout="", stderr="Error occurred"
        )

    ctx_fail = TrackExecutionContext(
        repo_root=tmp_path,
        worktree_path=wt_dir,
        wave_number=1,
        track=track,
        harness="claude-code",
        invoker=mock_invoker_fail,
    )
    res_fail = SingleShotExecutionPolicy().execute(ctx_fail)
    assert res_fail.status == "FAILED"
    assert res_fail.exit_code == 1

    # 2. TimeoutExpired
    def mock_invoker_timeout(
        prompt: str,
        model: str | None,
        effort: EffortTier | None,
        cwd: Path,
    ) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(
            cmd=["mock"], timeout=10.0, output="partial stdout", stderr="timeout"
        )

    ctx_timeout = TrackExecutionContext(
        repo_root=tmp_path,
        worktree_path=wt_dir,
        wave_number=1,
        track=track,
        harness="codex",
        timeout_seconds=10.0,
        invoker=mock_invoker_timeout,
    )
    res_timeout = SingleShotExecutionPolicy().execute(ctx_timeout)
    assert res_timeout.status == "TIMED_OUT"
    assert res_timeout.exit_code == 124
    assert "timed out after 10.0s" in (res_timeout.error_message or "")


def test_cyclic_review_policy_convergence_and_oscillation_handling(tmp_path: Path) -> None:
    wt_dir = tmp_path / "track-c"
    wt_dir.mkdir(parents=True)
    track = WaveTrack(track_id="track-c", task_ids=["T-3"], effort=EffortTier.LOW)

    prompts_received: list[str] = []

    def mock_invoker(
        prompt: str,
        model: str | None,
        effort: EffortTier | None,
        cwd: Path,
    ) -> subprocess.CompletedProcess[str]:
        prompts_received.append(prompt)
        return subprocess.CompletedProcess(
            args=["mock"],
            returncode=0,
            stdout=json.dumps({"conversation_id": "conv-cycle-1"}),
            stderr="",
        )

    # Reviewer returns WHACK_A_MOLE on iteration 1, then qualified on iteration 2
    def mock_reviewer(ctx: TrackExecutionContext, iteration: int) -> ReviewVerdict:
        if iteration == 1:
            return ReviewVerdict(
                iteration=1,
                critical_count=1,
                high_count=0,
                divergence_flag="WHACK_A_MOLE",
                summary="Regression introduced in core AST node",
            )
        return ReviewVerdict(
            iteration=2,
            critical_count=0,
            high_count=0,
            divergence_flag="CONVERGING",
            summary="All regressions fixed and verified",
        )

    policy = BoundedCyclicReviewPolicy(
        max_iterations=3,
        iteration_schedule={
            2: IterationDirective(effort=EffortTier.HIGH, model="pro"),
        },
    )

    ctx = TrackExecutionContext(
        repo_root=tmp_path,
        worktree_path=wt_dir,
        wave_number=1,
        track=track,
        harness="antigravity",
        invoker=mock_invoker,
        reviewer_fn=mock_reviewer,
    )

    result = policy.execute(ctx)

    assert result.status == "SUCCEEDED"
    assert result.iterations_used == 2
    assert len(result.verdicts) == 2
    assert len(prompts_received) == 2

    # Verify that iteration 2 was escalated to SUBTRACTIVE due to WHACK_A_MOLE oscillation
    assert "[EXECUTION_MODE: SUBTRACTIVE]" in prompts_received[1]
    assert "[PREVIOUS REVIEW FEEDBACK (Iteration 1)]" in prompts_received[1]


def test_cyclic_review_policy_zero_budget_preflight_nack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wt_dir = tmp_path / "track-d"
    wt_dir.mkdir(parents=True)
    track = WaveTrack(track_id="track-d", task_ids=["T-4"], effort=EffortTier.MEDIUM)

    executor_calls = 0
    preflight_calls = 0

    def mock_invoker(
        prompt: str,
        model: str | None,
        effort: EffortTier | None,
        cwd: Path,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal executor_calls
        executor_calls += 1
        return subprocess.CompletedProcess(
            args=["mock"],
            returncode=0,
            stdout=json.dumps({"session_id": "sess-nack-1"}),
            stderr="",
        )

    # Monkeypatch subprocess.run for preflight commands
    real_run = subprocess.run

    def mock_sub_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal preflight_calls
        if isinstance(cmd, list) and cmd[0] == "fake-pytest":
            preflight_calls += 1
            if preflight_calls == 1:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=1, stdout="", stderr="1 failed test"
                )
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="all passed", stderr=""
            )
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", mock_sub_run)

    reviewer_called = 0

    def mock_reviewer(ctx: TrackExecutionContext, iteration: int) -> ReviewVerdict:
        nonlocal reviewer_called
        reviewer_called += 1
        return ReviewVerdict(iteration=iteration, critical_count=0, high_count=0)

    policy = BoundedCyclicReviewPolicy(
        max_iterations=2,
        max_preflight_bounces=2,
        preflight_commands=(("fake-pytest",),),
    )

    ctx = TrackExecutionContext(
        repo_root=tmp_path,
        worktree_path=wt_dir,
        wave_number=1,
        track=track,
        harness="claude-code",
        invoker=mock_invoker,
        reviewer_fn=mock_reviewer,
    )

    result = policy.execute(ctx)

    assert result.status == "SUCCEEDED"
    assert result.iterations_used == 1
    # Executor was invoked twice due to 1 pre-flight bounce:
    assert executor_calls == 2
    # Pre-flight command was run twice:
    assert preflight_calls == 2
    # Reviewer was only called once, on iteration 1:
    assert reviewer_called == 1


def test_cyclic_review_policy_exhaustion_gate_and_triage(tmp_path: Path) -> None:
    wt_dir = tmp_path / "track-e"
    wt_dir.mkdir(parents=True)
    track = WaveTrack(track_id="track-e", task_ids=["T-5"], effort=EffortTier.MEDIUM)

    def mock_invoker(
        prompt: str,
        model: str | None,
        effort: EffortTier | None,
        cwd: Path,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["mock"],
            returncode=0,
            stdout=json.dumps({"session_id": "sess-exh"}),
            stderr="",
        )

    def mock_failing_reviewer(ctx: TrackExecutionContext, iteration: int) -> ReviewVerdict:
        return ReviewVerdict(
            iteration=iteration,
            critical_count=2,
            divergence_flag="ADDITIVE_BLOAT",
            summary="Deadlock in database transaction",
            split_candidates=("T-5a", "T-5b"),
        )

    # 1. on_exhaustion = "propose_split"
    policy_split = BoundedCyclicReviewPolicy(
        max_iterations=2,
        on_exhaustion="propose_split",
    )
    ctx_split = TrackExecutionContext(
        repo_root=tmp_path,
        worktree_path=wt_dir,
        wave_number=1,
        track=track,
        invoker=mock_invoker,
        reviewer_fn=mock_failing_reviewer,
    )
    res_split = policy_split.execute(ctx_split)

    assert res_split.status == "EXHAUSTED_NEEDS_SPLIT"
    assert res_split.iterations_used == 2

    triage_file = wt_dir / ".tmp" / "exhaustion-triage.json"
    assert triage_file.is_file()
    triage_data = json.loads(triage_file.read_text(encoding="utf-8"))
    assert triage_data["remaining_blockers"] == 2
    assert triage_data["divergence_flag"] == "ADDITIVE_BLOAT"
    assert triage_data["recommended_action"] == "propose_split"
    assert triage_data["split_candidates"] == ["T-5a", "T-5b"]

    # 2. on_exhaustion = "defer_wave"
    policy_defer = BoundedCyclicReviewPolicy(
        max_iterations=1,
        on_exhaustion="defer_wave",
    )
    res_defer = policy_defer.execute(ctx_split)
    assert res_defer.status == "ESCALATED_DEFERRED"


def test_wave_runner_preflight_binary_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Ensure binary is reported missing by shutil.which
    monkeypatch.setattr("shutil.which", lambda name: None)

    wave = Wave(
        wave_number=1,
        tracks=[WaveTrack(track_id="track-a", task_ids=["T-1"], effort=EffortTier.LOW)],
    )
    plan = WavePlan(
        sdd_kit="bmad",
        waves=[wave],
        edges=[],
        tasks=[TaskNode(id="T-1", title="Task 1")],
    )

    runner = WaveRunner(repo_root=tmp_path, harness="antigravity")
    summary = runner.run_wave(plan, wave_number=1)

    assert summary.all_succeeded is False
    assert summary.failed_tracks == 1
    assert "Harness CLI binary 'agy' not found in system PATH" in (summary.error_message or "")


def test_wave_runner_concurrent_execution_and_manifest(tmp_path: Path) -> None:
    # Prepare git repo
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True, check=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    (tmp_path / "README.md").write_text("# Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True, check=True)

    wt_base = tmp_path / ".worktrees"
    (wt_base / "track-a").mkdir(parents=True)
    (wt_base / "track-b").mkdir(parents=True)

    wave = Wave(
        wave_number=1,
        tracks=[
            WaveTrack(track_id="track-a", task_ids=["T-1"], effort=EffortTier.LOW),
            WaveTrack(track_id="track-b", task_ids=["T-2"], effort=EffortTier.HIGH),
        ],
    )
    plan = WavePlan(
        sdd_kit="bmad",
        waves=[wave],
        edges=[
            ConflictEdge(
                source="T-1",
                target="T-2",
                conflict_type=ConflictType.DIRECT_COLLISION,
                reason="Shared file",
                shared_files=("shared.py",),
            )
        ],
        tasks=[
            TaskNode(id="T-1", title="Task 1"),
            TaskNode(id="T-2", title="Task 2"),
        ],
    )

    def mock_invoker(
        prompt: str,
        model: str | None,
        effort: EffortTier | None,
        cwd: Path,
    ) -> subprocess.CompletedProcess[str]:
        track_name = cwd.name
        stdout_json = json.dumps({"session_id": f"sess-{track_name}"})
        return subprocess.CompletedProcess(
            args=["mock"], returncode=0, stdout=stdout_json, stderr=""
        )

    runner = WaveRunner(
        repo_root=tmp_path,
        harness="antigravity",
        invoker=mock_invoker,
        worktrees_dir=wt_base,
    )

    summary = runner.run_wave(plan, wave_number=1)

    assert summary.all_succeeded is True
    assert summary.total_tracks == 2
    assert summary.succeeded_tracks == 2
    assert summary.failed_tracks == 0

    # Verify manifest was persisted
    manifest_file = tmp_path / ".parallel" / "wave-1-manifest.json"
    assert manifest_file.is_file()
    man_data = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert man_data["wave_id"] == 1
    assert len(man_data["tracks"]) == 2
    assert {t["track_id"] for t in man_data["tracks"]} == {"track-a", "track-b"}
    assert {t["session_id"] for t in man_data["tracks"]} == {"sess-track-a", "sess-track-b"}

    # Verify run-manifest.json was updated
    run_man = tmp_path / ".parallel" / "run-manifest.json"
    assert run_man.is_file()
    run_data = json.loads(run_man.read_text(encoding="utf-8"))
    assert "1" in run_data["waves"]


def test_wave_runner_auto_merge_blocked_on_failure(tmp_path: Path) -> None:
    wt_base = tmp_path / ".worktrees"
    (wt_base / "track-a").mkdir(parents=True)
    (wt_base / "track-b").mkdir(parents=True)

    wave = Wave(
        wave_number=1,
        tracks=[
            WaveTrack(track_id="track-a", task_ids=["T-1"], effort=EffortTier.LOW),
            WaveTrack(track_id="track-b", task_ids=["T-2"], effort=EffortTier.HIGH),
        ],
    )
    plan = WavePlan(
        sdd_kit="bmad",
        waves=[wave],
        edges=[],
        tasks=[
            TaskNode(id="T-1", title="Task 1"),
            TaskNode(id="T-2", title="Task 2"),
        ],
    )

    def mock_invoker_with_failure(
        prompt: str,
        model: str | None,
        effort: EffortTier | None,
        cwd: Path,
    ) -> subprocess.CompletedProcess[str]:
        if cwd.name == "track-b":
            return subprocess.CompletedProcess(
                args=["mock"], returncode=1, stdout="", stderr="crash"
            )
        return subprocess.CompletedProcess(
            args=["mock"],
            returncode=0,
            stdout=json.dumps({"session_id": "sess-a"}),
            stderr="",
        )

    runner = WaveRunner(
        repo_root=tmp_path,
        harness="claude-code",
        invoker=mock_invoker_with_failure,
        worktrees_dir=wt_base,
    )

    summary = runner.run_wave(plan, wave_number=1, auto_merge=True)

    assert summary.all_succeeded is False
    assert summary.auto_merged is False
    assert summary.failed_tracks == 1
    assert "Auto-merge blocked: 1 tracks failed" in (summary.error_message or "")


def test_cli_autopilot_dry_run_and_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Setup minimal BMAD git repo
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test Runner"], cwd=tmp_path, check=True)

    impl_dir = tmp_path / "_bmad-output" / "implementation-artifacts"
    plan_dir = tmp_path / "_bmad-output" / "planning-artifacts"
    impl_dir.mkdir(parents=True)
    plan_dir.mkdir(parents=True)

    (impl_dir / "sprint-status.yaml").write_text(
        'last_updated: "10-21-2026 12:00"\ndevelopment_status:\n  11-1-first: ready-for-dev\n',
        encoding="utf-8",
    )
    (plan_dir / "epics.md").write_text(
        "## Epic 11: Auth\n### Story 11-1: First\nFiles: `src/main.py`\n",
        encoding="utf-8",
    )
    (impl_dir / "epic-11-context.md").write_text("# Epic 11 Context\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True)

    # 1. Dry run
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(["autopilot", "--repo", str(tmp_path), "--wave", "1", "--harness", "antigravity"])
    assert rc == 0
    specs = json.loads(buf.getvalue())
    assert len(specs) == 1
    assert specs[0]["argv"][0] == "agy"
    assert specs[0]["argv"][1] == "-p"

    # 2. Execute via WaveRunner
    monkeypatch.setattr("shutil.which", lambda name: "/mock/bin/" + name)
    monkeypatch.setattr(
        "sdd_parallel.runner._create_default_invoker",
        lambda harness, timeout, extra_env=None: (
            lambda prompt, model, effort, cwd: subprocess.CompletedProcess(
                args=["mock"],
                returncode=0,
                stdout=json.dumps({"session_id": "sess-cli-1"}),
                stderr="",
            )
        ),
    )

    buf_exec = io.StringIO()
    with redirect_stdout(buf_exec):
        rc_exec = main([
            "autopilot",
            "--repo", str(tmp_path),
            "--wave", "1",
            "--harness", "antigravity",
            "--execute",
            "--policy", "single-shot",
        ])
    assert rc_exec == 0
    summary = json.loads(buf_exec.getvalue())
    assert summary["all_succeeded"] is True
    assert summary["succeeded_tracks"] == 1
    assert "track-a" in summary["track_results"]

