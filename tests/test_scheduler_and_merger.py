"""Unit and golden-case tests for BSPWaveScheduler, status_merger, and ASTHotspotScanner."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from sdd_parallel.ast_scanner import ASTHotspotScanner
from sdd_parallel.models import ConflictType, EffortTier, FileRole, TaskNode
from sdd_parallel.scheduler import BSPWaveScheduler, CyclicDependencyError
from sdd_parallel.status_merger import merge_sprint_status_yaml
from sdd_parallel.worktree import WorktreeManager


def _build_epics_11_14_tasks() -> list[TaskNode]:
    """Reference benchmark dataset with 13 multi-epic stories."""
    return [
        TaskNode(
            id="11-1",
            title="Path canonicalization & DuckDB v4",
            epic_id="epic-11",
            effort=EffortTier.LOW,
            write_files=[
                "src/agentlens/canonical/paths.py",
                "src/agentlens/canonical/models.py",
                "src/agentlens/storage/schema.py",
            ],
        ),
        TaskNode(
            id="11-2",
            title="Semantic interval invalidation",
            epic_id="epic-11",
            effort=EffortTier.HIGH,
            depends_on=["11-1"],
            write_files=[
                "src/agentlens/canonical/intervals.py",
                "src/agentlens/canonical/normalizer.py",
            ],
            shared_function_notes={
                "src/agentlens/canonical/normalizer.py": "_classify_reads post-pass only"
            },
        ),
        TaskNode(
            id="11-3",
            title="Ingestion hygiene & blacklist",
            epic_id="epic-11",
            effort=EffortTier.MEDIUM,
            depends_on=["11-1"],
            write_files=[
                "src/agentlens/canonical/manifest.py",
                "src/agentlens/canonical/normalizer.py",
                "src/agentlens/cli.py",
            ],
            shared_function_notes={
                "src/agentlens/canonical/normalizer.py": "error taxonomy counters only"
            },
        ),
        TaskNode(
            id="11-4",
            title="Parent-child correlation & worktree root",
            epic_id="epic-11",
            effort=EffortTier.HIGH,
            depends_on=["11-1", "11-3"],
            write_files=[
                "src/agentlens/canonical/models.py",
                "src/agentlens/storage/schema.py",
                "src/agentlens/adapters/antigravity.py",
                "src/agentlens/canonical/manifest.py",
            ],
        ),
        TaskNode(
            id="12-1",
            title="Token & USD accounting",
            epic_id="epic-12",
            effort=EffortTier.MEDIUM,
            depends_on=["11-4"],
            write_files=[
                "src/agentlens/canonical/models.py",
                "src/agentlens/storage/schema.py",
                "src/agentlens/canonical/pricing.py",
            ],
        ),
        TaskNode(
            id="12-2",
            title="Model ID & thinking budget",
            epic_id="epic-12",
            effort=EffortTier.MEDIUM,
            depends_on=["12-1"],
            write_files=[
                "src/agentlens/canonical/models.py",
                "src/agentlens/storage/schema.py",
                "src/agentlens/adapters/antigravity.py",
                "src/agentlens/cli.py",
            ],
        ),
        TaskNode(
            id="12-3",
            title="Antigravity SQLite reader",
            epic_id="epic-12",
            effort=EffortTier.HIGH,
            depends_on=["11-1"],
            write_files=[
                "src/agentlens/adapters/antigravity_sqlite.py",
                "src/agentlens/cli.py",
                "src/agentlens/mcp/server.py",
            ],
        ),
        TaskNode(
            id="13-1",
            title="TSI engine",
            epic_id="epic-13",
            effort=EffortTier.MEDIUM,
            depends_on=["11-2"],
            write_files=["src/agentlens/analytics/tsi.py"],
        ),
        TaskNode(
            id="13-2",
            title="Tipping point & guidance",
            epic_id="epic-13",
            effort=EffortTier.MEDIUM,
            depends_on=["13-1"],
            write_files=[
                "src/agentlens/analytics/tipping_point.py",
                "src/agentlens/cli.py",
                "src/agentlens/mcp/server.py",
            ],
        ),
        TaskNode(
            id="13-3",
            title="CSSCR cross-session convergence",
            epic_id="epic-13",
            effort=EffortTier.HIGH,
            depends_on=["11-4"],
            write_files=[
                "src/agentlens/analytics/convergence.py",
                "src/agentlens/cli.py",
            ],
        ),
        TaskNode(
            id="14-1",
            title="Project discovery & ghost pruning",
            epic_id="epic-14",
            effort=EffortTier.MEDIUM,
            depends_on=["11-3", "11-4"],
            write_files=[
                "src/agentlens/analytics/discovery.py",
                "src/agentlens/cli.py",
            ],
        ),
        TaskNode(
            id="14-2",
            title="Skill vitality & dead skills",
            epic_id="epic-14",
            effort=EffortTier.MEDIUM,
            depends_on=["12-1"],
            write_files=[
                "src/agentlens/analytics/skill_funnel.py",
                "src/agentlens/cli.py",
            ],
        ),
        TaskNode(
            id="14-3",
            title="agentlens init TUI",
            epic_id="epic-14",
            effort=EffortTier.LOW,
            depends_on=["14-1"],
            write_files=[
                "src/agentlens/onboarding_tui.py",
                "src/agentlens/cli.py",
            ],
        ),
    ]


def test_epics_11_14_full_coverage_and_spine_ordering() -> None:
    tasks = _build_epics_11_14_tasks()
    scheduler = BSPWaveScheduler(max_tracks=3)
    plan = scheduler.schedule(tasks, sdd_kit="bmad")

    # 1. 100% coverage invariant (all 13 tasks scheduled, including 13-3)
    assert len(plan.scheduled_task_ids) == 13
    assert set(plan.scheduled_task_ids) == {t.id for t in tasks}

    wave_of: dict[str, int] = {}
    for wave in plan.waves:
        assert len(wave.tracks) <= 3
        for tid in wave.all_task_ids:
            wave_of[tid] = wave.wave_number

    # 2. Spine Chain A: 11-1 -> 11-4 -> 12-1 -> 12-2
    assert wave_of["11-1"] < wave_of["11-4"] < wave_of["12-1"] < wave_of["12-2"]

    # 3. Churn Chain B: 11-1 -> 11-2 -> 13-1 -> 13-2
    assert wave_of["11-1"] < wave_of["11-2"] < wave_of["13-1"] < wave_of["13-2"]

    # 4. Scanner Chain C: 11-3 -> 11-4 -> 14-1 -> 14-3
    assert wave_of["11-3"] < wave_of["11-4"] < wave_of["14-1"] < wave_of["14-3"]

    # 5. Parallelism: 11-2 and 11-3 run in parallel in Wave 2 (isolated scopes in normalizer.py)
    assert wave_of["11-2"] == wave_of["11-3"]


def test_type_2_semantic_collision_separates_waves() -> None:
    """Verify that W(A) intersecting I(B) creates a SEMANTIC_COLLISION and separates waves."""
    task_a = TaskNode(
        id="11-3",
        title="Change _normalize_discovered_root return type to str | None",
        write_files=["src/agentlens/canonical/manifest.py"],
        impact_files=["src/agentlens/adapters/antigravity.py"],
    )
    task_b = TaskNode(
        id="12-3",
        title="Call _normalize_discovered_root in antigravity.py",
        write_files=["src/agentlens/adapters/antigravity.py"],
    )
    scheduler = BSPWaveScheduler(max_tracks=2)
    edges = scheduler.build_edges([task_a, task_b])

    assert any(e.conflict_type == ConflictType.SEMANTIC_COLLISION for e in edges)
    plan = scheduler.schedule([task_a, task_b])
    assert len(plan.waves) == 2


def test_cyclic_dependency_raises_error() -> None:
    t1 = TaskNode(id="1-1", title="A", depends_on=["1-2"])
    t2 = TaskNode(id="1-2", title="B", depends_on=["1-1"])
    scheduler = BSPWaveScheduler(max_tracks=2)
    with pytest.raises(CyclicDependencyError):
        scheduler.schedule([t1, t2])


def test_three_way_sprint_status_merge_resolves_ranks() -> None:
    base_yaml = """# Sprint Status
