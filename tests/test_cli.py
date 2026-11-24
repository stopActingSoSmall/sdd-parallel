"""Unit tests for sdd-parallel CLI commands (run-guide and Assisted flow)."""

from __future__ import annotations

import io
import json
import subprocess
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

from sdd_parallel.cli import main, render_run_guide
from sdd_parallel.models import EffortTier, Wave, WavePlan, WaveTrack


def test_render_run_guide_formats_markdown_table_and_commands(tmp_path: Path) -> None:
    """Verify render_run_guide produces structured Markdown matrix and instructions."""
    plan = WavePlan(
        sdd_kit="bmad",
        waves=[
            Wave(
                wave_number=1,
                tracks=[
                    WaveTrack(
                        track_id="track-a",
                        task_ids=["11-2"],
                        effort=EffortTier.HIGH,
                        exclusive_write_files=["src/demo/intervals.py"],
                    ),
                    WaveTrack(
                        track_id="track-b",
                        task_ids=["11-3"],
                        effort=EffortTier.MEDIUM,
                        exclusive_write_files=["src/demo/manifest.py"],
                    ),
                ],
            )
        ],
        edges=[],
    )

    guide_local = render_run_guide(tmp_path, plan, wave_number=1, antigravity_layout=False)
    assert "# 🧭 Two-Stage Assisted Operator Guide — Wave 1 (BMAD)" in guide_local
    assert "| `track-a` | `11-2` | `high` |" in guide_local
    assert "| `track-b` | `11-3` | `medium` |" in guide_local
    assert "`/bmad-build 11-2`" in guide_local
    assert "`/bmad-build 11-3`" in guide_local
    assert ".worktrees/wave-1-track-a" in guide_local

    guide_ag = render_run_guide(tmp_path, plan, wave_number=1, antigravity_layout=True)
    assert "worktrees" in guide_ag


def test_cli_run_guide_execution(tmp_path: Path) -> None:
    """Verify sdd-parallel run-guide CLI command exits 0 and prints guide."""
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test Runner"], cwd=tmp_path, check=True)

    impl_dir = tmp_path / "_bmad-output" / "implementation-artifacts"
    plan_dir = tmp_path / "_bmad-output" / "planning-artifacts"
    impl_dir.mkdir(parents=True)
    plan_dir.mkdir(parents=True)

    (impl_dir / "sprint-status.yaml").write_text(
        'last_updated: "10-21-2026 12:00"\ndevelopment_status:\n  1-1-first: ready-for-dev\n',
        encoding="utf-8",
    )
    (plan_dir / "epics.md").write_text(
        "## Epic 1\n### Story 1-1: First\nFiles: `src/main.py`\n",
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True)

    buf = io.StringIO()
    with redirect_stdout(buf):
        exit_code = main(["run-guide", "--repo", str(tmp_path), "--wave", "1"])

    assert exit_code == 0
    output = buf.getvalue()
    assert "Two-Stage Assisted Operator Guide" in output
    assert "track-a" in output
    assert "`/bmad-build 1-1`" in output


def test_skill_md_realigned_to_two_stage_assisted() -> None:
    """Verify SKILL.md marks Two-Stage Assisted as recommended and deprecates Flat Dispatch."""
    repo_root = Path(__file__).resolve().parent.parent
    skill_path = repo_root / ".agent" / "skills" / "sdd-parallel" / "SKILL.md"
    assert skill_path.is_file()
    content = skill_path.read_text(encoding="utf-8")

    assert "Mode A: Two-Stage Assisted" in content
    assert "Mode C: In-Session Flat Dispatch (Deprecated / Fallback Only)" in content
    assert "sdd-parallel rollback-wave" in content
    assert "sdd-parallel abort-wave" in content


def test_cli_clean_json_error_handling(tmp_path: Path) -> None:
    """Verify CLI outputs clean JSON error without raw Traceback on failure."""
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test Runner"], cwd=tmp_path, check=True)

    impl_dir = tmp_path / "_bmad-output" / "implementation-artifacts"
    plan_dir = tmp_path / "_bmad-output" / "planning-artifacts"
    impl_dir.mkdir(parents=True)
    plan_dir.mkdir(parents=True)
    (impl_dir / "sprint-status.yaml").write_text(
        'last_updated: "10-25-2026 12:00"\ndevelopment_status:\n  1-1: ready-for-dev\n',
        encoding="utf-8",
    )
    (plan_dir / "epics.md").write_text(
        "## Epic 1\n### Story 1-1\nFiles: `src/m.py`\n",
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True)

    err_buf = io.StringIO()
    with redirect_stderr(err_buf):
        exit_code = main(["merge", "--repo", str(tmp_path), "--wave", "99"])

    assert exit_code == 2
    err_str = err_buf.getvalue()
    assert "Traceback" not in err_str
    err_json = json.loads(err_str)
    assert err_json["status"] == "error"
    assert err_json["command"] == "merge"
    assert err_json["wave"] == 99
    assert "Wave 99 not found" in err_json["error"]


