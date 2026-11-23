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


