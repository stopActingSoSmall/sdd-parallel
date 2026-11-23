"""Unit tests for Task 43: spine buckets, namespace collisions, rescheduler, and BarrierFeedback."""

from __future__ import annotations

from pathlib import Path

from sdd_parallel.ast_scanner import ASTHotspotScanner
from sdd_parallel.manifest import extract_prevented_conflicts
from sdd_parallel.models import BarrierFeedback, ConflictType, TaskNode
from sdd_parallel.scheduler import BSPWaveScheduler


def test_spine_bucket_serializes_distinct_migration_files() -> None:
    """Two tasks creating distinct files in one sequential spine bucket get SPINE_ORDER."""
    t1 = TaskNode(
        id="T1",
        title="Users migration",
        write_files=["db/migrations/005_users.sql"],
    )
    t2 = TaskNode(
        id="T2",
        title="Costs migration",
        write_files=["db/migrations/005_costs.sql"],
    )

    scheduler = BSPWaveScheduler(max_tracks=3)
    plan = scheduler.schedule([t1, t2])

    assert len(plan.waves) == 2
    assert plan.waves[0].all_task_ids == ["T1"]
    assert plan.waves[1].all_task_ids == ["T2"]
    assert any(
        e.conflict_type == ConflictType.SPINE_ORDER
        and e.directed is True
        and "db/migrations" in e.shared_files
        for e in plan.edges
    )


def test_namespace_collision_separates_tasks_with_same_cli_flag_or_env(tmp_path: Path) -> None:
    """Tasks touching disjoint files but sharing CLI/ENV tokens get NAMESPACE_COLLISION."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("A = 1\n", encoding="utf-8")
    (tmp_path / "src" / "b.py").write_text("B = 1\n", encoding="utf-8")

    t1 = TaskNode(
        id="T1",
        title="CLI strict sync A",
        description="Add --strict-sync flag controlled by SYNC_MODE.",
        write_files=["src/a.py"],
    )
    t2 = TaskNode(
        id="T2",
        title="CLI strict sync B",
        description="Expose --strict-sync and read SYNC_MODE in worker.",
        write_files=["src/b.py"],
    )

    scanner = ASTHotspotScanner(tmp_path)
    scheduler = BSPWaveScheduler(max_tracks=3, ast_scanner=scanner)
    plan = scheduler.schedule([t1, t2])

    assert len(plan.waves) == 2
    assert any(
        e.conflict_type == ConflictType.NAMESPACE_COLLISION
        and not e.directed
        and "ns:CLI:--strict-sync" in e.shared_files
        and "ns:ENV:SYNC_MODE" in e.shared_files
        for e in plan.edges
    )
    prevented = extract_prevented_conflicts(plan)
    assert any(p.conflict_type == ConflictType.NAMESPACE_COLLISION.value for p in prevented)


def test_reschedule_remaining_waves_respects_conflicts_in_wave_k_plus_1() -> None:
    """Deferred task with enriched writes conflicts with Wave K+1 and shifts into Wave K+2."""
    t0 = TaskNode(id="T0", title="Base core", write_files=["src/core.py"])
    t1 = TaskNode(id="T1", title="Track B feature", write_files=["src/a.py"])
    t2 = TaskNode(
        id="T2",
        title="Dependent feature",
        write_files=["src/b.py"],
        depends_on=["T0"],
    )

    scheduler = BSPWaveScheduler(max_tracks=3)
    initial_plan = scheduler.schedule([t0, t1, t2])

    assert initial_plan.waves[0].all_task_ids == ["T0", "T1"]
    assert initial_plan.waves[1].all_task_ids == ["T2"]

    replanned = scheduler.reschedule_remaining_waves(
        initial_plan,
        current_wave=1,
        deferred_task_ids=["T1"],
        enriched_writes={"T1": ["src/b.py"]},
    )

    assert len(replanned.waves) == 3
    assert replanned.waves[0].all_task_ids == ["T0"]
    assert len(replanned.waves[0].tracks) == 2
    assert replanned.waves[0].tracks[1].task_ids == []
    assert replanned.waves[0].tracks[1].exclusive_write_files == []
    assert replanned.waves[1].all_task_ids == ["T1"]
    assert replanned.waves[2].all_task_ids == ["T2"]
    assert t2.depends_on == ["T0"]
    assert set(replanned.scheduled_task_ids) == {"T0", "T1", "T2"}


def test_barrier_feedback_roundtrip() -> None:
    """Verify BarrierFeedback serialization and deserialization roundtrip."""
    feedback = BarrierFeedback(
        story_ids=("1-1", "1-2"),
        wave_number=2,
        track_id="track-b",
        failed_stage="ast_diff_gate",
        failed_command="uv run pytest",
        diagnostic_output="Cycle detected between a and b",
        conflicting_files=("src/a.py", "src/b.py"),
        merged_sibling_commits=("abc1234",),
        prior_branch_ref="parallel/wave-2-track-b",
        timestamp="2026-11-23T15:00:00Z",
    )

    payload = feedback.to_dict()
    restored = BarrierFeedback.from_dict(payload)

    assert restored == feedback
    assert isinstance(payload["story_ids"], list)
    assert isinstance(restored.story_ids, tuple)
