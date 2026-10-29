"""Unit tests for sdd-parallel CLI commands (run-guide and Assisted flow)."""

from __future__ import annotations

import io
import json
import subprocess
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

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