last_updated: "10-15-2026 12:00"
development_status:
  epic-11: in-progress
  11-2-semantic-interval: ready-for-dev
  11-3-ingestion-hygiene: ready-for-dev
  13-1-tsi-engine: backlog
"""
    ours_yaml = """# Sprint Status
last_updated: "10-15-2026 14:00"
development_status:
  epic-11: in-progress
  11-2-semantic-interval: done
  11-3-ingestion-hygiene: ready-for-dev
  13-1-tsi-engine: in-progress
"""
    theirs_yaml = """# Sprint Status
last_updated: "10-15-2026 14:05"
development_status:
  epic-11: in-progress
  11-2-semantic-interval: ready-for-dev
  11-3-ingestion-hygiene: done
  13-1-tsi-engine: backlog
  12-3-sqlite-reader: review
"""
    merged = merge_sprint_status_yaml(
        base_text=base_yaml,
        ours_text=ours_yaml,
        theirs_text=theirs_yaml,
        updated_at="10-18-2026 10:00",
    )
    assert "<<<<<<" not in merged
    assert 'last_updated: "10-18-2026 10:00"' in merged
    assert "  11-2-semantic-interval: done" in merged
    assert "  11-3-ingestion-hygiene: done" in merged
    assert "  13-1-tsi-engine: in-progress" in merged
    assert "  12-3-sqlite-reader: review" in merged


def test_ast_scanner_and_worktree_boundary_injection(tmp_path: Path) -> None:
    src_pkg = tmp_path / "src" / "demo"
    src_pkg.mkdir(parents=True)
    (src_pkg / "schema.py").write_text("SCHEMA_VERSION = 4\n", encoding="utf-8")
    (src_pkg / "cli.py").write_text(
        "import demo.schema\n@app.command()\ndef run(): pass\n", encoding="utf-8"
    )
    (src_pkg / "worker.py").write_text("import demo.schema\n", encoding="utf-8")

    scanner = ASTHotspotScanner(tmp_path)
    meta = scanner.scan()
    assert meta["src/demo/schema.py"].role == FileRole.SPINE
    assert meta["src/demo/cli.py"].role == FileRole.ADDITIVE_REGISTRY
    assert meta["src/demo/worker.py"].role == FileRole.STANDARD
    assert "src/demo/worker.py" in scanner.get_importers("src/demo/schema.py")

    # Test worktree boundary injection
    tasks = [
        TaskNode(id="1-1", title="Worker A", write_files=["src/demo/worker.py"]),
        TaskNode(id="1-2", title="Worker B", write_files=["src/demo/other.py"]),
    ]
    scheduler = BSPWaveScheduler(max_tracks=2)
    plan = scheduler.schedule(tasks)
    wave_1 = plan.waves[0]
    track_a = wave_1.tracks[0]

    wm = WorktreeManager(tmp_path)
    wt_dir = tmp_path / ".worktrees" / "wave-1-track-a"
    lock = wm.build_lock_manifest(
        wave=wave_1,
        track=track_a,
        worktree_path=wt_dir,
        all_spine_files=["src/demo/schema.py"],
    )
    wm.inject_track_boundary(wt_dir, wave_1, track_a, lock, headless=True)

    assert (wt_dir / ".tmp" / "track-context.md").is_file()
    assert (wt_dir / ".parallel-lock.json").is_file()
    assert (wt_dir / "_bmad" / "custom" / "bmad-build-auto.user.toml").is_file()
    assert (wt_dir / "_bmad" / "custom" / "bmad-build-auto.toml").is_file()
    assert (wt_dir / "_bmad" / "custom" / "bmad-build.toml").is_file()
    context_content = (wt_dir / ".tmp" / "track-context.md").read_text(encoding="utf-8")
    assert "HEADLESS WORKTREE MODE (Silent File-State Handoff)" in context_content
    assert "src/demo/other.py" in lock.forbidden_files
    assert "src/demo/schema.py" in lock.forbidden_files


def test_empty_schedule_and_cli_roundtrip(tmp_path: Path) -> None:
    import subprocess

    from sdd_parallel.cli import main

    # 1. Empty task list
    scheduler = BSPWaveScheduler(max_tracks=2)
    empty_plan = scheduler.schedule([])
    assert empty_plan.waves == []
    assert empty_plan.scheduled_task_ids == []

    # 2. Initialize real temporary git repo and verify BMADAdapter + CLI plan
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    impl_dir = tmp_path / "_bmad-output" / "implementation-artifacts"
    impl_dir.mkdir(parents=True, exist_ok=True)
    (impl_dir / "sprint-status.yaml").write_text(
        'last_updated: "10-18-2026 10:00"\n'
        "development_status:\n"
        "  11-2-interval-fix: ready-for-dev\n"
        "  12-3-sqlite-reader: ready-for-dev\n",
        encoding="utf-8",
    )
    (impl_dir / "spec-11-2-interval-fix.md").write_text(
        "# Spec 11-2\n## Code Map\n- `src/demo/intervals.py`\n",
        encoding="utf-8",
    )
    (impl_dir / "spec-12-3-sqlite-reader.md").write_text(
        "# Spec 12-3\n## Code Map\n- `src/demo/sqlite_reader.py`\n",
        encoding="utf-8",
    )

    rc = main(["plan", "--repo", str(tmp_path), "--max-tracks", "2"])
    assert rc == 0
    assert (tmp_path / "_bmad-output" / "parallel-artifacts" / "wave-plan.json").is_file()
    assert (
        tmp_path / "_bmad-output" / "parallel-artifacts" / "execution-plan-parallel.md"
    ).is_file()


def test_test_files_excluded_from_spine_and_additive_roles(tmp_path: Path) -> None:
    """Ensure test_duckdb.py and test_story_convergence_cli.py are classified as STANDARD."""
    scanner = ASTHotspotScanner(tmp_path)
    scheduler = BSPWaveScheduler(max_tracks=2)

    assert scanner.classify_file("tests/storage/test_duckdb.py") == FileRole.STANDARD
    assert scanner.classify_file("tests/cli/test_story_convergence_cli.py") == FileRole.STANDARD
    assert scheduler.get_file_role("tests/storage/test_duckdb.py") == FileRole.STANDARD
    assert scheduler.get_file_role("tests/cli/test_story_convergence_cli.py") == FileRole.STANDARD
    assert scheduler.get_file_role("src/agentlens/storage/schema.py") == FileRole.SPINE
    assert scheduler.get_file_role("src/agentlens/storage/duckdb.py") == FileRole.STANDARD
    assert scheduler.get_file_role("src/agentlens/cli.py") == FileRole.ADDITIVE_REGISTRY


def test_spine_order_respects_reverse_explicit_dependencies() -> None:
    """Ensure SPINE_ORDER does not create a false cycle when input list order is reversed."""
    t_later = TaskNode(
        id="12-1",
        title="Token accounting",
        depends_on=["11-4"],
        write_files=["src/agentlens/canonical/models.py"],
    )
    t_earlier = TaskNode(
        id="11-4",
        title="Parent correlation",
        write_files=["src/agentlens/canonical/models.py"],
    )
    scheduler = BSPWaveScheduler(max_tracks=2)
    plan = scheduler.schedule([t_later, t_earlier])
    assert [w.all_task_ids for w in plan.waves] == [["11-4"], ["12-1"]]
    assert "11-4" in scheduler.render_markdown_plan(plan)


def test_crg_sidecar_adapter_graphnode_and_worktree_normalization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify CRGSidecarAdapter calls get_impact_radius_sql and filters GraphNodes."""
    import sys
    import types
    from dataclasses import dataclass

    from sdd_parallel.crg_sidecar import CRGSidecarAdapter

    crg_dir = tmp_path / ".code-review-graph"
    crg_dir.mkdir(parents=True)
    (crg_dir / "graph.db").write_text("dummy", encoding="utf-8")

    @dataclass
    class FakeGraphNode:
        qualified_name: str
        file_path: str

    captured_kwargs: dict[str, object] = {}

    class FakeGraphStore:
        def __init__(self, db_path: Path) -> None:
            self.db_path = db_path

        def get_impact_radius_sql(
            self,
            changed_files: list[str],
            max_depth: int = 2,
            max_nodes: int = 500,
            resolution: str = "all",
        ) -> dict[str, object]:
            captured_kwargs["changed_files"] = changed_files
            captured_kwargs["max_depth"] = max_depth
            captured_kwargs["resolution"] = resolution
            return {
                "impacted_nodes": [
                    FakeGraphNode(
                        qualified_name="q_high",
                        file_path=str(tmp_path / "src/demo/high_impact.py"),
                    ),
                    FakeGraphNode(
                        qualified_name="q_low",
                        file_path=str(tmp_path / "src/demo/low_noise.py"),
                    ),
                ],
                "impact_scores": {
                    "q_high": 0.85,
                    "q_low": 0.25,
                },
            }

        def close(self) -> None:
            pass

    fake_pkg = types.ModuleType("code_review_graph")
    fake_graph_mod = types.ModuleType("code_review_graph.graph")
    fake_graph_mod.GraphStore = FakeGraphStore  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "code_review_graph", fake_pkg)
    monkeypatch.setitem(sys.modules, "code_review_graph.graph", fake_graph_mod)

    sidecar = CRGSidecarAdapter(tmp_path)
    wt_input = f"{tmp_path.as_posix()}/.worktrees/wave-1-track-a/src/demo/manifest.py"
    assert sidecar.normalize_to_repo_relative(wt_input) == "src/demo/manifest.py"

    impact = sidecar.compute_impact_files([wt_input], min_impact_score=0.5)
    assert impact == {"src/demo/high_impact.py"}
    assert captured_kwargs["resolution"] == "direct"


