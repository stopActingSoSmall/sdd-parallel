"""Unit and integration tests for manifest, structural signals, and routing overrides (Task 30)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sdd_parallel.autopilot import AutopilotLauncher
from sdd_parallel.cli import main as cli_main
from sdd_parallel.manifest import (
    apply_routing_overrides,
    extract_prevented_conflicts,
    harvest_wave_manifest,
    load_routing_overrides,
    save_wave_manifest,
)
from sdd_parallel.models import (
    ConflictEdge,
    ConflictType,
    EffortTier,
    FileRole,
    RoutingOverride,
    TaskNode,
    TaskStructuralSignals,
    Wave,
    WavePlan,
    WaveTrack,
)
from sdd_parallel.scheduler import BSPWaveScheduler


def test_task_structural_signals_roundtrip() -> None:
    signals = TaskStructuralSignals(
        files_count=4,
        max_fan_in=7,
        touches_spine=True,
        ast_loc=142,
        conflict_degree=3,
    )
    s_dict = signals.to_dict()
    assert s_dict == {
        "files_count": 4,
        "max_fan_in": 7,
        "touches_spine": True,
        "ast_loc": 142,
        "conflict_degree": 3,
    }
    reconstructed = TaskStructuralSignals.from_dict(s_dict)
    assert reconstructed == signals


def test_routing_override_roundtrip() -> None:
    override = RoutingOverride(
        task_id="11-2",
        effort=EffortTier.HIGH,
        model="gemini-3.1-pro",
        execution_mode="CONTRACT_HARDENING",
        reason="Touches SPINE schema",
    )
    o_dict = override.to_dict()
    assert o_dict == {
        "task_id": "11-2",
        "effort": "high",
        "model": "gemini-3.1-pro",
        "execution_mode": "CONTRACT_HARDENING",
        "reason": "Touches SPINE schema",
    }
    reconstructed = RoutingOverride.from_dict("11-2", o_dict)
    assert reconstructed == override


def test_task_node_structural_signals_backward_compat() -> None:
    # 1. Without signals and overrides
    raw_node: dict[str, object] = {
        "id": "10-1",
        "title": "Baseline story",
        "write_files": ["src/app.py"],
    }
    task = TaskNode.from_dict(raw_node)
    assert task.structural_signals is None
    assert task.model_override is None
    assert task.execution_mode == "NORMAL"

    # 2. With signals and overrides
    task.structural_signals = TaskStructuralSignals(
        files_count=1,
        max_fan_in=2,
        touches_spine=False,
        ast_loc=50,
        conflict_degree=1,
    )
    task.model_override = "custom-flash"
    task.execution_mode = "SUBTRACTIVE"

    t_dict = task.to_dict()
    assert t_dict["structural_signals"] is not None
    assert t_dict["model_override"] == "custom-flash"
    assert t_dict["execution_mode"] == "SUBTRACTIVE"

    reconstructed = TaskNode.from_dict(t_dict)
    assert reconstructed.structural_signals == task.structural_signals
    assert reconstructed.model_override == "custom-flash"
    assert reconstructed.execution_mode == "SUBTRACTIVE"


def test_wave_track_and_wave_plan_roundtrip() -> None:
    track = WaveTrack(
        track_id="track-a",
        task_ids=["10-1", "10-2"],
        effort=EffortTier.HIGH,
        model_override="pro",
        execution_mode="NORMAL",
    )
    t_dict = track.to_dict()
    assert t_dict["model_override"] == "pro"
    reconstructed_track = WaveTrack.from_dict(t_dict)
    assert reconstructed_track.track_id == "track-a"
    assert reconstructed_track.effort == EffortTier.HIGH
    assert reconstructed_track.model_override == "pro"

    wave = Wave(wave_number=1, tracks=[track])
    task_node = TaskNode(id="10-1", title="Task 1")
    plan = WavePlan(
        sdd_kit="bmad",
        waves=[wave],
        edges=[],
        tasks=[task_node],
    )
    plan_dict = plan.to_dict()
    assert "tasks" in plan_dict
    assert len(plan_dict["tasks"]) == 1

    reconstructed_plan = WavePlan.from_dict(plan_dict)
    assert reconstructed_plan.sdd_kit == "bmad"
    assert len(reconstructed_plan.waves) == 1
    assert len(reconstructed_plan.tasks) == 1
    assert reconstructed_plan.tasks[0].id == "10-1"


def test_scheduler_computes_structural_signals(tmp_path: Path) -> None:
    # Create sample files
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    schema_file = src_dir / "schema.py"
    schema_file.write_text("class Schema:\n    pass\n", encoding="utf-8")
    app_file = src_dir / "app.py"
    app_file.write_text("import os\n\ndef run():\n    return 42\n", encoding="utf-8")

    class DummyASTScanner:
        def __init__(self, repo_root: Path) -> None:
            self.repo_root = repo_root

        def link_tasks(self, tasks: object) -> None:
            pass

        def get_importers(self, rel_path: str) -> set[str]:
            if "schema" in rel_path:
                return {"src/app.py", "src/models.py", "src/views.py"}
            return set()

    scanner = DummyASTScanner(tmp_path)
    scheduler = BSPWaveScheduler(
        max_tracks=2,
        file_roles={"src/schema.py": FileRole.SPINE, "src/app.py": FileRole.STANDARD},
        ast_scanner=scanner,
        repo_root=tmp_path,
    )

    task_a = TaskNode(id="T-1", title="Update Schema", write_files=["src/schema.py"])
    task_b = TaskNode(id="T-2", title="Update App", write_files=["src/app.py"])

    plan = scheduler.schedule([task_a, task_b], sdd_kit="bmad")
    assert len(plan.tasks) == 2

    # Check T-1 signals
    sig_a = plan.tasks[0].structural_signals
    assert sig_a is not None
    assert sig_a.files_count == 1
    assert sig_a.max_fan_in == 3
    assert sig_a.touches_spine is True
    assert sig_a.ast_loc == 2

    # Check T-2 signals
    sig_b = plan.tasks[1].structural_signals
    assert sig_b is not None
    assert sig_b.files_count == 1
    assert sig_b.max_fan_in == 0
    assert sig_b.touches_spine is False
    assert sig_b.ast_loc == 4


def test_load_routing_overrides(tmp_path: Path) -> None:
    # 1. Valid overrides file
    overrides_file = tmp_path / "routing-overrides.json"
    content = {
        "schema_version": "1.0",
        "router": "clef-flash-v1",
        "overrides": {
            "11-2": {
                "effort": "high",
                "model": "gemini-3.1-pro",
                "execution_mode": "CONTRACT_HARDENING",
                "reason": "Touches SPINE schema",
            },
            "11-3": {
                "effort": "low",
                "model": "flash-lite",
            },
        },
    }
    overrides_file.write_text(json.dumps(content), encoding="utf-8-sig")

    loaded = load_routing_overrides(overrides_file)
    assert len(loaded) == 2
    assert loaded["11-2"].effort == EffortTier.HIGH
    assert loaded["11-2"].model == "gemini-3.1-pro"
    assert loaded["11-2"].execution_mode == "CONTRACT_HARDENING"
    assert loaded["11-3"].effort == EffortTier.LOW
    assert loaded["11-3"].model == "flash-lite"
    assert loaded["11-3"].execution_mode == "NORMAL"

    # 2. Non-existent file
    with pytest.raises(ValueError, match="does not exist"):
        load_routing_overrides(tmp_path / "missing.json")

    # 3. Invalid JSON
    bad_file = tmp_path / "bad.json"
    bad_file.write_text("{not-valid-json", encoding="utf-8")
    with pytest.raises(ValueError, match="Failed to parse"):
        load_routing_overrides(bad_file)

    # 4. Invalid structure (missing overrides)
    bad_schema = tmp_path / "bad_schema.json"
    bad_schema.write_text(json.dumps({"router": "clef"}), encoding="utf-8")
    with pytest.raises(ValueError, match="missing or non-dict 'overrides'"):
        load_routing_overrides(bad_schema)


def test_apply_routing_overrides() -> None:
    task_a = TaskNode(id="T-1", title="T1", effort=EffortTier.LOW)
    task_b = TaskNode(id="T-2", title="T2", effort=EffortTier.MEDIUM)
    track = WaveTrack(
        track_id="track-a",
        task_ids=["T-1", "T-2"],
        effort=EffortTier.MEDIUM,
    )
    wave = Wave(wave_number=1, tracks=[track])
    plan = WavePlan(
        sdd_kit="bmad",
        waves=[wave],
        edges=[],
        tasks=[task_a, task_b],
    )

    overrides = {
        "T-1": RoutingOverride(
            task_id="T-1",
            effort=EffortTier.HIGH,
            model="gemini-3.1-pro",
            execution_mode="NORMAL",
        ),
        "T-2": RoutingOverride(
            task_id="T-2",
            effort=EffortTier.LOW,
            model="flash-lite",
            execution_mode="SUBTRACTIVE",
        ),
    }

    updated_plan = apply_routing_overrides(plan, overrides)
    assert updated_plan is plan
    # TaskNodes updated
    assert task_a.effort == EffortTier.HIGH
    assert task_a.model_override == "gemini-3.1-pro"
    assert task_b.effort == EffortTier.LOW
    assert task_b.model_override == "flash-lite"
    assert task_b.execution_mode == "SUBTRACTIVE"

    # Track updated: max effort is HIGH, winning model is gemini-3.1-pro from highest effort
    assert track.effort == EffortTier.HIGH
    assert track.model_override == "gemini-3.1-pro"


def test_autopilot_respects_model_override_and_execution_mode(tmp_path: Path) -> None:
    track = WaveTrack(
        track_id="track-a",
        task_ids=["T-1"],
        effort=EffortTier.LOW,
        model_override="claude-3-7-sonnet",
        execution_mode="CONTRACT_HARDENING",
    )
    wave = Wave(wave_number=1, tracks=[track])
    plan = WavePlan(
        sdd_kit="bmad",
        waves=[wave],
        edges=[],
        tasks=[TaskNode(id="T-1", title="Task")],
    )

    launcher = AutopilotLauncher(repo_root=tmp_path)
    specs = launcher.build_launch_specs(plan=plan, wave_number=1, harness="claude-code")
    assert len(specs) == 1
    spec = specs[0]
    assert spec["model"] == "claude-3-7-sonnet"
    assert spec["execution_mode"] == "CONTRACT_HARDENING"
    assert "[EXECUTION_MODE: CONTRACT_HARDENING]" in spec["prompt"]


def test_extract_prevented_conflicts() -> None:
    edges = [
        ConflictEdge(
            source="T-1",
            target="T-2",
            conflict_type=ConflictType.DIRECT_COLLISION,
            reason="Direct collision on main.py",
            shared_files=("main.py",),
            directed=False,
        ),
        ConflictEdge(
            source="T-2",
            target="T-3",
            conflict_type=ConflictType.SPINE_ORDER,
            reason="Spine order on schema.py",
            shared_files=("schema.py",),
            directed=True,
        ),
        ConflictEdge(
            source="T-1",
            target="T-3",
            conflict_type=ConflictType.EXPLICIT_DEP,
            reason="T3 explicitly depends on T1",
            directed=True,
        ),
    ]
    plan = WavePlan(
        sdd_kit="bmad",
        waves=[],
        edges=edges,
    )
    prevented = extract_prevented_conflicts(plan)
    assert len(prevented) == 2
    types = {p.conflict_type for p in prevented}
    assert types == {ConflictType.DIRECT_COLLISION.value, ConflictType.SPINE_ORDER.value}


def test_harvest_and_save_wave_manifest(tmp_path: Path) -> None:
    wt_dir = tmp_path / ".worktrees" / "track-a"
    (wt_dir / ".tmp").mkdir(parents=True)
    session_data = {
        "session_id": "ses-9988",
        "harness": "antigravity",
        "started_at": "2026-11-03T10:00:00+00:00",
        "finished_at": "2026-11-03T10:02:30+00:00",
        "exit_code": 0,
    }
    (wt_dir / ".tmp" / "runner-session.json").write_text(
        json.dumps(session_data), encoding="utf-8"
    )

    track = WaveTrack(
        track_id="track-a",
        task_ids=["10-1"],
        effort=EffortTier.HIGH,
        model_override="pro",
        execution_mode="NORMAL",
    )
    wave = Wave(wave_number=1, tracks=[track])
    plan = WavePlan(
        sdd_kit="bmad",
        waves=[wave],
        edges=[],
        tasks=[TaskNode(id="10-1", title="Task")],
    )

    manifest = harvest_wave_manifest(
        repo_root=tmp_path,
        plan=plan,
        wave_id=1,
        sdd_kit="bmad",
        fixed_slots=True,
    )
    assert manifest.wave_id == 1
    assert len(manifest.tracks) == 1
    t_entry = manifest.tracks[0]
    assert t_entry.session_id == "ses-9988"
    assert t_entry.harness == "antigravity"
    assert t_entry.duration_seconds == 150.0
    assert t_entry.status == "completed"
    assert t_entry.selected_model == "pro"

    saved_path = save_wave_manifest(tmp_path, manifest)
    assert saved_path.is_file()
    assert (tmp_path / ".parallel" / "run-manifest.json").is_file()

    run_manifest = json.loads(
        (tmp_path / ".parallel" / "run-manifest.json").read_text(encoding="utf-8")
    )
    assert "1" in run_manifest["waves"]
    assert run_manifest["waves"]["1"]["tracks"][0]["session_id"] == "ses-9988"


def test_zero_finops_and_pricing_in_core() -> None:
    """Ensure no USD calculation, tariff pricing tables or OTel/Langfuse bloat in core."""
    core_dir = Path(__file__).parent.parent / "src" / "sdd_parallel"
    banned_tokens = [
        "DEFAULT_TIER_RATES",
        "cost_usd",
        "actual_cost_usd",
        "opentelemetry",
        "langfuse",
    ]
    for py_file in core_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8").lower()
        for token in banned_tokens:
            assert token.lower() not in content, (
                f"Found banned token '{token}' in {py_file.as_posix()} violating ADR-0009"
            )


def test_cli_routing_file_and_manifest(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import subprocess

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test Runner"], cwd=tmp_path, check=True)

    impl_dir = tmp_path / "_bmad-output" / "implementation-artifacts"
    plan_dir = tmp_path / "_bmad-output" / "planning-artifacts"
    impl_dir.mkdir(parents=True)
    plan_dir.mkdir(parents=True)

    (impl_dir / "sprint-status.yaml").write_text(
        'last_updated: "10-21-2026 12:00"\n'
        "development_status:\n"
        "  11-2-first-story: ready-for-dev\n",
        encoding="utf-8",
    )
    (plan_dir / "epics.md").write_text(
        "## Epic 11\n### Story 11-2: First Story\nFiles: `src/a.py`\n",
        encoding="utf-8",
    )
    (plan_dir / "epic-11-context.md").write_text("# Context 11", encoding="utf-8")
    (impl_dir / "epic-11-context.md").write_text("# Context 11", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")

    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)

    routing_file = tmp_path / "routing-overrides.json"
    routing_file.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "overrides": {
                    "11-2": {
                        "effort": "high",
                        "model": "gemini-3.1-pro",
                        "execution_mode": "CONTRACT_HARDENING",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    # 1. Test plan with --routing-file
    ret = cli_main(["plan", "--repo", str(tmp_path), "--routing-file", str(routing_file)])
    assert ret == 0
    captured = capsys.readouterr()
    assert "high" in captured.out

    # Check generated wave-plan.json
    plan_file = tmp_path / "_bmad-output" / "parallel-artifacts" / "wave-plan.json"
    assert plan_file.is_file()
    plan_data = json.loads(plan_file.read_text(encoding="utf-8"))
    assert plan_data["tasks"][0]["effort"] == "high"
    assert plan_data["tasks"][0]["model_override"] == "gemini-3.1-pro"
    assert plan_data["tasks"][0]["execution_mode"] == "CONTRACT_HARDENING"
    assert plan_data["tasks"][0]["structural_signals"] is not None

    # 2. Test prepare
    ret = cli_main(
        [
            "prepare",
            "--repo",
            str(tmp_path),
            "--wave",
            "1",
            "--routing-file",
            str(routing_file),
        ]
    )
    assert ret == 0
    capsys.readouterr()

    # 3. Test verify-wave saves manifest
    ret = cli_main(["verify-wave", "--repo", str(tmp_path), "--wave", "1"])
    assert ret == 0
    captured = capsys.readouterr()
    verify_out = json.loads(captured.out)
    assert verify_out["compliant"] is True
    assert "manifest_path" in verify_out
    assert Path(verify_out["manifest_path"]).is_file()

    # Simulate worker commit in track-a
    wt_a = tmp_path / ".worktrees" / "track-a"
    (wt_a / "src" / "a.py").write_text("x = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=wt_a, check=True)
    subprocess.run(["git", "commit", "-m", "feat: completed 11-2"], cwd=wt_a, check=True)

    # 4. Test merge saves manifest
    ret = cli_main(["merge", "--repo", str(tmp_path), "--wave", "1", "--cleanup"])
    assert ret == 0
    captured = capsys.readouterr()
    merge_out = json.loads(captured.out)
    assert "manifest_path" in merge_out
    assert Path(merge_out["manifest_path"]).is_file()

