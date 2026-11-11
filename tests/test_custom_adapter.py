"""Unit tests for Pluggable Custom SDD Adapter and init-custom onboarding wizard (Task 34a)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sdd_parallel.adapters import get_adapter
from sdd_parallel.adapters.custom import CustomManifestAdapter
from sdd_parallel.cli import main
from sdd_parallel.models import EffortTier, LockManifest, TaskStatus, Wave, WaveTrack
from sdd_parallel.runner import _create_default_invoker
from sdd_parallel.scheduler import BSPWaveScheduler
from sdd_parallel.worktree import WorktreeManager


def test_custom_adapter_detect_and_registration(tmp_path: Path) -> None:
    # 1. Without manifest: detect is False
    adapter = CustomManifestAdapter(tmp_path)
    assert adapter.detect() is False

    # 2. Via get_adapter
    kit_name, inst = get_adapter(tmp_path, kit="custom")
    assert kit_name == "custom"
    assert isinstance(inst, CustomManifestAdapter)

    # 3. Create manifest
    parallel_dir = tmp_path / ".parallel"
    parallel_dir.mkdir(parents=True)
    (parallel_dir / "custom-kit.yaml").write_text(
        "version: '1.0'\nkit_id: 'custom'\nname: 'Test Kit'\n",
        encoding="utf-8",
    )
    assert adapter.detect() is True


def test_custom_adapter_parses_markdown_standalone_task_files(tmp_path: Path) -> None:
    phases_dir = tmp_path / "phases"
    phases_dir.mkdir(parents=True)

    task1 = phases_dir / "task_01_core.md"
    task1.write_text(
        "# 📋 Task 01: Core Architecture\n\n"
        "- **Status:** 🎯 PLANNED\n"
        "- **Effort:** High\n\n"
        "### Files Allowed to Edit / Create:\n"
        "- `src/custom/core.py`\n"
        "- `src/custom/utils.py`\n",
        encoding="utf-8",
    )

    task2 = phases_dir / "task_02_api.md"
    task2.write_text(
        "# 📋 Task 02: API Interface\n\n"
        "- **Status:** READY\n"
        "- **Dependencies:** [Task 01](task_01_core.md)\n"
        "- **Effort:** Low\n\n"
        "### Files Allowed to Edit / Create:\n"
        "- `src/custom/api.py`\n",
        encoding="utf-8",
    )

    manifest_file = tmp_path / ".parallel" / "custom-kit.yaml"
    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    manifest_file.write_text(
        "version: '1.0'\n"
        "roadmap_sources:\n"
        "  - path: 'phases/task_*.md'\n"
        "    format: 'markdown'\n"
        "status_ranks:\n"
        "  planned: 'ready-for-dev'\n"
        "  ready: 'ready-for-dev'\n"
        "forbidden_files:\n"
        "  - 'MASTER_INDEX.md'\n"
        "worker_command: '/run-task {task_id}'\n",
        encoding="utf-8",
    )

    adapter = CustomManifestAdapter(tmp_path)
    tasks = adapter.load_tasks(include_done=True)
    assert len(tasks) == 2

    t1 = next(t for t in tasks if "01" in t.id)
    assert t1.effort == EffortTier.HIGH
    assert t1.status == TaskStatus.READY_FOR_DEV
    assert "src/custom/core.py" in t1.write_files
    assert "src/custom/utils.py" in t1.write_files

    t2 = next(t for t in tasks if "02" in t.id)
    assert t2.effort == EffortTier.LOW
    assert "01" in t2.depends_on

    # Test worker command
    track = WaveTrack(
        track_id="track-a",
        task_ids=["01", "02"],
        effort=EffortTier.MEDIUM,
        exclusive_write_files=[],
    )
    cmd = adapter.render_worker_command(track)
    assert cmd == "`/run-task 01 02`"

    # Test forbidden files
    wave = Wave(wave_number=1, tracks=[track])
    forbid = adapter.get_forbidden_files(wave, track)
    assert "MASTER_INDEX.md" in forbid


def test_custom_adapter_parses_checklist_and_resets_status(tmp_path: Path) -> None:
    specs_dir = tmp_path / "specs"
    specs_dir.mkdir(parents=True)
    tasks_file = specs_dir / "tasks.md"
    tasks_file.write_text(
        "# Specification Tasks\n\n"
        "- [x] `T001`: Implement core engine\n"
        "  - Files: `src/engine.py`\n"
        "- [ ] `T002`: Implement CLI interface\n"
        "  - Files: `src/cli.py`\n"
        "  - Depends on: `T001`\n",
        encoding="utf-8",
    )

    manifest_file = tmp_path / ".parallel" / "custom-kit.yaml"
    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    manifest_file.write_text(
        "version: '1.0'\n"
        "roadmap_sources:\n"
        "  - path: 'specs/tasks.md'\n"
        "    format: 'checklist'\n",
        encoding="utf-8",
    )

    adapter = CustomManifestAdapter(tmp_path)
    # include_done=True
    all_tasks = adapter.load_tasks(include_done=True)
    assert len(all_tasks) == 2
    assert all_tasks[0].status == TaskStatus.DONE
    assert all_tasks[1].status == TaskStatus.READY_FOR_DEV
    assert "src/engine.py" in all_tasks[0].write_files

    # include_done=False
    active_tasks = adapter.load_tasks(include_done=False)
    assert len(active_tasks) == 1
    assert active_tasks[0].id == "T002"

    # Reset task status
    adapter.reset_tasks_status(["T001"])
    reloaded = adapter.load_tasks(include_done=True)
    assert reloaded[0].status == TaskStatus.READY_FOR_DEV
    content = tasks_file.read_text(encoding="utf-8")
    assert "- [ ] `T001`" in content


def test_custom_adapter_parses_yaml_roadmap(tmp_path: Path) -> None:
    roadmap_file = tmp_path / "roadmap.yaml"
    roadmap_file.write_text(
        "tasks:\n"
        "  - id: 'FEAT-1'\n"
        "    title: 'Authentication Module'\n"
        "    status: 'in-progress'\n"
        "    effort: 'high'\n"
        "    files: ['src/auth/tokens.py', 'src/auth/jwt.py']\n"
        "  - id: 'FEAT-2'\n"
        "    title: 'User Profile API'\n"
        "    status: 'todo'\n"
        "    effort: 'medium'\n"
        "    after: ['FEAT-1']\n"
        "    files: ['src/api/profile.py']\n",
        encoding="utf-8",
    )

    manifest_file = tmp_path / ".parallel" / "custom-kit.yaml"
    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    manifest_file.write_text(
        "version: '1.0'\n"
        "roadmap_sources:\n"
        "  - path: 'roadmap.yaml'\n"
        "    format: 'yaml'\n",
        encoding="utf-8",
    )

    adapter = CustomManifestAdapter(tmp_path)
    tasks = adapter.load_tasks(include_done=True)
    assert len(tasks) == 2
    assert tasks[0].status == TaskStatus.IN_PROGRESS
    assert tasks[1].depends_on == ["FEAT-1"]

    # Schedule waves
    scheduler = BSPWaveScheduler(max_tracks=2)
    plan = scheduler.schedule(tasks)
    assert len(plan.waves) == 2


def test_init_custom_cli_wizard(tmp_path: Path, monkeypatch: Any) -> None:
    # 1. Create a dummy spec folder with task files
    tasks_dir = tmp_path / "custom_specs"
    tasks_dir.mkdir(parents=True)
    (tasks_dir / "story_01.md").write_text(
        "# 📋 Story 01: Setup\n- **Status:** planned\n### Files Allowed to Edit:\n- `src/a.py`\n",
        encoding="utf-8",
    )

    # 2. Run init-custom in dry-run mode
    rc_dry = main([
        "init-custom",
        "--repo", str(tmp_path),
        "--specs-dir", "custom_specs",
        "--format", "markdown",
        "--non-interactive",
        "--dry-run",
    ])
    assert rc_dry == 0
    assert not (tmp_path / ".parallel" / "custom-kit.yaml").exists()

    # 3. Run init-custom non-interactive
    rc = main([
        "init-custom",
        "--repo", str(tmp_path),
        "--specs-dir", "custom_specs",
        "--format", "markdown",
        "--test-cmd", "pytest -v",
        "--non-interactive",
    ])
    assert rc == 0
    config_file = tmp_path / ".parallel" / "custom-kit.yaml"
    assert config_file.is_file()

    # 4. Verify adapter detects and loads tasks
    adapter = CustomManifestAdapter(tmp_path)
    assert adapter.detect() is True
    tasks = adapter.load_tasks(include_done=True)
    assert len(tasks) == 1
    assert "src/a.py" in tasks[0].write_files


def test_claude_code_hardening_env_vars_in_invoker(tmp_path: Path, monkeypatch: Any) -> None:
    invoked_env: dict[str, str] = {}

    import subprocess

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        env = kwargs.get("env") or {}
        invoked_env.update(env)
        return subprocess.CompletedProcess(cmd, returncode=0, stdout='{"session_id": "s-123"}')

    monkeypatch.setattr(subprocess, "run", fake_run)

    invoker = _create_default_invoker(
        harness="claude-code",
        timeout_seconds=30.0,
    )
    invoker("implement story", "claude-opus-4-6", EffortTier.HIGH, tmp_path)

    assert invoked_env.get("CLAUDE_CODE_SUBAGENT_EFFORT") == "high"
    assert invoked_env.get("CLAUDE_CODE_PLUGIN_DIR_WATCH") == "0"
    assert invoked_env.get("CLAUDE_CODE_DISABLE_MCP_TASK_BACKGROUND") == "1"


def test_worktree_track_context_includes_token_hygiene(tmp_path: Path) -> None:
    wm = WorktreeManager(tmp_path)
    wave = Wave(wave_number=1, tracks=[])
    track = WaveTrack(
        track_id="track-a",
        task_ids=["T01"],
        effort=EffortTier.MEDIUM,
        exclusive_write_files=["src/x.py"],
    )
    lock = LockManifest(
        wave_id=1,
        track_id="track-a",
        stories=["T01"],
        repo_root=str(tmp_path),
        worktree_root=str(tmp_path / "wt"),
        allowed_write_globs=["src/x.py"],
        additive_shared_files=[],
        forbidden_files={"src/forbidden.py": "Locked by spine"},
    )
    context_md = wm.render_track_context_md(wave, track, lock, headless=True)
    assert "Context & Token Hygiene" in context_md
    assert "unchanged contract" in context_md