def test_cli_plan_default_artifacts_dir_polymorphic(tmp_path: Path) -> None:
    """Verify sdd-parallel plan saves artifacts in .parallel/artifacts for non-BMAD kits."""
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test Runner"], cwd=tmp_path, check=True)

    # Setup Spec Kit repo
    spec_dir = tmp_path / "specs" / "001-feat"
    spec_dir.mkdir(parents=True)
    (spec_dir / "tasks.md").write_text(
        "# Tasks\n- [ ] T001 Implement in `src/feature.py`\n",
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "feature.py").write_text("y = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True)

    exit_code = main(["plan", "--repo", str(tmp_path), "--kit", "spec-kit"])
    assert exit_code == 0

    parallel_artifacts = tmp_path / ".parallel" / "artifacts"
    assert parallel_artifacts.is_dir()
    assert (parallel_artifacts / "execution-plan-parallel.md").is_file()
    assert (parallel_artifacts / "wave-plan.json").is_file()

    # Invariant: non-BMAD kits must not produce _bmad-output
    assert not (tmp_path / "_bmad-output").exists()


def test_cli_bench_demo_execution() -> None:
    """Verify sdd-parallel bench --demo runs under 2s with zero false negatives."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        exit_code = main(["bench", "--demo"])

    assert exit_code == 0
    output = buf.getvalue()
    assert "SDD-PARALLEL HISTORICAL BENCHMARK & RETROSPECTIVE EVALUATION" in output
    assert "False Negatives (FN) : 0" in output
    assert "Recall    : 100.0%" in output


def test_cli_inspect_execution(tmp_path: Path) -> None:
    """Verify sdd-parallel inspect generates valid standalone HTML visualizer."""
    out_html = tmp_path / "wave_inspector.html"
    buf = io.StringIO()
    with redirect_stdout(buf):
        exit_code = main(["inspect", "--repo", str(tmp_path), "--output", str(out_html)])

    assert exit_code == 0
    assert out_html.is_file()
    assert "[SDD-Parallel] Wave Inspector generated at:" in buf.getvalue()
    content = out_html.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in content
    assert "SDD-Parallel Wave Inspector" in content


def test_cli_scope_escalation_e2e_verify_and_merge(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """E2E C2 test: hook writes escalation, verify-wave defers/resets, merge uses saved plan."""
    from sdd_parallel.hooks.blast_radius_guard import run_stdin_hook

    subprocess.run(["git", "init", "-b", "master"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test Runner"], cwd=tmp_path, check=True)

    spec_dir = tmp_path / "specs" / "001-feat"
    spec_dir.mkdir(parents=True)
    (spec_dir / "tasks.md").write_text(
        "# Tasks\n"
        "- [ ] T001 [P] Alpha work in `src/alpha.py`\n"
        "- [ ] T002 [P] Beta work in `src/beta.py`\n",
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "alpha.py").write_text("alpha = 0\n", encoding="utf-8")
    (tmp_path / "src" / "beta.py").write_text("beta = 0\n", encoding="utf-8")
    (tmp_path / "src" / "extra.py").write_text("extra = 0\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)

    master_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    assert main(["plan", "--repo", str(tmp_path), "--kit", "spec-kit"]) == 0
    assert (
        main(["prepare", "--repo", str(tmp_path), "--wave", "1", "--kit", "spec-kit", "--headless"])
        == 0
    )

    wt_a = tmp_path / ".worktrees" / "track-a"
    wt_b = tmp_path / ".worktrees" / "track-b"

    # Track A completes T001 cleanly
    (wt_a / "src" / "alpha.py").write_text("alpha = 1\n", encoding="utf-8")
    tasks_a = wt_a / "specs" / "001-feat" / "tasks.md"
    tasks_a.write_text(
        tasks_a.read_text(encoding="utf-8").replace("[ ] T001", "[x] T001"),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "src/alpha.py", "specs/001-feat/tasks.md"], cwd=wt_a, check=True)
    subprocess.run(["git", "commit", "-m", "feat(T001)"], cwd=wt_a, check=True)

    # Track B writes partial work, then hits PreToolUse hook trying to write src/extra.py
    (wt_b / "src" / "beta.py").write_text("beta = 999\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/beta.py"], cwd=wt_b, check=True)
    subprocess.run(["git", "commit", "-m", "wip(T002)"], cwd=wt_b, check=True)

    payload = {
        "cwd": str(wt_b),
        "toolCall": {
            "name": "write_file",
            "args": {"TargetFile": str(wt_b / "src" / "extra.py")},
        },
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    out_hook = io.StringIO()
    with redirect_stdout(out_hook):
        assert run_stdin_hook() == 0
    assert json.loads(out_hook.getvalue())["decision"] == "deny"
    assert (wt_b / ".tmp" / "scope-escalation.json").is_file()

    # verify-wave defers T002 to Wave 2, resets track-b slot, writes .parallel/wave-plan.json
    verify_out = io.StringIO()
    with redirect_stdout(verify_out):
        rc_verify = main(
            ["verify-wave", "--repo", str(tmp_path), "--wave", "1", "--kit", "spec-kit"]
        )
    assert rc_verify == 0
    v_payload = json.loads(verify_out.getvalue())
    assert v_payload["compliant"] is True
    assert len(v_payload["escalations"]) == 1
    assert v_payload["escalations"][0]["story_id"] == "T002"

    # Deferred slot was reset to master_head and escalation file cleared
    wt_b_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=wt_b,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert wt_b_head == master_head
    assert not (wt_b / ".tmp" / "scope-escalation.json").exists()

    saved_plan = json.loads((tmp_path / ".parallel" / "wave-plan.json").read_text(encoding="utf-8"))
    assert len(saved_plan["waves"]) == 2
    assert saved_plan["waves"][1]["tracks"][0]["task_ids"] == ["T002"]
    assert "src/extra.py" in saved_plan["waves"][1]["tracks"][0]["exclusive_write_files"]

    # merge uses saved .parallel/wave-plan.json and merges ONLY track-a, leaving beta.py untouched
    merge_out = io.StringIO()
    with redirect_stdout(merge_out):
        rc_merge = main(
            ["merge", "--repo", str(tmp_path), "--wave", "1", "--kit", "spec-kit", "--skip-gates"]
        )
    assert rc_merge == 0
    m_payload = json.loads(merge_out.getvalue())
    assert m_payload["merged_branches"] == ["parallel/track-a"]
    assert (tmp_path / "src" / "alpha.py").read_text(encoding="utf-8") == "alpha = 1\n"
    assert (tmp_path / "src" / "beta.py").read_text(encoding="utf-8") == "beta = 0\n"


def test_cli_e2e_partial_merge_to_prepare_next_wave_delivers_nack(tmp_path: Path) -> None:
    """E2E CLI test: merge default partial_merge=True -> prepare Wave 2 delivers NACK."""
    from sdd_parallel.worktree import _restore_os_writable

    subprocess.run(["git", "init", "-b", "master"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test Runner"], cwd=tmp_path, check=True)

    spec_dir = tmp_path / "specs" / "001-feat"
    spec_dir.mkdir(parents=True)
    (spec_dir / "tasks.md").write_text(
        "# Tasks\n"
        "- [ ] T001 [P] Alpha work in `src/shared.py`\n"
        "- [ ] T002 [P] Beta work in `src/beta.py`\n",
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "shared.py").write_text("shared = 0\n", encoding="utf-8")
    (tmp_path / "src" / "beta.py").write_text("beta = 0\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)

    assert main(["plan", "--repo", str(tmp_path), "--kit", "spec-kit"]) == 0
    assert (
        main(["prepare", "--repo", str(tmp_path), "--wave", "1", "--kit", "spec-kit", "--headless"])
        == 0
    )

    wt_a = tmp_path / ".worktrees" / "track-a"
    wt_b = tmp_path / ".worktrees" / "track-b"

    # Track A edits src/shared.py and marks T001 done
    (wt_a / "src" / "shared.py").write_text("shared = 1\n", encoding="utf-8")
    tasks_a = wt_a / "specs" / "001-feat" / "tasks.md"
    tasks_a.write_text(
        tasks_a.read_text(encoding="utf-8").replace("[ ] T001", "[x] T001"),
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "add", "src/shared.py", "specs/001-feat/tasks.md"],
        cwd=wt_a,
        check=True,
    )
    subprocess.run(["git", "commit", "-m", "feat(T001)"], cwd=wt_a, check=True)

    # Track B edits src/beta.py and conflicts on src/shared.py
    (wt_b / "src" / "beta.py").write_text("beta = 1\n", encoding="utf-8")
    _restore_os_writable(wt_b, ["src/shared.py"])
    (wt_b / "src" / "shared.py").write_text("shared = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/beta.py", "src/shared.py"], cwd=wt_b, check=True)
    subprocess.run(["git", "commit", "-m", "conflict(T002)"], cwd=wt_b, check=True)

    # CLI merge WITHOUT --partial-merge (uses new CLI default partial_merge=True)
    merge_out = io.StringIO()
    with redirect_stdout(merge_out):
        rc_merge = main(
            ["merge", "--repo", str(tmp_path), "--wave", "1", "--kit", "spec-kit", "--skip-gates"]
        )
    assert rc_merge == 0
    m_payload = json.loads(merge_out.getvalue())
    assert m_payload["partial_merge"] is True
    assert m_payload["merged_branches"] == ["parallel/track-a"]
    assert len(m_payload["rejected_tracks"]) == 1

    # Master working tree stays completely clean
    st_master = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert st_master == "", f"Expected clean master after partial merge, got: {st_master}"

    # Prepare Wave 2 reads .parallel/wave-plan.json, keeps enriched_writes, and injects NACK
    prep2_out = io.StringIO()
    with redirect_stdout(prep2_out):
        rc_prep2 = main(
            ["prepare", "--repo", str(tmp_path), "--wave", "2", "--kit", "spec-kit", "--headless"]
        )
    assert rc_prep2 == 0
    p2_payload = json.loads(prep2_out.getvalue())
    assert len(p2_payload["worktrees"]) == 1
    wt_w2 = Path(p2_payload["worktrees"][0])

    lock_w2 = json.loads((wt_w2 / ".parallel-lock.json").read_text(encoding="utf-8"))
    assert lock_w2["stories"] == ["T002"]
    assert "src/shared.py" in lock_w2["allowed_write_globs"]

    ctx_md = (wt_w2 / ".tmp" / "track-context.md").read_text(encoding="utf-8")
    assert "PREVIOUS WAVE BARRIER NACK (ADAPT TO UPDATED MASTER)" in ctx_md
    assert "src/shared.py" in ctx_md

    wt_fb = json.loads((wt_w2 / ".tmp" / "barrier-feedback.json").read_text(encoding="utf-8"))
    assert wt_fb["failed_stage"] == "git_merge"
    assert len(wt_fb["all_feedbacks"]) == 1


def test_cli_merge_no_partial_merge_flag_forces_atomic_rollback(tmp_path: Path) -> None:
    """--no-partial-merge rolls back the entire wave to pre_merge_head and exits with code 2."""
    from sdd_parallel.worktree import _restore_os_writable

    subprocess.run(["git", "init", "-b", "master"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test Runner"], cwd=tmp_path, check=True)

    spec_dir = tmp_path / "specs" / "001-feat"
    spec_dir.mkdir(parents=True)
    (spec_dir / "tasks.md").write_text(
        "# Tasks\n"
        "- [ ] T001 [P] Alpha work in `src/shared.py`\n"
        "- [ ] T002 [P] Beta work in `src/beta.py`\n",
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "shared.py").write_text("shared = 0\n", encoding="utf-8")
    (tmp_path / "src" / "beta.py").write_text("beta = 0\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)

    pre_merge_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    assert main(["plan", "--repo", str(tmp_path), "--kit", "spec-kit"]) == 0
    assert (
        main(["prepare", "--repo", str(tmp_path), "--wave", "1", "--kit", "spec-kit", "--headless"])
        == 0
    )

    wt_a = tmp_path / ".worktrees" / "track-a"
    wt_b = tmp_path / ".worktrees" / "track-b"

    (wt_a / "src" / "shared.py").write_text("shared = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/shared.py"], cwd=wt_a, check=True)
    subprocess.run(["git", "commit", "-m", "a"], cwd=wt_a, check=True)

    _restore_os_writable(wt_b, ["src/shared.py"])
    (wt_b / "src" / "shared.py").write_text("shared = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/shared.py"], cwd=wt_b, check=True)
    subprocess.run(["git", "commit", "-m", "b conflict"], cwd=wt_b, check=True)

    err_buf = io.StringIO()
    with redirect_stderr(err_buf):
        rc = main(
            [
                "merge",
                "--repo",
                str(tmp_path),
                "--wave",
                "1",
                "--kit",
                "spec-kit",
                "--skip-gates",
                "--no-partial-merge",
            ]
        )
    assert rc == 2
    err_json = json.loads(err_buf.getvalue())
    assert err_json["status"] == "error"
    assert "transactional auto-rollback" in err_json["error"]

    head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert head_after == pre_merge_head
    assert (tmp_path / "src" / "shared.py").read_text(encoding="utf-8") == "shared = 0\n"


def test_plan_and_abort_clear_stale_barrier_feedback(tmp_path: Path) -> None:
    """sdd-parallel plan and abort-wave delete stale .parallel/barrier-feedback-*.json files."""
    subprocess.run(["git", "init", "-b", "master"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test Runner"], cwd=tmp_path, check=True)

    spec_dir = tmp_path / "specs" / "001-feat"
    spec_dir.mkdir(parents=True)
    (spec_dir / "tasks.md").write_text(
        "- [ ] T001 Alpha in `src/a.py`\n", encoding="utf-8"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("a = 0\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)

    parallel_dir = tmp_path / ".parallel"
    parallel_dir.mkdir(parents=True, exist_ok=True)
    stale_1 = parallel_dir / "barrier-feedback-OLD-1.json"
    stale_1.write_text('{"failed_stage": "git_merge"}\n', encoding="utf-8")

    assert main(["plan", "--repo", str(tmp_path), "--kit", "spec-kit"]) == 0
    assert not stale_1.exists()

    stale_2 = parallel_dir / "barrier-feedback-OLD-2.json"
    stale_2.write_text('{"failed_stage": "checkpoint_gate"}\n', encoding="utf-8")
    assert main(["abort-wave", "--repo", str(tmp_path), "--wave", "1"]) == 0
    assert not stale_2.exists()


def test_cli_plan_cache_validation_and_inspector_priority(tmp_path: Path) -> None:
    """Verify plan cache invalidation on new tasks/max_tracks and inspector priority."""
    from sdd_parallel.cli import _load_or_compute_repo_plan
    from sdd_parallel.inspector import inspect_plan

    subprocess.run(["git", "init", "-b", "master"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test Runner"], cwd=tmp_path, check=True)

    spec_dir = tmp_path / "specs" / "001-feat"
    spec_dir.mkdir(parents=True)
    tasks_md = spec_dir / "tasks.md"
    tasks_md.write_text(
        "- [ ] T001 [P] One in `src/one.py`\n"
        "- [ ] T002 [P] Two in `src/two.py`\n",
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "one.py").write_text("one = 1\n", encoding="utf-8")
    (tmp_path / "src" / "two.py").write_text("two = 2\n", encoding="utf-8")
    (tmp_path / "src" / "three.py").write_text("three = 3\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)

    assert main(["plan", "--repo", str(tmp_path), "--kit", "spec-kit"]) == 0
    artifacts_plan = tmp_path / ".parallel" / "artifacts" / "wave-plan.json"
    runtime_plan = tmp_path / ".parallel" / "wave-plan.json"

    # Simulate rescheduled runtime plan with Wave 2 marker in .parallel/wave-plan.json
    art_data = json.loads(artifacts_plan.read_text(encoding="utf-8"))
    rt_data = json.loads(json.dumps(art_data))
    rt_data["spine_files"] = ["src/runtime_rescheduled_marker.py"]
    runtime_plan.write_text(json.dumps(rt_data, indent=2), encoding="utf-8")

    # 1. Valid cache is loaded when tasks and max_tracks match
    loaded = _load_or_compute_repo_plan(tmp_path, max_tracks=3, kit="spec-kit")
    assert loaded.spine_files == ["src/runtime_rescheduled_marker.py"]

    # 2. Inspector prioritizes .parallel/wave-plan.json over .parallel/artifacts/wave-plan.json
    out_html = tmp_path / "inspector_prio.html"
    inspect_plan(repo_root=tmp_path, output_path=out_html, open_browser=False)
    assert "src/runtime_rescheduled_marker.py" in out_html.read_text(encoding="utf-8")

    # 3. Smaller --max-tracks invalidates cache (Wave 1 had 2 tracks > max_tracks=1)
    recomputed_small = _load_or_compute_repo_plan(tmp_path, max_tracks=1, kit="spec-kit")
    assert recomputed_small.spine_files != ["src/runtime_rescheduled_marker.py"]
    assert all(len(w.tracks) <= 1 for w in recomputed_small.waves)

    # 4. Adding a new task in tasks.md invalidates cache
    tasks_md.write_text(
        tasks_md.read_text(encoding="utf-8") + "- [ ] T003 [P] Three in `src/three.py`\n",
        encoding="utf-8",
    )
    recomputed_new = _load_or_compute_repo_plan(tmp_path, max_tracks=3, kit="spec-kit")
    assert "T003" in recomputed_new.scheduled_task_ids
    assert recomputed_new.spine_files != ["src/runtime_rescheduled_marker.py"]