def test_bmad_adapter_parses_epics_md_and_filters_negative_codemap(
    tmp_path: Path,
) -> None:
    """Verify BMADAdapter extracts footprints/deps and ignores negative Code Map lines."""
    from sdd_parallel.adapters.bmad import BMADAdapter

    (tmp_path / "src" / "demo").mkdir(parents=True)
    impl_dir = tmp_path / "_bmad-output" / "implementation-artifacts"
    plan_dir = tmp_path / "_bmad-output" / "planning-artifacts"
    impl_dir.mkdir(parents=True)
    plan_dir.mkdir(parents=True)

    (impl_dir / "sprint-status.yaml").write_text(
        'last_updated: "10-18-2026 10:00"\n'
        "development_status:\n"
        "  11-2-interval-fix: ready-for-dev\n"
        "  13-1-tsi-engine: backlog\n",
        encoding="utf-8",
    )
    (impl_dir / "spec-11-2-interval-fix.md").write_text(
        "# Spec 11-2\n"
        "## Code Map\n"
        "- `src/demo/intervals.py` — tracker update\n"
        "- Без изменений: `src/demo/models.py`, `src/demo/schema.py`\n",
        encoding="utf-8",
    )
    (plan_dir / "epics.md").write_text(
        "### Story 13.1: Deterministic TSI Engine (`EPIC-TSI`)\n"
        "Depends on Story 11.2 and modifies `analytics/tsi.py`.\n",
        encoding="utf-8",
    )

    adapter = BMADAdapter(tmp_path)
    tasks = adapter.load_tasks()
    t_map = {t.id: t for t in tasks}

    assert t_map["11-2"].write_files == ["src/demo/intervals.py"]
    assert "src/demo/models.py" not in t_map["11-2"].write_files
    assert t_map["13-1"].title == "Deterministic TSI Engine"
    assert t_map["13-1"].depends_on == ["11-2"]
    assert "src/demo/analytics/tsi.py" in t_map["13-1"].write_files


