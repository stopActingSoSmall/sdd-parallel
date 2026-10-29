"""Unit tests for Multi-SDD adapters (Spec Kit, GSD Artifact Hijack, OpenSpec, Beads)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from sdd_parallel.adapters import (
    BeadsAdapter,
    GSDHijackAdapter,
    OpenSpecAdapter,
    SpecKitAdapter,
    detect_sdd_kits,
    diagnose_multi_kit_coexistence,
    get_adapter,
)
from sdd_parallel.ast_scanner import ASTHotspotScanner
from sdd_parallel.crg_sidecar import CRGSidecarAdapter
from sdd_parallel.demo import create_demo_repository
from sdd_parallel.hooks.blast_radius_guard import evaluate_tool_access
from sdd_parallel.models import EffortTier, FileRole, TaskStatus
from sdd_parallel.scheduler import BSPWaveScheduler
from sdd_parallel.worktree import WorktreeManager, WorktreeRollbackManager


def test_get_adapter_instantiates_all_supported_kits(tmp_path: Path) -> None:
    kit, adapter = get_adapter(tmp_path, kit="bmad")
    assert kit == "bmad" and adapter.__class__.__name__ == "BMADAdapter"

    kit, adapter = get_adapter(tmp_path, kit="spec-kit")
    assert kit == "spec-kit" and isinstance(adapter, SpecKitAdapter)

    kit, adapter = get_adapter(tmp_path, kit="gsd")
    assert kit == "gsd" and isinstance(adapter, GSDHijackAdapter)

    kit, adapter = get_adapter(tmp_path, kit="openspec")
    assert kit == "openspec" and isinstance(adapter, OpenSpecAdapter)

    kit, adapter = get_adapter(tmp_path, kit="beads")
    assert kit == "beads" and isinstance(adapter, BeadsAdapter)


def test_detect_sdd_kits_discovers_all_five_ecosystems(tmp_path: Path) -> None:
    (tmp_path / "_bmad").mkdir()
    (tmp_path / ".specify" / "specs").mkdir(parents=True)
    (tmp_path / ".planning" / "phases").mkdir(parents=True)
    (tmp_path / "openspec" / "changes").mkdir(parents=True)
    beads_dir = tmp_path / ".beads"
    beads_dir.mkdir()
    (beads_dir / "issues.jsonl").write_text('{"id": "bd-1", "title": "Init"}\n', encoding="utf-8")

    kits = detect_sdd_kits(tmp_path)
    assert kits == ["bmad", "spec-kit", "gsd", "openspec", "beads"]


def test_spec_kit_adapter_parses_tasks_and_scheduler_overrides_unsafe_parallel_marker(
    tmp_path: Path,
) -> None:
    feat_dir = tmp_path / ".specify" / "specs" / "001-telemetry"
    feat_dir.mkdir(parents=True)
    (feat_dir / "tasks.md").write_text(
        "# Tasks\n"
        "- [x] T001 Setup base directory\n"
        "- [ ] T002 [P] [US1] Add user schema in `src/app/models.py`\n"
        "- [ ] T003 [P] [US2] Add billing schema in `src/app/models.py`\n"
        "- [ ] T004 [P] [US3] Add independent exporter in `src/app/exporter.py`\n",
        encoding="utf-8",
    )

    adapter = SpecKitAdapter(tmp_path)
    assert adapter.detect() is True
    tasks = adapter.load_tasks(include_done=False)
    assert [t.id for t in tasks] == ["T002", "T003", "T004"]

    # Even though T002 and T003 both have [P], they touch Spine file src/app/models.py
    scheduler = BSPWaveScheduler(max_tracks=3)
    plan = scheduler.schedule(tasks, sdd_kit="spec-kit")
    wave_of = {tid: w.wave_number for w in plan.waves for tid in w.all_task_ids}
    assert wave_of["T002"] < wave_of["T003"]
    assert wave_of["T004"] == wave_of["T002"]


def test_spec_kit_realistic_repository_with_sub_bullets_sections_and_diagnostics(
    tmp_path: Path,
) -> None:
    specify_dir = tmp_path / ".specify"
    mem_dir = specify_dir / "memory"
    mem_dir.mkdir(parents=True)
    (mem_dir / "constitution.md").write_text(
        "# Constitution\n"
        "1. Zero Worktree Pollution: git status must be clean.\n"
        "2. No direct writes to shared schema without Spine Lock.\n",
        encoding="utf-8",
    )

    feat_dir = specify_dir / "specs" / "002-auth"
    feat_dir.mkdir(parents=True)
    (feat_dir / "plan.md").write_text(
        "# Plan: Authentication\n"
        "Target files include `src/auth/token.ts` and `src/auth/service.py`.\n",
        encoding="utf-8",
    )
    (feat_dir / "tasks.md").write_text(
        "# Tasks for Authentication\n\n"
        "## User Story 1 - Local Auth\n"
        "- [ ] T001 Create user schema in `src/db/schema.py`\n"
        "- [ ] T002 [P] Implement password hasher in `src/utils/crypto.py` (depends on T001)\n"
        "- [ ] T003 Implement JWT token generator\n"
        "  - Files: src/auth/token.ts, src/auth/types.ts\n"
        "  - Depends on: T002\n\n"
        "## User Story 2 - OAuth SSO\n"
        "- [ ] T004 [P] OAuth client implementation in `src/auth/oauth.py` [Effort: high]\n"
        "- [ ] T005 [P] Add oauth token storage in `src/db/schema.py` (depends on T004)\n",
        encoding="utf-8",
    )

    adapter = SpecKitAdapter(tmp_path)
    assert adapter.detect() is True

    invariants = adapter.get_constitution_invariants()
    assert len(invariants) == 2
    assert "Zero Worktree Pollution" in invariants[0]

    tasks = adapter.load_tasks(include_done=False)
    assert len(tasks) == 5

    t3 = next(t for t in tasks if t.id == "T003")
    assert "src/auth/token.ts" in t3.write_files
    assert "src/auth/types.ts" in t3.write_files
    assert "T002" in t3.depends_on

    t4 = next(t for t in tasks if t.id == "T004")
    assert t4.effort == EffortTier.HIGH
    assert t4.write_files == ["src/auth/oauth.py"]

    # Verify diagnostic report for unsafe parallel markers
    diagnostics = adapter.verify_parallel_markers(tasks)
    # T001 and T005 both write to src/db/schema.py
    assert any(
        d["shared_files"] == ["src/db/schema.py"] and d["conflict_type"] == "DIRECT_COLLISION"
        for d in diagnostics
    )

    # BSPWaveScheduler separates them safely into different waves
    scheduler = BSPWaveScheduler(max_tracks=3)
    plan = scheduler.schedule(tasks, sdd_kit="spec-kit")
    wave_of = {tid: w.wave_number for w in plan.waves for tid in w.all_task_ids}
    assert wave_of["T001"] < wave_of["T005"]


def test_gsd_hijack_adapter_schedules_cross_phase_and_locks_state_md(
    tmp_path: Path,
) -> None:
    p1 = tmp_path / ".planning" / "phases" / "01-core"
    p2 = tmp_path / ".planning" / "phases" / "02-analytics"
    p1.mkdir(parents=True)
    p2.mkdir(parents=True)
    (tmp_path / ".planning" / "STATE.md").write_text("# State\n", encoding="utf-8")

    (p1 / "01-01-PLAN.md").write_text(
        "---\n"
        "wave: 1\n"
        "depends_on: []\n"
        'files_modified: ["src/app/parser.py"]\n'
        "---\n"
        "# Phase 1 Parser\n",
        encoding="utf-8",
    )
    (p2 / "02-01-PLAN.md").write_text(
        "---\n"
        "wave: 1\n"
        "depends_on: []\n"
        'files_modified: ["src/app/metrics.py"]\n'
        "---\n"
        "# Phase 2 Metrics\n",
        encoding="utf-8",
    )

    adapter = GSDHijackAdapter(tmp_path)
    assert adapter.detect() is True
    tasks = adapter.load_tasks()
    assert len(tasks) == 2

    scheduler = BSPWaveScheduler(max_tracks=2)
    plan = scheduler.schedule(tasks, sdd_kit="gsd")
    # Cross-phase tasks with disjoint files are packed into Wave 1 together
    assert len(plan.waves) == 1
    assert set(plan.waves[0].all_task_ids) == {"01-01", "02-01"}

    wm = WorktreeManager(tmp_path)
    wt_dir = tmp_path / ".worktrees" / "wave-1-track-a"
    lock = wm.claim_track(
        plan=plan,
        wave_number=1,
        track_id="track-a",
        worktree_path=wt_dir,
    )
    assert ".planning/STATE.md" in lock.forbidden_files
    assert ".planning/ROADMAP.md" in lock.forbidden_files


def test_gsd_realistic_repository_multi_phase_and_central_state_merge(
    tmp_path: Path,
) -> None:
    planning_dir = tmp_path / ".planning"
    phases_dir = planning_dir / "phases"
    p1 = phases_dir / "01-storage"
    p2 = phases_dir / "02-api"
    p3 = phases_dir / "03-ui"
    p1.mkdir(parents=True)
    p2.mkdir(parents=True)
    p3.mkdir(parents=True)

    (planning_dir / "ROADMAP.md").write_text(
        "# Project Roadmap\n\n"
        "## Phase 1: Storage Layer\n"
        "- 01-01: SQLite storage engine\n"
        "- 01-02: Storage migrations\n\n"
        "## Phase 2: REST API (depends on Phase 1)\n"
        "- 02-01: FastAPI router\n\n"
        "## Phase 3: Web Dashboard\n"
        "- 03-01: Dashboard stats component\n",
        encoding="utf-8",
    )
    (planning_dir / "STATE.md").write_text(
        "# Project State\n"
        "- [x] 01-01 SQLite engine\n"
        "- [ ] 01-02 Storage migrations\n"
        "- [ ] 02-01 FastAPI router\n"
        "- [ ] 03-01 Dashboard stats\n",
        encoding="utf-8",
    )

    # 01-01 is done (has SUMMARY.md and is [x] in STATE.md)
    (p1 / "01-01-PLAN.md").write_text(
        "---\nphase: 01\nfiles_modified: [src/db/sqlite.py]\n---\n# SQLite Engine\n",
        encoding="utf-8",
    )
    (p1 / "01-01-SUMMARY.md").write_text("# Summary\nDone.\n", encoding="utf-8")

    # 01-02 ready for dev, block list yaml
    (p1 / "01-02-PLAN.md").write_text(
        "---\n"
        "phase: 01\n"
        "depends_on:\n"
        '  - "01-01-PLAN.md"\n'
        "files_modified:\n"
        '  - "src/db/migrations.py"\n'
        '  - "src/db/schema.py"\n'
        "effort: high\n"
        "---\n"
        "# Storage Migrations\n",
        encoding="utf-8",
    )

    # 02-01 depends on 01-02
    (p2 / "02-01-PLAN.md").write_text(
        "---\n"
        "phase: 02\n"
        'depends_on: ["01-02"]\n'
        'files_modified: ["src/api/routes.py"]\n'
        "---\n"
        "# FastAPI Router\n",
        encoding="utf-8",
    )

    # 03-01 independent from phase 1 & 2!
    (p3 / "03-01-PLAN.md").write_text(
        "---\n"
        "phase: 03\n"
        "depends_on: []\n"
        'files_modified: ["src/ui/stats.tsx"]\n'
        "---\n"
        "# Dashboard Stats\n",
        encoding="utf-8",
    )

    adapter = GSDHijackAdapter(tmp_path)
    assert adapter.detect() is True

    # 01-01 filtered out because it is done
    active_tasks = adapter.load_tasks(include_done=False)
    assert [t.id for t in active_tasks] == ["01-02", "02-01", "03-01"]
    assert active_tasks[0].effort == EffortTier.HIGH
    assert active_tasks[0].depends_on == ["01-01"]

    # Cross-phase scheduling: 01-02 and 03-01 have disjoint files, so they run together in Wave 1!
    scheduler = BSPWaveScheduler(max_tracks=2)
    plan = scheduler.schedule(active_tasks, sdd_kit="gsd")
    wave1_ids = plan.waves[0].all_task_ids
    assert set(wave1_ids) == {"01-02", "03-01"}
    assert plan.waves[1].all_task_ids == ["02-01"]

    # Test centralized STATE.md updating on merge
    updated_state = adapter.update_state_on_merge(wave_number=1, merged_task_ids=["01-02", "03-01"])
    assert updated_state is not None and updated_state.is_file()
    state_content = updated_state.read_text(encoding="utf-8")
    assert "- [x] 01-02 Storage migrations" in state_content
    assert "Wave 1 Merge Checkpoint" in state_content


def test_openspec_and_beads_adapters(tmp_path: Path) -> None:
    # 1. OpenSpec
    c1 = tmp_path / "openspec" / "changes" / "add-oauth"
    c2 = tmp_path / "openspec" / "changes" / "archive"
    c1.mkdir(parents=True)
    c2.mkdir(parents=True)
    (c1 / "tasks.md").write_text(
        "- [ ] Implement OAuth client in `src/auth/oauth.py`\n",
        encoding="utf-8",
    )
    os_adapter = OpenSpecAdapter(tmp_path)
    os_tasks = os_adapter.load_tasks()
    assert len(os_tasks) == 1
    assert os_tasks[0].id == "add-oauth"
    assert os_tasks[0].write_files == ["src/auth/oauth.py"]

    # 2. Beads
    beads_dir = tmp_path / ".beads"
    beads_dir.mkdir(exist_ok=True)
    issues = [
        {
            "id": "bd-101",
            "title": "Add token table",
            "status": "open",
            "files": ["src/db/schema.py"],
        },
        {
            "id": "bd-102",
            "title": "Add cost calculator",
            "status": "open",
            "depends_on": ["bd-101"],
            "description": "Updates `src/db/pricing.py`",
        },
    ]
    (beads_dir / "issues.jsonl").write_text(
        "\n".join(json.dumps(row) for row in issues) + "\n",
        encoding="utf-8",
    )
    bd_adapter = BeadsAdapter(tmp_path)
    bd_tasks = bd_adapter.load_tasks()
    assert [t.id for t in bd_tasks] == ["bd-101", "bd-102"]
    assert bd_tasks[1].depends_on == ["bd-101"]
    assert bd_tasks[1].write_files == ["src/db/pricing.py"]


def test_openspec_realistic_repository_with_proposals_and_delta_specs(
    tmp_path: Path,
) -> None:
    changes_dir = tmp_path / "openspec" / "changes"
    c_auth = changes_dir / "add-user-auth"
    c_bill = changes_dir / "stripe-billing"
    c_arch = changes_dir / "archive" / "old-change"
    c_auth.mkdir(parents=True)
    c_bill.mkdir(parents=True)
    c_arch.mkdir(parents=True)

    # Change 1: add-user-auth with proposal frontmatter
    (c_auth / "proposal.md").write_text(
        "---\n"
        "id: add-user-auth\n"
        "title: User Authentication Subsystem\n"
        "effort: high\n"
        "status: in_progress\n"
        "---\n"
        "# Proposal: User Authentication Subsystem\n",
        encoding="utf-8",
    )
    (c_auth / "tasks.md").write_text(
        "# Tasks\n"
        "- [x] 1. Data schema in `src/models/user.py`\n"
        "- [ ] 2. Auth service logic in `src/services/auth.py`\n"
        "- [ ] 3. Routes in `src/api/auth.py`\n",
        encoding="utf-8",
    )

    # Change 2: stripe-billing with delta spec and dependency on add-user-auth
    (c_bill / "proposal.md").write_text(
        "# Proposal: Stripe Billing\nDepends on: add-user-auth\n",
        encoding="utf-8",
    )
    (c_bill / "tasks.md").write_text(
        "# Tasks\n- [ ] 1. Stripe webhook receiver in `src/api/billing.py`\n",
        encoding="utf-8",
    )
    delta_dir = c_bill / "specs" / "billing"
    delta_dir.mkdir(parents=True)
    (delta_dir / "spec.md").write_text(
        "# Delta Spec for Billing\nModifies `src/services/invoice.py`.\n",
        encoding="utf-8",
    )

    adapter = OpenSpecAdapter(tmp_path)
    assert adapter.detect() is True

    tasks = adapter.load_tasks(include_done=False)
    assert len(tasks) == 2

    t_auth = next(t for t in tasks if t.id == "add-user-auth")
    assert t_auth.title == "User Authentication Subsystem"
    assert t_auth.status == TaskStatus.IN_PROGRESS
    assert t_auth.effort == EffortTier.HIGH
    assert "src/models/user.py" in t_auth.write_files
    assert "src/services/auth.py" in t_auth.write_files

    t_bill = next(t for t in tasks if t.id == "stripe-billing")
    assert "add-user-auth" in t_bill.depends_on
    assert "src/api/billing.py" in t_bill.write_files
    assert "src/services/invoice.py" in t_bill.write_files

    # Granular decomposition test for single change
    subtasks = adapter.load_granular_tasks("add-user-auth", include_done=True)
    assert len(subtasks) == 3
    assert subtasks[0].id == "add-user-auth-T01"
    assert subtasks[0].status == TaskStatus.DONE
    assert subtasks[1].depends_on == ["add-user-auth-T01"]


def test_beads_realistic_repository_with_rich_schemas_and_effort(
    tmp_path: Path,
) -> None:
    beads_dir = tmp_path / ".beads"
    beads_dir.mkdir(parents=True)
    issues = [
        {
            "id": "bd-100",
            "title": "Setup SQLite Schema Engine",
            "status": "closed",
            "files": ["src/db/engine.py"],
            "effort": "low",
        },
        {
            "id": "bd-101",
            "title": "User Authentication Models",
            "status": "open",
            "dependencies": [{"target": "bd-100", "type": "blocks"}],
            "write_files": ["src/models/user.py"],
            "story_points": 8,
            "epic": "epic-auth",
        },
        {
            "id": "bd-102",
            "title": "User Token JWT Generator",
            "status": "in_progress",
            "dependencies": ["bd-101"],
            "description": "Creates token logic in `src/auth/jwt.py`",
            "points": 3,
            "epic": "epic-auth",
        },
        {
            "id": "bd-103",
            "title": "Metrics Exporter",
            "status": "open",
            "dependencies": ["bd-100"],
            "write_files": ["src/telemetry/exporter.py"],
            "points": 1,
            "epic": "epic-telemetry",
        },
    ]
    (beads_dir / "issues.jsonl").write_text(
        "\n".join(json.dumps(row) for row in issues) + "\n",
        encoding="utf-8",
    )

    adapter = BeadsAdapter(tmp_path)
    assert adapter.detect() is True

    # When include_done is False, bd-100 is skipped
    tasks = adapter.load_tasks(include_done=False)
    assert [t.id for t in tasks] == ["bd-101", "bd-102", "bd-103"]

    t101 = tasks[0]
    assert t101.effort == EffortTier.HIGH
    assert t101.depends_on == ["bd-100"]
    assert t101.write_files == ["src/models/user.py"]

    t102 = tasks[1]
    assert t102.status == TaskStatus.IN_PROGRESS
    assert t102.effort == EffortTier.MEDIUM
    assert t102.write_files == ["src/auth/jwt.py"]

    t103 = tasks[2]
    assert t103.effort == EffortTier.LOW
    assert t103.write_files == ["src/telemetry/exporter.py"]

    # Scheduler test: bd-101 and bd-103 can run in Wave 1 parallel tracks!
    scheduler = BSPWaveScheduler(max_tracks=2)
    plan = scheduler.schedule(tasks, sdd_kit="beads")
    assert len(plan.waves) == 2
    assert set(plan.waves[0].all_task_ids) == {"bd-101", "bd-103"}
    assert plan.waves[1].all_task_ids == ["bd-102"]


def test_worktree_verify_and_claim_across_all_sdd_kits(tmp_path: Path) -> None:
    # Test worktree claim and verification for non-BMAD kit (OpenSpec)
    wm = WorktreeManager(tmp_path)
    file_roles = {
        "src/core/schema.py": FileRole.SPINE,
        "src/api/routes.py": FileRole.STANDARD,
    }
    scheduler = BSPWaveScheduler(max_tracks=2, file_roles=file_roles)
    tasks = [
        sdd_parallel_task("change-1", ["src/core/schema.py"]),
        sdd_parallel_task("change-2", ["src/api/routes.py"]),
    ]
    plan = scheduler.schedule(tasks, sdd_kit="openspec")

    wt_dir = tmp_path / ".worktrees" / "wave-1-track-a"
    lock = wm.claim_track(
        plan=plan,
        wave_number=1,
        track_id="track-a",
        worktree_path=wt_dir,
    )
    assert lock.allowed_write_globs
    assert any("openspec/changes" in g for g in lock.allowed_write_globs)
    assert "src/core/schema.py" not in lock.forbidden_files

    # verify_wave passes without violations
    violations = wm.verify_wave(plan, wave_number=1)
    assert violations == []


@pytest.mark.parametrize("kit", ["bmad", "spec-kit", "gsd", "openspec", "beads"])
def test_all_five_kits_full_wave_lifecycle_merge_and_rollback(tmp_path: Path, kit: str) -> None:
    """Verify plan -> prepare -> PreToolUse guard -> verify -> 3-way merge -> rollback
    across all 5 supported SDD kits.
    """
    repo_dir = tmp_path / f"repo-{kit}"
    create_demo_repository(repo_dir, kit=kit)

    kit_name, adapter = get_adapter(repo_dir, kit=kit)
    assert kit_name == ("spec-kit" if kit in {"spec-kit", "speckit"} else kit)

    tasks = adapter.load_tasks(include_done=False)
    assert len(tasks) == 5

    scanner = ASTHotspotScanner(repo_dir)
    meta_map = scanner.scan()
    file_roles = {rel: meta.role for rel, meta in meta_map.items()}
    crg = CRGSidecarAdapter(repo_dir, ast_scanner=scanner)

    for task in tasks:
        if task.write_files:
            impact = set(crg.compute_impact_files(task.write_files))
            for wf in task.write_files:
                meta = meta_map.get(wf)
                if meta is not None:
                    for dep_mod in meta.imports:
                        for cand in meta_map:
                            if cand.endswith(f"/{dep_mod.split('.')[-1]}.py"):
                                impact.add(cand)
            task.impact_files = sorted(impact)

    scheduler = BSPWaveScheduler(max_tracks=3, file_roles=file_roles)
    plan = scheduler.schedule(tasks, sdd_kit=kit_name)
    assert len(plan.waves) >= 2
    assert set(plan.waves[0].all_task_ids) == {"1-2", "1-3"}

    wm = WorktreeManager(repo_dir, fixed_slots=True)
    wt_paths = wm.prepare_wave(plan=plan, wave_number=1)
    assert len(wt_paths) == 2

    # Verify PreToolUse guard blocks kit constitution/state and Spine schema
    track_a_path = wm.worktree_path_for_track(1, "track-a")
    track_b_path = wm.worktree_path_for_track(1, "track-b")

    spine_dec = evaluate_tool_access(
        tool_name="write_to_file",
        tool_input={"TargetFile": (track_a_path / "src/demo_app/schema.py").as_posix()},
        cwd=track_a_path,
    )
    assert spine_dec.decision == "deny"

    # Kit specific forbidden file check
    forbidden_target = {
        "bmad": "_bmad-output/planning-artifacts/epic-1-context.md",
        "spec-kit": ".specify/memory/constitution.md",
        "gsd": ".planning/STATE.md",
        "openspec": "openspec/project.md",
        "beads": ".beads/config.yaml",
    }[kit]
    kit_dec = evaluate_tool_access(
        tool_name="write_to_file",
        tool_input={"TargetFile": (track_a_path / forbidden_target).as_posix()},
        cwd=track_a_path,
    )
    assert kit_dec.decision == "deny"

    # Simulate Worker A committing Task 1-2
    norm_file = track_a_path / "src/demo_app/normalizer.py"
    norm_file.write_text(norm_file.read_text(encoding="utf-8") + "\n# worker a\n", encoding="utf-8")

    if kit == "bmad":
        st_a = track_a_path / "_bmad-output/implementation-artifacts/sprint-status.yaml"
        st_a.write_text(
            st_a.read_text(encoding="utf-8").replace(
                "1-2-normalize-telemetry-payload: ready-for-dev",
                "1-2-normalize-telemetry-payload: done",
            ),
            encoding="utf-8",
        )
        subprocess.run(
            ["git", "add", "src/demo_app/normalizer.py", st_a.as_posix()],
            cwd=track_a_path,
            check=True,
        )
    elif kit in {"spec-kit", "speckit"}:
        tasks_a = track_a_path / ".specify/specs/001-telemetry/tasks.md"
        tasks_a.write_text(
            tasks_a.read_text(encoding="utf-8").replace("- [ ] 1-2", "- [x] 1-2"), encoding="utf-8"
        )
        subprocess.run(
            ["git", "add", "src/demo_app/normalizer.py", tasks_a.as_posix()],
            cwd=track_a_path,
            check=True,
        )
    elif kit == "gsd":
        sum_a = track_a_path / ".planning/phases/01-core/1-2-SUMMARY.md"
        sum_a.write_text("# Summary 1-2\nDone\n", encoding="utf-8")
        subprocess.run(
            ["git", "add", "src/demo_app/normalizer.py", sum_a.as_posix()],
            cwd=track_a_path,
            check=True,
        )
    elif kit == "openspec":
        tasks_a = track_a_path / "openspec/changes/1-2/tasks.md"
        tasks_a.write_text(
            tasks_a.read_text(encoding="utf-8").replace("- [ ] Normalize", "- [x] Normalize"),
            encoding="utf-8",
        )
        subprocess.run(
            ["git", "add", "src/demo_app/normalizer.py", tasks_a.as_posix()],
            cwd=track_a_path,
            check=True,
        )
    elif kit == "beads":
        iss_a = track_a_path / ".beads/issues.jsonl"
        recs = [
            json.loads(line)
            for line in iss_a.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        for r in recs:
            if r.get("id") == "1-2":
                r["status"] = "closed"
        iss_a.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n", encoding="utf-8"
        )
        subprocess.run(
            ["git", "add", "src/demo_app/normalizer.py", iss_a.as_posix()],
            cwd=track_a_path,
            check=True,
        )

    subprocess.run(["git", "commit", "-m", "feat(1-2): worker a"], cwd=track_a_path, check=True)

    # Simulate Worker B committing Task 1-3
    exp_file = track_b_path / "src/demo_app/exporter.py"
    exp_file.write_text(exp_file.read_text(encoding="utf-8") + "\n# worker b\n", encoding="utf-8")

    if kit == "bmad":
        st_b = track_b_path / "_bmad-output/implementation-artifacts/sprint-status.yaml"
        st_b.write_text(
            st_b.read_text(encoding="utf-8").replace(
                "1-3-cli-batch-export: ready-for-dev", "1-3-cli-batch-export: done"
            ),
            encoding="utf-8",
        )
        subprocess.run(
            ["git", "add", "src/demo_app/exporter.py", st_b.as_posix()],
            cwd=track_b_path,
            check=True,
        )
    elif kit in {"spec-kit", "speckit"}:
        tasks_b = track_b_path / ".specify/specs/001-telemetry/tasks.md"
        tasks_b.write_text(
            tasks_b.read_text(encoding="utf-8").replace("- [ ] 1-3", "- [x] 1-3"), encoding="utf-8"
        )
        subprocess.run(
            ["git", "add", "src/demo_app/exporter.py", tasks_b.as_posix()],
            cwd=track_b_path,
            check=True,
        )
    elif kit == "gsd":
        sum_b = track_b_path / ".planning/phases/01-core/1-3-SUMMARY.md"
        sum_b.write_text("# Summary 1-3\nDone\n", encoding="utf-8")
        subprocess.run(
            ["git", "add", "src/demo_app/exporter.py", sum_b.as_posix()],
            cwd=track_b_path,
            check=True,
        )
    elif kit == "openspec":
        tasks_b = track_b_path / "openspec/changes/1-3/tasks.md"
        tasks_b.write_text(
            tasks_b.read_text(encoding="utf-8").replace("- [ ] Add CLI", "- [x] Add CLI"),
            encoding="utf-8",
        )
        subprocess.run(
            ["git", "add", "src/demo_app/exporter.py", tasks_b.as_posix()],
            cwd=track_b_path,
            check=True,
        )
    elif kit == "beads":
        iss_b = track_b_path / ".beads/issues.jsonl"
        recs = [
            json.loads(line)
            for line in iss_b.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        for r in recs:
            if r.get("id") == "1-3":
                r["status"] = "closed"
        iss_b.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n", encoding="utf-8"
        )
        subprocess.run(
            ["git", "add", "src/demo_app/exporter.py", iss_b.as_posix()],
            cwd=track_b_path,
            check=True,
        )

    subprocess.run(["git", "commit", "-m", "feat(1-3): worker b"], cwd=track_b_path, check=True)

    # Verify wave: 0 violations
    violations = wm.verify_wave(plan=plan, wave_number=1)
    assert violations == []

    # Merge wave: 0 conflicts
    merged = wm.merge_wave(plan=plan, wave_number=1, run_quality_gates=False)
    assert len(merged) == 2

    # Check status file in repo_dir
    if kit == "bmad":
        st_content = (
            repo_dir / "_bmad-output/implementation-artifacts/sprint-status.yaml"
        ).read_text(encoding="utf-8")
        assert "1-2-normalize-telemetry-payload: done" in st_content
        assert "1-3-cli-batch-export: done" in st_content
        assert "<<<<<<<" not in st_content
    elif kit in {"spec-kit", "speckit"}:
        t_content = (repo_dir / ".specify/specs/001-telemetry/tasks.md").read_text(encoding="utf-8")
        assert "- [x] 1-2" in t_content
        assert "- [x] 1-3" in t_content
        assert "<<<<<<<" not in t_content
    elif kit == "gsd":
        s_content = (repo_dir / ".planning/STATE.md").read_text(encoding="utf-8")
        assert "- [x] 1-2" in s_content
        assert "- [x] 1-3" in s_content
        assert "<<<<<<<" not in s_content
    elif kit == "openspec":
        t12 = (repo_dir / "openspec/changes/1-2/tasks.md").read_text(encoding="utf-8")
        t13 = (repo_dir / "openspec/changes/1-3/tasks.md").read_text(encoding="utf-8")
        assert "- [x] Normalize" in t12
        assert "- [x] Add CLI" in t13
    elif kit == "beads":
        b_content = (repo_dir / ".beads/issues.jsonl").read_text(encoding="utf-8")
        assert "<<<<<<<" not in b_content
        closed_ids = [
            json.loads(line)["id"]
            for line in b_content.splitlines()
            if json.loads(line).get("status") == "closed"
        ]
        assert "1-2" in closed_ids and "1-3" in closed_ids

    # Rollback wave 1
    wrm = WorktreeRollbackManager(repo_dir)
    wrm.rollback_wave(
        wave_id=1,
        save_backup=True,
        kit=kit_name,
        task_ids=plan.waves[0].all_task_ids,
    )

    # Check backup branch exists
    bk_proc = subprocess.run(
        ["git", "branch", "--list", "backup/wave-1-before-rollback-*"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
    )
    assert bk_proc.stdout.strip() != ""

    # Verify task statuses reset
    tasks_after_rb = adapter.load_tasks(include_done=False)
    assert "1-2" in [t.id for t in tasks_after_rb]
    assert "1-3" in [t.id for t in tasks_after_rb]


def test_multi_kit_coexistence_auto_selects_kit_with_active_tasks_and_isolates_inactive_kit(
    tmp_path: Path,
) -> None:
    """When BMAD has 0 active tasks and Spec Kit has active tasks, auto-select
    Spec Kit and isolate BMAD inside the worktrees.
    """
    repo_dir = tmp_path / "coexistence_repo"
    repo_dir.mkdir(parents=True)

    # 1. BMAD artifacts with all tasks marked done
    impl_dir = repo_dir / "_bmad-output/implementation-artifacts"
    impl_dir.mkdir(parents=True)
    (impl_dir / "sprint-status.yaml").write_text(
        "development_status:\n  1-1-setup: done\n  1-2-core: done\n",
        encoding="utf-8",
    )

    # 2. Spec Kit artifacts with active [ ] tasks
    spec_dir = repo_dir / ".specify/specs/001-active"
    mem_dir = repo_dir / ".specify/memory"
    spec_dir.mkdir(parents=True)
    mem_dir.mkdir(parents=True)
    (mem_dir / "constitution.md").write_text("# Constitution\n1. Rule\n", encoding="utf-8")
    (spec_dir / "tasks.md").write_text(
        "# Tasks\n"
        "- [ ] T01 First task in `src/app/core.py`\n"
        "- [ ] T02 Second task in `src/app/api.py`\n",
        encoding="utf-8",
    )

    # 3. Source files and Git repo
    src_app = repo_dir / "src/app"
    src_app.mkdir(parents=True)
    (src_app / "core.py").write_text("def core(): pass\n", encoding="utf-8")
    (src_app / "api.py").write_text("def api(): pass\n", encoding="utf-8")

    subprocess.run(["git", "init", "-q"], cwd=repo_dir, check=True)
    git_cfg = repo_dir / ".git/config"
    if git_cfg.is_file():
        cfg_text = git_cfg.read_text(encoding="utf-8")
        cfg_text += (
            "\n[user]\n\temail = test@local\n\tname = Tester\n"
            "[core]\n\tautocrlf = false\n\tfileMode = false\n"
        )
        git_cfg.write_text(cfg_text, encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "chore: initial"], cwd=repo_dir, check=True)

    # Auto-detection should choose 'spec-kit' because BMAD has 0 active tasks!
    detected_kits = detect_sdd_kits(repo_dir)
    assert "bmad" in detected_kits and "spec-kit" in detected_kits

    kit_name, adapter = get_adapter(repo_dir, kit="auto")
    assert kit_name == "spec-kit"
    assert isinstance(adapter, SpecKitAdapter)

    # Diagnostic warning
    diags = diagnose_multi_kit_coexistence(repo_dir, kit_name)
    assert len(diags) == 1
    assert "Multiple SDD frameworks detected" in diags[0]
    assert "Active adapter: 'spec-kit'" in diags[0]

    # Prepare wave in worktree
    tasks = adapter.load_tasks(include_done=False)
    scheduler = BSPWaveScheduler(max_tracks=2)
    plan = scheduler.schedule(tasks, sdd_kit=kit_name)

    wm = WorktreeManager(repo_dir, fixed_slots=True)
    wt_paths = wm.prepare_wave(plan=plan, wave_number=1)
    track_a = wt_paths[0]

    # Writing to inactive kit (_bmad-output/) inside track-a worktree is DENIED!
    bmad_write_dec = evaluate_tool_access(
        tool_name="write_to_file",
        tool_input={
            "TargetFile": (
                track_a / "_bmad-output/implementation-artifacts/sprint-status.yaml"
            ).as_posix()
        },
        cwd=track_a,
    )
    assert bmad_write_dec.decision == "deny"


def sdd_parallel_task(task_id: str, write_files: list[str]) -> Any:
    from sdd_parallel.models import TaskNode

    return TaskNode(
        id=task_id,
        title=f"Task {task_id}",
        epic_id="general",
        status=TaskStatus.READY_FOR_DEV,
        effort=EffortTier.MEDIUM,
        write_files=write_files,
        depends_on=[],
    )


def test_spec_kit_upstream_env_vars_and_forbidden_feature_json(tmp_path: Path) -> None:
    feat_dir = tmp_path / "specs" / "001-telemetry"
    feat_dir.mkdir(parents=True)
    (feat_dir / "tasks.md").write_text(
        "# Tasks\n- [ ] T001 Setup in `src/app/core.py`\n", encoding="utf-8"
    )
    specify_dir = tmp_path / ".specify"
    specify_dir.mkdir(parents=True)
    (specify_dir / "feature.json").write_text(
        json.dumps({"current_feature": "001-telemetry"}), encoding="utf-8"
    )

    adapter = SpecKitAdapter(tmp_path)
    assert adapter.detect() is True

    from sdd_parallel.models import Wave, WaveTrack

    track = WaveTrack(track_id="track-a", effort=EffortTier.MEDIUM, task_ids=["001-telemetry:T001"])
    env_vars = adapter.get_track_env_vars(track)
    assert env_vars.get("SPECIFY_FEATURE") == "001-telemetry"
    assert env_vars.get("SPECIFY_FEATURE_DIRECTORY") == "specs/001-telemetry"

    cmd = adapter.render_worker_command(track)
    assert "SPECIFY_FEATURE=001-telemetry /speckit.implement T001" in cmd

    wave = Wave(wave_number=1, tracks=[track])
    forbidden = adapter.get_forbidden_files(wave, track)
    assert ".specify/feature.json" in forbidden or str(Path(".specify/feature.json")) in forbidden


def test_gsd_xml_task_files_and_depends_on_parsing(tmp_path: Path) -> None:
    p_dir = tmp_path / ".planning" / "phases" / "01-core"
    p_dir.mkdir(parents=True)
    (p_dir / "01-01-PLAN.md").write_text(
        "# Plan: Core Service\n\n"
        '<task type="auto">\n'
        "<name>Create Core Service</name>\n"
        "<files>src/app/core.py, src/app/utils.py</files>\n"
        "</task>\n",
        encoding="utf-8",
    )
    (p_dir / "01-02-PLAN.md").write_text(
        "# Plan: Secondary Service\n\n"
        '<task type="auto">\n'
        "<name>Create Secondary Service</name>\n"
        "<files>src/app/secondary.py</files>\n"
        "<depends_on>01-01</depends_on>\n"
        "</task>\n",
        encoding="utf-8",
    )

    adapter = GSDHijackAdapter(tmp_path)
    assert adapter.detect() is True
    tasks = adapter.load_tasks(include_done=False)
    assert len(tasks) == 2

    t1 = next(t for t in tasks if t.id == "01-01")
    assert "src/app/core.py" in t1.write_files
    assert "src/app/utils.py" in t1.write_files

    t2 = next(t for t in tasks if t.id == "01-02")
    assert "src/app/secondary.py" in t2.write_files
    assert "01-01" in t2.depends_on


def test_openspec_proposal_without_frontmatter_and_config_protection(tmp_path: Path) -> None:
    os_dir = tmp_path / "openspec"
    changes_dir = os_dir / "changes" / "feature-x"
    changes_dir.mkdir(parents=True)
    (os_dir / "config.yaml").write_text("version: 1\n", encoding="utf-8")
    (changes_dir / "proposal.md").write_text(
        "# Proposal: Feature X\n\n"
        "## Why\nCore feature\n\n"
        "## What Changes\n"
        "- `src/app/feature_x.py`\n\n"
        "Depends on: setup-base\n",
        encoding="utf-8",
    )
    (changes_dir / "tasks.md").write_text("# Tasks\n- [ ] Implement\n", encoding="utf-8")

    adapter = OpenSpecAdapter(tmp_path)
    assert adapter.detect() is True
    tasks = adapter.load_tasks(include_done=False)
    assert len(tasks) == 1
    t = tasks[0]
    assert t.id == "feature-x"
    assert "src/app/feature_x.py" in t.write_files
    assert "setup-base" in t.depends_on

    from sdd_parallel.models import Wave, WaveTrack

    track = WaveTrack(track_id="track-a", effort=EffortTier.MEDIUM, task_ids=["feature-x"])
    wave = Wave(wave_number=1, tracks=[track])
    forbidden = adapter.get_forbidden_files(wave, track)
    assert "openspec/config.yaml" in forbidden
    assert "openspec/project.md" in forbidden


def test_beads_jsonl_typed_dependencies_and_blocked_status(tmp_path: Path) -> None:
    b_dir = tmp_path / ".beads"
    b_dir.mkdir(parents=True)
    (b_dir / "config.yaml").write_text("tracker: beads\n", encoding="utf-8")
    issues = [
        {
            "id": "bd-01",
            "title": "First Issue",
            "status": "blocked",
            "files": ["src/app/a.py"],
            "dependencies": [
                {"id": "bd-00", "type": "blocks"},
                {"id": "bd-info", "type": "related"},
            ],
            "priority": 0,
        },
        {
            "id": "bd-02",
            "title": "Second Issue",
            "status": "open",
            "files": ["src/app/b.py"],
            "priority": 3,
        },
    ]
    (b_dir / "beads.jsonl").write_text(
        "\n".join(json.dumps(r) for r in issues) + "\n", encoding="utf-8"
    )

    adapter = BeadsAdapter(tmp_path)
    assert adapter.detect() is True
    assert adapter.issues_path.name == "beads.jsonl"

    tasks = adapter.load_tasks(include_done=False)
    assert len(tasks) == 2

    t1 = next(t for t in tasks if t.id == "bd-01")
    assert t1.status == TaskStatus.BLOCKED
    assert t1.effort == EffortTier.HIGH
    # "related" dependency is ignored; only "blocks" is recorded
    assert t1.depends_on == ["bd-00"]

    t2 = next(t for t in tasks if t.id == "bd-02")
    assert t2.effort == EffortTier.LOW