def test_e2e_worktree_prepare_verify_and_merge(tmp_path: Path) -> None:
    """Full E2E test of prepare_wave, clean git status in worktrees, verify_wave, and merge_wave."""
    import subprocess

    def _git(*args: str, cwd: Path = tmp_path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )

    _git("init")
    _git("config", "user.email", "test@example.com")
    _git("config", "user.name", "Test Runner")

    impl_dir = tmp_path / "_bmad-output" / "implementation-artifacts"
    impl_dir.mkdir(parents=True)
    status_file = impl_dir / "sprint-status.yaml"
    status_file.write_text(
        'last_updated: "10-18-2026 09:00"\n'
        "development_status:\n"
        "  1-1-alpha: ready-for-dev\n"
        "  1-2-beta: ready-for-dev\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "demo").mkdir(parents=True)
    (tmp_path / "src" / "demo" / "alpha.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "src" / "demo" / "beta.py").write_text("y = 1\n", encoding="utf-8")
    _git("add", ".")
    _git("commit", "-m", "initial commit")

    tasks = [
        TaskNode(id="1-1", title="Alpha", write_files=["src/demo/alpha.py"]),
        TaskNode(id="1-2", title="Beta", write_files=["src/demo/beta.py"]),
    ]
    scheduler = BSPWaveScheduler(max_tracks=2)
    plan = scheduler.schedule(tasks)

    wm = WorktreeManager(tmp_path)
    wt_paths = wm.prepare_wave(plan=plan, wave_number=1, headless=True)
    assert len(wt_paths) == 2

    # Verify 100% clean git status inside both provisioned worktrees
    for wt in wt_paths:
        st = _git("status", "--porcelain", cwd=wt)
        assert st.stdout.strip() == ""
        assert (wt / ".tmp" / "track-context.md").is_file()
        assert (wt / ".parallel-lock.json").is_file()

    # Simulate Track A and Track B completing their stories & updating sprint-status.yaml
    wt_a, wt_b = wt_paths[0], wt_paths[1]
    (wt_a / "src" / "demo" / "alpha.py").write_text("x = 2\n", encoding="utf-8")
    (wt_a / "_bmad-output" / "implementation-artifacts" / "sprint-status.yaml").write_text(
        'last_updated: "10-18-2026 10:00"\n'
        "development_status:\n"
        "  1-1-alpha: done\n"
        "  1-2-beta: ready-for-dev\n",
        encoding="utf-8",
    )
    _git(
        "add",
        "src/demo/alpha.py",
        "_bmad-output/implementation-artifacts/sprint-status.yaml",
        cwd=wt_a,
    )
    _git("commit", "-m", "feat(1-1): complete alpha", cwd=wt_a)

    (wt_b / "src" / "demo" / "beta.py").write_text("y = 2\n", encoding="utf-8")
    (wt_b / "_bmad-output" / "implementation-artifacts" / "sprint-status.yaml").write_text(
        'last_updated: "10-18-2026 10:05"\n'
        "development_status:\n"
        "  1-1-alpha: ready-for-dev\n"
        "  1-2-beta: done\n",
        encoding="utf-8",
    )
    _git(
        "add",
        "src/demo/beta.py",
        "_bmad-output/implementation-artifacts/sprint-status.yaml",
        cwd=wt_b,
    )
    _git("commit", "-m", "feat(1-2): complete beta", cwd=wt_b)

    assert wm.verify_wave(plan, wave_number=1) == []
    merged = wm.merge_wave(
        plan=plan,
        wave_number=1,
        run_quality_gates=False,
        cleanup_worktrees=True,
    )
    assert len(merged) == 2
    final_status = status_file.read_text(encoding="utf-8")
    assert "  1-1-alpha: done" in final_status
    assert "  1-2-beta: done" in final_status
    assert (tmp_path / "src" / "demo" / "alpha.py").read_text(encoding="utf-8") == "x = 2\n"
    assert (tmp_path / "src" / "demo" / "beta.py").read_text(encoding="utf-8") == "y = 2\n"


def test_bmad_adapter_extracts_multiline_and_inline_function_scopes(tmp_path: Path) -> None:
    """Verify BMADAdapter captures both inline notes and indented bullet scopes."""
    from sdd_parallel.adapters.bmad import BMADAdapter

    impl_dir = tmp_path / "_bmad-output" / "implementation-artifacts"
    impl_dir.mkdir(parents=True)

    spec_inline = (
        "# Spec 11-2\n"
        "## Code Map\n"
        "- `src/demo/normalizer.py` — _classify_read_churn post-pass (L246-284)\n"
    )
    (impl_dir / "spec-11-2-interval.md").write_text(spec_inline, encoding="utf-8")

    spec_multiline = (
        "# Spec 11-3\n"
        "## Code Map\n"
        "- `src/demo/normalizer.py` --\n"
        "  - is_blacklisted_system_dir filter for system roots\n"
    )
    (impl_dir / "spec-11-3-blacklist.md").write_text(spec_multiline, encoding="utf-8")

    (impl_dir / "sprint-status.yaml").write_text(
        'last_updated: "10-18-2026 10:00"\n'
        "development_status:\n"
        "  11-2-interval: ready-for-dev\n"
        "  11-3-blacklist: ready-for-dev\n",
        encoding="utf-8",
    )

    adapter = BMADAdapter(tmp_path)
    tasks = adapter.load_tasks()
    t_map = {t.id: t for t in tasks}

    note_11_2 = t_map["11-2"].shared_function_notes["src/demo/normalizer.py"]
    note_11_3 = t_map["11-3"].shared_function_notes["src/demo/normalizer.py"]
    assert "_classify_read_churn" in note_11_2
    assert "is_blacklisted_system_dir" in note_11_3

    scheduler = BSPWaveScheduler(max_tracks=2)
    plan = scheduler.schedule(tasks)
    # Both tasks have isolated function scopes on normalizer.py, so they can run in parallel
    assert len(plan.waves) == 1
    assert set(plan.waves[0].all_task_ids) == {"11-2", "11-3"}


def test_universal_heuristics_roles_and_direct_collision() -> None:
    """Verify standard default role for egress/storage modules and collision enforcement."""
    scheduler = BSPWaveScheduler(max_tracks=2)
    assert scheduler.get_file_role("src/agentlens/egress/console.py") == FileRole.STANDARD
    assert scheduler.get_file_role("src/agentlens/storage/duckdb.py") == FileRole.STANDARD

    # Two tasks writing to console.py without isolated function scope create DIRECT_COLLISION
    t1 = TaskNode(
        id="1-1",
        title="Format report",
        write_files=["src/demo/worker_a.py", "src/demo/egress/console.py"],
    )
    t2 = TaskNode(
        id="1-2",
        title="Format summary",
        write_files=["src/demo/worker_b.py", "src/demo/egress/console.py"],
    )
    edges = scheduler.build_edges([t1, t2])
    collision_edges = [e for e in edges if e.conflict_type == ConflictType.DIRECT_COLLISION]
    assert len(collision_edges) == 1
    assert collision_edges[0].shared_files == ("src/demo/egress/console.py",)

    # Scheduler splits them into 2 separate sequential waves
    plan = scheduler.schedule([t1, t2])
    assert len(plan.waves) == 2
    assert plan.waves[0].all_task_ids == ["1-1"]
    assert plan.waves[1].all_task_ids == ["1-2"]


def test_ast_scanner_universal_role_detection(tmp_path: Path) -> None:
    """Verify ASTHotspotScanner classifies roles via AST properties and fan-in >= 5."""
    src = tmp_path / "src" / "demo"
    src.mkdir(parents=True)

    # 1. Custom module with SCHEMA_VERSION -> SPINE without matching filename
    (src / "custom_store.py").write_text(
        'SCHEMA_VERSION = "2.0"\nclass Store: pass\n',
        encoding="utf-8",
    )

    # 2. Module with @app.command() -> ADDITIVE_REGISTRY
    (src / "handlers.py").write_text(
        "class App:\n"
        "    def command(self): return lambda f: f\n"
        "app = App()\n"
        "@app.command()\n"
        "def run_job(): pass\n",
        encoding="utf-8",
    )

    # 3. High fan-in module (imported by 5 files) -> SPINE
    (src / "shared_util.py").write_text("def helper(): pass\n", encoding="utf-8")
    for i in range(5):
        (src / f"client_{i}.py").write_text(
            "from demo.shared_util import helper\n",
            encoding="utf-8",
        )

    # 4. Standard module without decorators/version -> STANDARD
    (src / "console.py").write_text("def print_line(): pass\n", encoding="utf-8")

    scanner = ASTHotspotScanner(tmp_path)
    meta = scanner.scan()

    assert meta["src/demo/custom_store.py"].role == FileRole.SPINE
    assert meta["src/demo/handlers.py"].role == FileRole.ADDITIVE_REGISTRY
    assert meta["src/demo/shared_util.py"].role == FileRole.SPINE
    assert len(meta["src/demo/shared_util.py"].imported_by) == 5
    assert meta["src/demo/console.py"].role == FileRole.STANDARD


def test_bmad_adapter_plan_source_selection(tmp_path: Path) -> None:
    """Verify BMADAdapter respects plan_source='plan' vs plan_source='specs'."""
    from sdd_parallel.adapters.bmad import BMADAdapter

    impl_dir = tmp_path / "_bmad-output" / "implementation-artifacts"
    plan_dir = tmp_path / "_bmad-output" / "planning-artifacts"
    impl_dir.mkdir(parents=True)
    plan_dir.mkdir(parents=True)

    (impl_dir / "sprint-status.yaml").write_text(
        'last_updated: "10-18-2026 10:00"\ndevelopment_status:\n  12-3-sqlite: ready-for-dev\n',
        encoding="utf-8",
    )
    # Spec has drifted file scope
    (impl_dir / "spec-12-3-sqlite.md").write_text(
        "# Spec 12-3\n## Code Map\n- `src/demo/sqlite.py`\n- `src/demo/models.py`\n",
        encoding="utf-8",
    )
    # Planning doc has clean roadmap scope
    (plan_dir / "execution-plan-roadmap.md").write_text(
        "| Story | Brief | Modules |\n"
        "|---|---|---|\n"
        "| 12-3 SQLite reader | read-only | sqlite.py, cli.py |\n",
        encoding="utf-8",
    )

    adapter = BMADAdapter(tmp_path)
    tasks_plan = adapter.load_tasks(plan_source="plan")
    tasks_specs = adapter.load_tasks(plan_source="specs")

    assert "src/demo/models.py" not in tasks_plan[0].write_files
    assert "src/demo/models.py" in tasks_specs[0].write_files


def test_external_benchmark_repo_plan_dry_run_if_present() -> None:
    """Verify sdd-parallel plan against external benchmark repo if present on this host."""
    from sdd_parallel.cli import _compute_repo_plan

    env_repo = os.environ.get("SDD_PARALLEL_BENCHMARK_REPO")
    if not env_repo:
        pytest.skip("Set SDD_PARALLEL_BENCHMARK_REPO to run external brownfield benchmark test")
    benchmark_path = Path(env_repo)
    if not (benchmark_path / ".git").is_dir():
        pytest.skip(f"Benchmark repo at {env_repo} does not have a .git directory.")

    # 1. Test clean planning-artifacts roadmap mode
    sched_plan, tasks_plan, plan_roadmap = _compute_repo_plan(
        repo_root=benchmark_path,
        max_tracks=3,
        include_done=False,
        plan_source="plan",
    )
    assert len(plan_roadmap.scheduled_task_ids) == len(tasks_plan)
    assert len(plan_roadmap.waves) <= 6

    # 2. Test spec-enriched live execution mode
    sched_live, tasks_live, plan_live = _compute_repo_plan(
        repo_root=benchmark_path,
        max_tracks=3,
        include_done=False,
        plan_source="auto",
    )
    assert len(plan_live.scheduled_task_ids) == len(tasks_live)
    # Verify acyclicity and valid tracks
    for wave in plan_live.waves:
        assert len(wave.tracks) <= 3


def test_bmad_adapter_parses_sprint_change_proposal_and_resolves_recursive_module(
    tmp_path: Path,
) -> None:
    """Verify BMADAdapter scans proposals, resolves submodules, and ignores ellipsis paths."""
    from sdd_parallel.adapters.bmad import BMADAdapter

    (tmp_path / "src" / "demo" / "discovery").mkdir(parents=True)
    (tmp_path / "src" / "demo" / "canonical").mkdir(parents=True)
    (tmp_path / "src" / "demo" / "storage").mkdir(parents=True)
    (tmp_path / "src" / "demo" / "discovery" / "harness.py").write_text(
        "# harness\n", encoding="utf-8"
    )
    (tmp_path / "src" / "demo" / "canonical" / "zones.py").write_text("# zones\n", encoding="utf-8")
    (tmp_path / "src" / "demo" / "storage" / "duckdb.py").write_text("# duckdb\n", encoding="utf-8")
    (tmp_path / "src" / "demo" / "cli.py").write_text("# cli\n", encoding="utf-8")
    (tmp_path / "src" / "demo" / "console.py").write_text("# console\n", encoding="utf-8")

    impl_dir = tmp_path / "_bmad-output" / "implementation-artifacts"
    plan_dir = tmp_path / "_bmad-output" / "planning-artifacts"
    impl_dir.mkdir(parents=True)
    plan_dir.mkdir(parents=True)

    (impl_dir / "sprint-status.yaml").write_text(
        'last_updated: "10-19-2026 12:00"\n'
        "development_status:\n"
        "  15-2-path-sanitization: ready-for-dev\n"
        "  15-3-subagent-inheritance: ready-for-dev\n"
        "  16-3-guidance-targeting: ready-for-dev\n",
        encoding="utf-8",
    )

    (plan_dir / "epics.md").write_text(
        "### Story 15.2: Path Sanitization & Test Zone Classifier\n"
        "Fixes duplicate slashes in `harness.py` and zones in `canonical/zones.py`.\n\n"
        "### Story 15.3: Subagent Project Inheritance\n"
        "Inherit project context from parent orchestrator traces.\n\n"
        "### Story 16.3: Intelligent Guidance Auto-Targeting\n"
        "Target column center-truncation (`.../readiness_gate.py`), guaranteeing visibility.\n",
        encoding="utf-8",
    )

    (plan_dir / "sprint-change-proposal-2026-10-19.md").write_text(
        "## 4. Спецификация новых эпиков и историй\n\n"
        "- **Story 15.2: Path Sanitization & Test Zone Classifier (`BUG-01`)**\n"
        "  - **Файлы:** `src/demo/discovery/harness.py`, `src/demo/canonical/zones.py`.\n\n"
        "- **Story 15.3: Subagent Project Inheritance & Delta Sync (`DATA-01`)**\n"
        "  - **Файлы:** `src/demo/discovery/harness.py`, `src/demo/storage/duckdb.py`, "
        "`src/demo/cli.py`.\n\n"
        "- **Story 16.3: Intelligent Guidance Auto-Targeting (`UX-15`)**\n"
        "  - **Описание:** Выводить обрезку посередине (`.../readiness_gate.py`).\n"
        "  - **Файлы:** `src/demo/cli.py`, `src/demo/console.py`.\n",
        encoding="utf-8",
    )

    adapter = BMADAdapter(tmp_path)
    tasks = adapter.load_tasks()
    t_map = {t.id: t for t in tasks}

    assert "src/demo/discovery/harness.py" in t_map["15-2"].write_files
    assert "src/demo/harness.py" not in t_map["15-2"].write_files
    assert "src/demo/canonical/zones.py" in t_map["15-2"].write_files

    assert t_map["15-3"].write_files == [
        "src/demo/discovery/harness.py",
        "src/demo/storage/duckdb.py",
        "src/demo/cli.py",
    ]

    assert "src/demo/readiness_gate.py" not in t_map["16-3"].write_files
    assert t_map["16-3"].write_files == [
        "src/demo/cli.py",
        "src/demo/console.py",
    ]


def test_ast_scanner_fallback_without_src_dir(tmp_path: Path) -> None:
    """Verify ASTHotspotScanner falls back to repo_root when src/ is absent."""
    app_dir = tmp_path / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "schema.py").write_text("SCHEMA_VERSION = 1\n", encoding="utf-8")
    (app_dir / "service.py").write_text(
        "from app.schema import SCHEMA_VERSION\n\ndef run():\n    return SCHEMA_VERSION\n",
        encoding="utf-8",
    )

    venv_dir = tmp_path / ".venv" / "lib"
    venv_dir.mkdir(parents=True)
    (venv_dir / "site.py").write_text("dummy = True\n", encoding="utf-8")

    scanner = ASTHotspotScanner(tmp_path)
    files = scanner.scan()

    assert "app/schema.py" in files
    assert files["app/schema.py"].role == FileRole.SPINE
    assert "app/service.py" in scanner.get_importers("app/schema.py")
    assert ".venv/lib/site.py" not in files


def test_bmad_adapter_loads_sprint_status_with_utf8_bom(tmp_path: Path) -> None:
    """Verify BMADAdapter and status_merger handle UTF-8 BOM prefix seamlessly."""
    from sdd_parallel.adapters.bmad import BMADAdapter
    from sdd_parallel.status_merger import merge_sprint_status_yaml

    impl_dir = tmp_path / "_bmad-output" / "implementation-artifacts"
    plan_dir = tmp_path / "_bmad-output" / "planning-artifacts"
    impl_dir.mkdir(parents=True)
    plan_dir.mkdir(parents=True)

    # Prepend \ufeff UTF-8 BOM
    status_content = (
        "\ufeff"
        'last_updated: "10-25-2026 14:00"\n'
        "development_status:\n"
        "  1-1-first: ready-for-dev\n"
        "  1-2-second: ready-for-dev\n"
    )
    (impl_dir / "sprint-status.yaml").write_text(status_content, encoding="utf-8")

    epics_content = (
        "\ufeff"
        "## Epic 1: Foundation\n\n"
        "### Story 1.1: First Story\n"
        "Files: `src/first.py`\n\n"
        "### Story 1.2: Second Story\n"
        "Files: `src/second.py`\n"
    )
    (plan_dir / "epics.md").write_text(epics_content, encoding="utf-8")

    adapter = BMADAdapter(tmp_path)
    tasks = adapter.load_tasks()
    assert len(tasks) == 2
    task_ids = {t.id for t in tasks}
    assert task_ids == {"1-1", "1-2"}

    # Also verify merge_sprint_status_yaml handles BOM strings
    merged = merge_sprint_status_yaml(
        base_text="\ufeffdevelopment_status:\n  1-1: ready-for-dev\n",
        ours_text="\ufeffdevelopment_status:\n  1-1: in-progress\n",
        theirs_text="\ufeffdevelopment_status:\n  1-1: ready-for-dev\n  1-2: ready-for-dev\n",
    )
    assert "1-1: in-progress" in merged
    assert "1-2: ready-for-dev" in merged
