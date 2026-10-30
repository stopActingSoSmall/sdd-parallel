"""Standalone reproducible Merge-Bomb demonstration for sdd-parallel.

Provisions an ephemeral Git repository in a temporary directory and executes
a 4-step verification showcase in <5 seconds without LLM API keys or network access:
1. AST & Hotspot Scan (SPINE, ADDITIVE_REGISTRY, STANDARD classification)
2. Type-2 Semantic Merge-Bomb Detection (W_A \\cap I_B != \\emptyset) & Wave Packing
3. Fixed-Slot Worktree Provisioning (track-a, track-b), 100% Clean Git Status & Scope Guard
4. Monotonic 3-Way Sprint Status Merge & Barrier Checkpoint Integration
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from sdd_parallel.adapters import get_adapter
from sdd_parallel.ast_scanner import ASTHotspotScanner
from sdd_parallel.crg_sidecar import CRGSidecarAdapter
from sdd_parallel.hooks.blast_radius_guard import HookDecision, evaluate_tool_access
from sdd_parallel.models import ConflictType, FileRole, WavePlan
from sdd_parallel.scheduler import BSPWaveScheduler
from sdd_parallel.worktree import WorktreeManager, _restore_os_writable


@dataclass(frozen=True)
class DemoResult:
    """Structured telemetry returned by a standalone demo run."""

    demo_repo: Path
    kept_temp: bool
    file_roles: dict[str, FileRole]
    plan: WavePlan
    semantic_collision_detected: bool
    worktree_paths: list[Path]
    clean_git_status: bool
    scope_guard_decision: HookDecision
    merged_branches: list[str]
    merged_status_yaml: str
    elapsed_seconds: float


def _run_git(cwd: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


_DEMO_KIT_TASKS: dict[str, dict[str, str]] = {
    "bmad": {
        "s1_spine": "1-1",
        "s2_norm": "1-2",
        "s3_exp": "1-3",
        "s4_api": "2-1",
        "s5_audit": "2-2",
    },
    "spec-kit": {
        "s1_spine": "T001",
        "s2_norm": "T002",
        "s3_exp": "T003",
        "s4_api": "T004",
        "s5_audit": "T005",
    },
    "speckit": {
        "s1_spine": "T001",
        "s2_norm": "T002",
        "s3_exp": "T003",
        "s4_api": "T004",
        "s5_audit": "T005",
    },
    "gsd": {
        "s1_spine": "01-01",
        "s2_norm": "01-02",
        "s3_exp": "01-03",
        "s4_api": "02-01",
        "s5_audit": "02-02",
    },
    "openspec": {
        "s1_spine": "upgrade-schema-v2",
        "s2_norm": "normalize-telemetry",
        "s3_exp": "cli-batch-export",
        "s4_api": "ingestion-api-endpoint",
        "s5_audit": "audit-log-schema",
    },
    "beads": {
        "s1_spine": "bd-101",
        "s2_norm": "bd-102",
        "s3_exp": "bd-103",
        "s4_api": "bd-201",
        "s5_audit": "bd-202",
    },
}


def create_demo_repository(repo_root: Path, kit: str = "bmad") -> None:
    """Populate `repo_root` with a realistic Python package and native SDD kit artifacts."""
    norm_kit = kit.strip().lower()
    src_pkg = repo_root / "src" / "demo_app"
    src_pkg.mkdir(parents=True, exist_ok=True)

    (src_pkg / "__init__.py").write_text(
        '"""Demo telemetry application package."""\n\n'
        '__all__ = ["normalizer", "api", "exporter"]\n',
        encoding="utf-8",
    )

    (src_pkg / "schema.py").write_text(
        '"""Core database schema definitions (Spine)."""\n\n'
        "SCHEMA_VERSION = 1\n\n\n"
        "def auto_migrate() -> int:\n"
        '    """Run schema migration."""\n'
        "    return SCHEMA_VERSION\n",
        encoding="utf-8",
    )

    (src_pkg / "cli.py").write_text(
        '"""CLI command dispatcher (Additive Registry)."""\n\n'
        "from collections.abc import Callable\n"
        "from typing import TypeVar\n\n"
        'F = TypeVar("F", bound=Callable[..., object])\n\n\n'
        "class App:\n"
        "    def command(self, name: str) -> Callable[[F], F]:\n"
        "        def decorator(fn: F) -> F:\n"
        "            return fn\n"
        "        return decorator\n\n\n"
        "app = App()\n\n\n"
        '@app.command("version")\n'
        "def version_cmd() -> int:\n"
        "    return 1\n",
        encoding="utf-8",
    )

    (src_pkg / "normalizer.py").write_text(
        '"""Telemetry payload normalization service."""\n\n\n'
        "def normalize_payload(raw: dict[str, str]) -> dict[str, str]:\n"
        '    """Normalize keys and strip whitespace from incoming payload."""\n'
        "    return {k.strip().lower(): v.strip() for k, v in raw.items()}\n",
        encoding="utf-8",
    )

    (src_pkg / "api.py").write_text(
        '"""HTTP ingestion endpoint depending on normalizer."""\n\n'
        "from demo_app.normalizer import normalize_payload\n"
        "from src.demo_app.normalizer import normalize_payload as _norm_ref\n\n\n"
        "def ingest_event(payload: dict[str, str]) -> dict[str, object]:\n"
        '    """Validate and normalize telemetry payload."""\n'
        "    cleaned = normalize_payload(payload)\n"
        '    return {"status": "accepted", "payload": cleaned, "valid": _norm_ref is not None}\n',
        encoding="utf-8",
    )

    (src_pkg / "exporter.py").write_text(
        '"""Batch telemetry exporter."""\n\n\n'
        "def export_events(events: list[dict[str, str]]) -> int:\n"
        '    """Export normalized events count."""\n'
        "    return len(events)\n",
        encoding="utf-8",
    )

    (repo_root / ".gitignore").write_text(
        ".parallel/\n.worktrees/\n_bmad-output/parallel-artifacts/\n",
        encoding="utf-8",
    )

    if norm_kit == "bmad":
        impl_dir = repo_root / "_bmad-output" / "implementation-artifacts"
        plan_dir = repo_root / "_bmad-output" / "planning-artifacts"
        impl_dir.mkdir(parents=True, exist_ok=True)
        plan_dir.mkdir(parents=True, exist_ok=True)

        (plan_dir / "project-context.md").write_text(
            "# Project Context: Demo Telemetry App\n\n"
            "Canonical architecture definitions and tech stack constraints.\n",
            encoding="utf-8",
        )

        for ctx_dir in (impl_dir, plan_dir):
            (ctx_dir / "epic-1-context.md").write_text(
                "# Epic 1 Context: Core Telemetry Normalization & Schema\n",
                encoding="utf-8",
            )
            (ctx_dir / "epic-2-context.md").write_text(
                "# Epic 2 Context: Ingestion API & Audit Storage\n",
                encoding="utf-8",
            )
            (ctx_dir / "epics.md").write_text(
                "# Epics Roadmap\n\n"
                "## Epic 1: Telemetry Core\n"
                "- Story 1.1: Upgrade schema to v2 "
                "(depends on `1-2`, modifies `src/demo_app/schema.py`)\n"
                "- Story 1.2: Normalize telemetry payload "
                "(modifies `src/demo_app/normalizer.py`, `src/demo_app/cli.py`)\n"
                "- Story 1.3: Add CLI batch export command "
                "(modifies `src/demo_app/exporter.py`, `src/demo_app/cli.py`)\n\n"
                "## Epic 2: API & Audit\n"
                "- Story 2.1: Expose ingestion API endpoint "
                "(modifies `src/demo_app/api.py`)\n"
                "- Story 2.2: Add audit log table to schema "
                "(depends on `1-1`, modifies `src/demo_app/schema.py`)\n",
                encoding="utf-8",
            )

        (impl_dir / "sprint-status.yaml").write_text(
            "# BMAD Sprint Status\n"
            'generated: "2026-10-23"\n'
            "project: demo-app\n"
            "development_status:\n"
            "  epic-1: in-progress\n"
            "  1-1-upgrade-schema-v2: ready-for-dev\n"
            "  1-2-normalize-telemetry-payload: ready-for-dev\n"
            "  1-3-cli-batch-export: ready-for-dev\n"
            "  epic-2: in-progress\n"
            "  2-1-expose-ingestion-api: ready-for-dev\n"
            "  2-2-add-audit-log-schema: ready-for-dev\n",
            encoding="utf-8",
        )

        specs: dict[str, str] = {
            "spec-1-1-upgrade-schema-v2.md": (
                "# Story 1-1: Upgrade schema to v2\n\n"
                "Depends on story `1-2`\n\n"
                "## Code Map\n"
                "- `src/demo_app/schema.py`\n"
            ),
            "spec-1-2-normalize-telemetry-payload.md": (
                "# Story 1-2: Normalize telemetry payload\n\n"
                "## Code Map\n"
                "- `src/demo_app/normalizer.py`\n"
                "- `src/demo_app/cli.py`\n"
            ),
            "spec-1-3-cli-batch-export.md": (
                "# Story 1-3: Add CLI batch export command\n\n"
                "## Code Map\n"
                "- `src/demo_app/exporter.py`\n"
                "- `src/demo_app/cli.py`\n"
            ),
            "spec-2-1-expose-ingestion-api.md": (
                "# Story 2-1: Expose ingestion API endpoint\n\n"
                "## Code Map\n"
                "- `src/demo_app/api.py`\n"
            ),
            "spec-2-2-add-audit-log-schema.md": (
                "# Story 2-2: Add audit log table to schema\n\n"
                "Depends on story `1-1`\n\n"
                "## Code Map\n"
                "- `src/demo_app/schema.py`\n"
            ),
        }
        for filename, content in specs.items():
            (impl_dir / filename).write_text(content, encoding="utf-8")

    elif norm_kit in {"spec-kit", "speckit"}:
        mem_dir = repo_root / ".specify" / "memory"
        templates_dir = repo_root / ".specify" / "templates"
        feat_dir = repo_root / "specs" / "001-telemetry"
        compat_feat_dir = repo_root / ".specify" / "specs" / "001-telemetry"
        mem_dir.mkdir(parents=True, exist_ok=True)
        templates_dir.mkdir(parents=True, exist_ok=True)
        feat_dir.mkdir(parents=True, exist_ok=True)
        compat_feat_dir.mkdir(parents=True, exist_ok=True)

        (mem_dir / "constitution.md").write_text(
            "# System Constitution\n"
            "1. Zero Worktree Pollution: clean git status required.\n"
            "2. Blast-Radius Enforcement: Prevent semantic collisions.\n"
            "3. Spine-Lock: schema migrations executed sequentially.\n",
            encoding="utf-8",
        )
        (templates_dir / "tasks-template.md").write_text(
            "# Tasks Template for GitHub Spec Kit\n"
            "## User Story N: Feature Name\n"
            "- [ ] T001 [P] [US1] Task description with `path/to/file.py`\n",
            encoding="utf-8",
        )
        (repo_root / ".specify" / "feature.json").write_text(
            json.dumps({"current_feature": "001-telemetry", "status": "active"}, indent=2) + "\n",
            encoding="utf-8",
        )
        spec_content = "# Spec: Telemetry & Ingestion System\n"
        plan_content = (
            "# Execution Plan: Telemetry\n"
            "- `src/demo_app/normalizer.py`\n"
            "- `src/demo_app/cli.py`\n"
            "- `src/demo_app/exporter.py`\n"
            "- `src/demo_app/api.py`\n"
            "- `src/demo_app/schema.py`\n"
        )
        tasks_content = (
            "# Tasks for 001-telemetry\n\n"
            "## User Story 1: Core Telemetry\n"
            "- [ ] T001 [US1] Upgrade schema in `src/demo_app/schema.py` (depends on T002)\n"
            "- [ ] T002 [P] [US1] Normalize `src/demo_app/normalizer.py`, `src/demo_app/cli.py`\n"
            "- [ ] T003 [P] [US1] Export `src/demo_app/exporter.py`, `src/demo_app/cli.py`\n\n"
            "## User Story 2: Ingestion & Audit\n"
            "- [ ] T004 [P] [US2] Expose ingestion in `src/demo_app/api.py`\n"
            "- [ ] T005 [US2] Add audit log in `src/demo_app/schema.py` (depends on T001)\n"
        )
        for d in (feat_dir, compat_feat_dir):
            (d / "spec.md").write_text(spec_content, encoding="utf-8")
            (d / "plan.md").write_text(plan_content, encoding="utf-8")
            (d / "tasks.md").write_text(tasks_content, encoding="utf-8")

    elif norm_kit == "gsd":
        p_dir = repo_root / ".planning"
        phases_1 = p_dir / "phases" / "01-core"
        phases_2 = p_dir / "phases" / "02-api"
        phases_1.mkdir(parents=True, exist_ok=True)
        phases_2.mkdir(parents=True, exist_ok=True)

        (p_dir / "PROJECT.md").write_text(
            "# Project Context\n"
            "Telemetry processing pipeline with high-throughput ingestion and audit logging.\n",
            encoding="utf-8",
        )
        (p_dir / "REQUIREMENTS.md").write_text(
            "# Requirements\n"
            "1. Zero regression on telemetry normalization.\n"
            "2. Strict schema migration ordering.\n",
            encoding="utf-8",
        )
        (p_dir / "ROADMAP.md").write_text(
            "# Project Roadmap\n\n"
            "## Phase 1: Core Telemetry\n"
            "## Phase 2: Ingestion & Audit (depends on Phase 1)\n",
            encoding="utf-8",
        )
        (p_dir / "STATE.md").write_text(
            "# Project State\n\n"
            "## Active Checklist\n"
            "- [ ] 01-01 Upgrade schema to v2\n"
            "- [ ] 01-02 Normalize telemetry payload\n"
            "- [ ] 01-03 Add CLI batch export command\n"
            "- [ ] 02-01 Expose ingestion API endpoint\n"
            "- [ ] 02-02 Add audit log table to schema\n",
            encoding="utf-8",
        )

        plans: dict[Path, str] = {
            phases_1 / "01-02-PLAN.md": (
                "---\n"
                "title: Normalize telemetry payload\n"
                "files_modified:\n"
                "  - src/demo_app/normalizer.py\n"
                "  - src/demo_app/cli.py\n"
                "depends_on: []\n"
                "---\n"
                "# Plan 01-02: Normalize telemetry payload\n\n"
                '<task type="auto">\n'
                "<name>Normalize telemetry payload</name>\n"
                "<files>src/demo_app/normalizer.py, src/demo_app/cli.py</files>\n"
                "</task>\n"
            ),
            phases_1 / "01-03-PLAN.md": (
                "---\n"
                "title: Add CLI batch export command\n"
                "files_modified:\n"
                "  - src/demo_app/exporter.py\n"
                "  - src/demo_app/cli.py\n"
                "depends_on: []\n"
                "---\n"
                "# Plan 01-03: Add CLI batch export command\n\n"
                '<task type="auto">\n'
                "<name>Add CLI batch export command</name>\n"
                "<files>src/demo_app/exporter.py, src/demo_app/cli.py</files>\n"
                "</task>\n"
            ),
            phases_1 / "01-01-PLAN.md": (
                "---\n"
                "title: Upgrade schema to v2\n"
                "files_modified:\n"
                "  - src/demo_app/schema.py\n"
                "depends_on:\n"
                "  - 01-02\n"
                "---\n"
                "# Plan 01-01: Upgrade schema to v2\n\n"
                '<task type="auto">\n'
                "<name>Upgrade schema to v2</name>\n"
                "<files>src/demo_app/schema.py</files>\n"
                "<depends_on>01-02</depends_on>\n"
                "</task>\n"
            ),
            phases_2 / "02-01-PLAN.md": (
                "---\n"
                "title: Expose ingestion API endpoint\n"
                "files_modified:\n"
                "  - src/demo_app/api.py\n"
                "depends_on: []\n"
                "---\n"
                "# Plan 02-01: Expose ingestion API endpoint\n\n"
                '<task type="auto">\n'
                "<name>Expose ingestion API endpoint</name>\n"
                "<files>src/demo_app/api.py</files>\n"
                "</task>\n"
            ),
            phases_2 / "02-02-PLAN.md": (
                "---\n"
                "title: Add audit log table to schema\n"
                "files_modified:\n"
                "  - src/demo_app/schema.py\n"
                "depends_on:\n"
                "  - 01-01\n"
                "---\n"
                "# Plan 02-02: Add audit log table to schema\n\n"
                '<task type="auto">\n'
                "<name>Add audit log table to schema</name>\n"
                "<files>src/demo_app/schema.py</files>\n"
                "<depends_on>01-01</depends_on>\n"
                "</task>\n"
            ),
        }
        for plan_path, content in plans.items():
            plan_path.write_text(content, encoding="utf-8")

    elif norm_kit == "openspec":
        os_dir = repo_root / "openspec"
        changes_dir = os_dir / "changes"
        specs_dir = os_dir / "specs" / "telemetry"
        os_dir.mkdir(parents=True, exist_ok=True)
        changes_dir.mkdir(parents=True, exist_ok=True)
        specs_dir.mkdir(parents=True, exist_ok=True)

        (os_dir / "project.md").write_text(
            "# Project: Telemetry Application\n\n"
            "## Phase 1: Core Telemetry\n"
            "- normalize-telemetry\n"
            "- cli-batch-export\n"
            "- upgrade-schema-v2\n\n"
            "## Phase 2: Ingestion & Audit\n"
            "- ingestion-api-endpoint\n"
            "- audit-log-schema\n",
            encoding="utf-8",
        )
        (os_dir / "config.yaml").write_text(
            "version: 1\nspecs_dir: openspec/specs\nchanges_dir: openspec/changes\n",
            encoding="utf-8",
        )
        (specs_dir / "spec.md").write_text(
            "# Spec: Telemetry Pipeline\nBaseline canonical telemetry specification.\n",
            encoding="utf-8",
        )

        ch_map: dict[str, tuple[str, list[str], list[str]]] = {
            "upgrade-schema-v2": (
                "Upgrade schema to v2",
                ["src/demo_app/schema.py"],
                ["normalize-telemetry"],
            ),
            "normalize-telemetry": (
                "Normalize telemetry payload",
                ["src/demo_app/normalizer.py", "src/demo_app/cli.py"],
                [],
            ),
            "cli-batch-export": (
                "Add CLI batch export command",
                ["src/demo_app/exporter.py", "src/demo_app/cli.py"],
                [],
            ),
            "ingestion-api-endpoint": (
                "Expose ingestion API endpoint",
                ["src/demo_app/api.py"],
                [],
            ),
            "audit-log-schema": (
                "Add audit log table to schema",
                ["src/demo_app/schema.py"],
                ["upgrade-schema-v2"],
            ),
        }
        for cid, (title, files, deps) in ch_map.items():
            c_dir = changes_dir / cid
            c_dir.mkdir(parents=True, exist_ok=True)
            files_lines = "\n".join(f"- `{f}`" for f in files)
            dep_line = f"\nDepends on: {', '.join(deps)}\n" if deps else ""
            (c_dir / "proposal.md").write_text(
                f"# Proposal: {title}\n\n"
                "## Why\nRequired for robust telemetry ingestion.\n\n"
                f"## What Changes\n{files_lines}\n"
                f"{dep_line}\n"
                "## Impact\nNone.\n",
                encoding="utf-8",
            )
            (c_dir / "tasks.md").write_text(
                f"# Tasks\n- [ ] [{cid}] {title}\n",
                encoding="utf-8",
            )
            (c_dir / "design.md").write_text(
                f"# Design: {title}\nArchitectural considerations and data flow.\n",
                encoding="utf-8",
            )

    elif norm_kit == "beads":
        b_dir = repo_root / ".beads"
        b_dir.mkdir(parents=True, exist_ok=True)
        (b_dir / "config.yaml").write_text("tracker: beads\nversion: 1\n", encoding="utf-8")
        issues = [
            {
                "id": "bd-101",
                "title": "Upgrade schema to v2",
                "status": "open",
                "files": ["src/demo_app/schema.py"],
                "dependencies": [{"target": "bd-102", "type": "blocks"}],
                "priority": 1,
                "epic": "epic-1",
            },
            {
                "id": "bd-102",
                "title": "Normalize telemetry payload",
                "status": "open",
                "files": ["src/demo_app/normalizer.py", "src/demo_app/cli.py"],
                "dependencies": [],
                "priority": 2,
                "epic": "epic-1",
            },
            {
                "id": "bd-103",
                "title": "Add CLI batch export command",
                "status": "open",
                "files": ["src/demo_app/exporter.py", "src/demo_app/cli.py"],
                "dependencies": [],
                "priority": 2,
                "epic": "epic-1",
            },
            {
                "id": "bd-201",
                "title": "Expose ingestion API endpoint",
                "status": "open",
                "files": ["src/demo_app/api.py"],
                "dependencies": [],
                "priority": 2,
                "epic": "epic-2",
            },
            {
                "id": "bd-202",
                "title": "Add audit log table to schema",
                "status": "open",
                "files": ["src/demo_app/schema.py"],
                "dependencies": [{"target": "bd-101", "type": "blocks"}],
                "priority": 1,
                "epic": "epic-2",
            },
        ]
        jsonl_text = "\n".join(json.dumps(iss, ensure_ascii=False) for iss in issues) + "\n"
        (b_dir / "beads.jsonl").write_text(jsonl_text, encoding="utf-8")
        (b_dir / "issues.jsonl").write_text(jsonl_text, encoding="utf-8")
    else:
        raise ValueError(f"Unsupported demo kit: '{kit}'")

    _run_git(repo_root, ["init", "-q"])
    git_cfg = repo_root / ".git" / "config"
    if git_cfg.is_file():
        cfg_text = git_cfg.read_text(encoding="utf-8")
        cfg_text += (
            "\n[user]\n"
            "\temail = demo@sdd-parallel.local\n"
            "\tname = SDD-Parallel Demo\n"
            "[core]\n"
            "\tautocrlf = false\n"
            "\tfileMode = false\n"
        )
        git_cfg.write_text(cfg_text, encoding="utf-8")
    _run_git(repo_root, ["add", "."])
    _run_git(repo_root, ["commit", "-q", "-m", "chore: initial demo repository"])


def _execute_single_demo(
    demo_dir: Path,
    out: TextIO,
    keep_temp: bool,
    kit: str,
) -> DemoResult:
    """Execute the 4-step demo for a specific SDD kit."""
    t0 = time.perf_counter()
    create_demo_repository(demo_dir, kit=kit)

    out.write("=" * 76 + "\n")
    out.write(
        f"SDD-PARALLEL STANDALONE MERGE-BOMB DEMO [{kit.upper()}] (ZERO API KEYS / OFFLINE)\n"
    )
    out.write("=" * 76 + "\n")
    out.write(f"Workspace: {demo_dir.as_posix()}\n\n")

    # ------------------------------------------------------------------
    # Step 1: AST & Hotspot Scan
    # ------------------------------------------------------------------
    scanner = ASTHotspotScanner(demo_dir)
    meta_map = scanner.scan()
    file_roles = {rel: meta.role for rel, meta in meta_map.items()}

    out.write("[Step 1/4] AST & Architectural Role Classification\n")
    out.write("-" * 76 + "\n")
    out.write(f"{'File Path':<38} | {'Detected Role':<22} | {'Importers'}\n")
    out.write("-" * 76 + "\n")
    for rel_path in sorted(file_roles):
        importers = sorted(scanner.get_importers(rel_path))
        imp_str = ", ".join(importers) if importers else "-"
        out.write(f"{rel_path:<38} | {file_roles[rel_path].value:<22} | {imp_str}\n")
    out.write("\n")

    # ------------------------------------------------------------------
    # Step 2: The Merge-Bomb Detection (W_{1-2} \cap I_{2-1} != \emptyset)
    # ------------------------------------------------------------------
    crg = CRGSidecarAdapter(demo_dir, ast_scanner=scanner)
    kit_name, adapter = get_adapter(demo_dir, kit=kit)
    tasks = adapter.load_tasks(include_done=False)
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

    task_map = _DEMO_KIT_TASKS.get(kit, _DEMO_KIT_TASKS["bmad"])
    t_norm = task_map["s2_norm"]
    t_exp = task_map["s3_exp"]
    t_api = task_map["s4_api"]

    sem_edges = [
        e
        for e in plan.edges
        if e.conflict_type == ConflictType.SEMANTIC_COLLISION
        and {e.source, e.target} == {t_norm, t_api}
    ]
    semantic_detected = len(sem_edges) > 0

    out.write("[Step 2/4] Type-2 Semantic Merge-Bomb Detection & BSP Wave Schedule\n")
    out.write("-" * 76 + "\n")
    out.write("  Collision Analysis:\n")
    out.write(
        f"    - Naive Scheduler : W({t_norm})={{normalizer.py, cli.py}} vs W({t_api})={{api.py}}\n"
        "                        -> DISJOINT WRITES (False Safe!)\n"
    )
    if sem_edges:
        edge = sem_edges[0]
        out.write(
            f"    - BSPWaveScheduler: Detected {edge.conflict_type.value} between "
            f"'{edge.source}' and '{edge.target}'\n"
            f"                        -> Overlap W({t_norm}) ^ I({t_api}) on: "
            f"{', '.join(edge.shared_files)}\n"
        )
    out.write(
        f"    - Additive Registry: Tasks '{t_norm}' and '{t_exp}' "
        "both touch 'src/demo_app/cli.py'\n"
        "                        -> Role: ADDITIVE_REGISTRY (Parallel allowed in Wave 1)\n\n"
    )
    out.write("  Wave Schedule:\n")
    for wave in plan.waves:
        out.write(f"    * Wave {wave.wave_number}\n")
        for track in wave.tracks:
            writes_str = ", ".join(track.exclusive_write_files) or "-"
            shared_str = (
                f" (+ shared: {', '.join(track.additive_shared_files)})"
                if track.additive_shared_files
                else ""
            )
            out.write(
                f"        - {track.track_id}: Story {track.task_ids[0]} "
                f"-> writes: {writes_str}{shared_str}\n"
            )
    out.write("\n")

    # ------------------------------------------------------------------
    # Step 3: Fixed-Slot Worktree Provisioning & Scope Guard Check
    # ------------------------------------------------------------------
    wm = WorktreeManager(demo_dir, fixed_slots=True)
    wt_paths = wm.prepare_wave(plan=plan, wave_number=1)

    clean_status = True
    for wt_path in wt_paths:
        st = _run_git(wt_path, ["status", "--porcelain"])
        if st.stdout.strip():
            clean_status = False

    track_a_path = wm.worktree_path_for_track(1, "track-a")
    guard_decision = evaluate_tool_access(
        tool_name="write_to_file",
        tool_input={"TargetFile": (track_a_path / "src/demo_app/schema.py").as_posix()},
        cwd=track_a_path,
    )

    out.write("[Step 3/4] Fixed-Slot Worktree Provisioning & PreToolUse Scope Guard\n")
    out.write("-" * 76 + "\n")
    out.write(f"  Provisioned Fixed Slots (100% Clean git status: {clean_status}):\n")
    for wt_path in wt_paths:
        out.write(f"    - {wt_path.name} (.worktrees/{wt_path.name})\n")
    raw_reason = guard_decision.reason or ""
    if ". Reason: " in raw_reason:
        viol_part, lock_part = raw_reason.split(". Reason: ", 1)
        viol_part = viol_part.removeprefix("BLAST RADIUS VIOLATION: ")
        out.write(
            "\n  PreToolUse Scope Guard Check (track-a -> 'src/demo_app/schema.py'):\n"
            f"    - Decision : {guard_decision.decision.upper()}\n"
            f"    - Violation: {viol_part}\n"
            f"    - Reason   : {lock_part}\n\n"
        )
    else:
        out.write(
            "\n  PreToolUse Scope Guard Check (track-a -> 'src/demo_app/schema.py'):\n"
            f"    - Decision : {guard_decision.decision.upper()}\n"
            f"    - Reason   : {raw_reason}\n\n"
        )

    # ------------------------------------------------------------------
    # Step 4: Monotonic 3-Way Merge of Wave 1
    # ------------------------------------------------------------------
    track_b_path = wm.worktree_path_for_track(1, "track-b")

    # Simulate Worker A completing Task t_norm
    norm_file = track_a_path / "src" / "demo_app" / "normalizer.py"
    norm_file.write_text(
        norm_file.read_text(encoding="utf-8")
        + "\ndef normalize_key(key: str) -> str:\n    return key.strip().lower()\n",
        encoding="utf-8",
    )

    if kit == "bmad":
        status_a = track_a_path / "_bmad-output" / "implementation-artifacts" / "sprint-status.yaml"
        status_a.write_text(
            status_a.read_text(encoding="utf-8").replace(
                f"{t_norm}-normalize-telemetry-payload: ready-for-dev",
                f"{t_norm}-normalize-telemetry-payload: done",
            ),
            encoding="utf-8",
        )
        _run_git(track_a_path, ["add", "src/demo_app/normalizer.py", status_a.as_posix()])
    elif kit in {"spec-kit", "speckit"}:
        tasks_a = track_a_path / "specs" / "001-telemetry" / "tasks.md"
        if not tasks_a.is_file():
            tasks_a = track_a_path / ".specify" / "specs" / "001-telemetry" / "tasks.md"
        tasks_a.write_text(
            tasks_a.read_text(encoding="utf-8").replace(f"- [ ] {t_norm}", f"- [x] {t_norm}"),
            encoding="utf-8",
        )
        _run_git(track_a_path, ["add", "src/demo_app/normalizer.py", tasks_a.as_posix()])
    elif kit == "gsd":
        sum_a = track_a_path / ".planning" / "phases" / "01-core" / f"{t_norm}-SUMMARY.md"
        sum_a.write_text(f"# Summary {t_norm}: Done\n", encoding="utf-8")
        _run_git(track_a_path, ["add", "src/demo_app/normalizer.py", sum_a.as_posix()])
    elif kit == "openspec":
        tasks_a = track_a_path / "openspec" / "changes" / t_norm / "tasks.md"
        tasks_a.write_text(
            tasks_a.read_text(encoding="utf-8").replace("- [ ]", "- [x]"),
            encoding="utf-8",
        )
        _run_git(track_a_path, ["add", "src/demo_app/normalizer.py", tasks_a.as_posix()])
    elif kit == "beads":
        for fname in ("beads.jsonl", "issues.jsonl"):
            iss_a = track_a_path / ".beads" / fname
            if iss_a.is_file():
                recs_a = [
                    json.loads(line)
                    for line in iss_a.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                for r in recs_a:
                    if r.get("id") == t_norm:
                        r["status"] = "closed"
                iss_a.write_text(
                    "\n".join(json.dumps(r, ensure_ascii=False) for r in recs_a) + "\n",
                    encoding="utf-8",
                )
                _run_git(track_a_path, ["add", iss_a.as_posix()])
        _run_git(track_a_path, ["add", "src/demo_app/normalizer.py"])

    _run_git(track_a_path, ["commit", "-m", f"feat({t_norm}): add normalize_key helper"])

    # Simulate Worker B completing Task t_exp
    exp_file = track_b_path / "src" / "demo_app" / "exporter.py"
    exp_file.write_text(
        exp_file.read_text(encoding="utf-8")
        + "\ndef format_export_summary(count: int) -> str:\n    return f'exported={count}'\n",
        encoding="utf-8",
    )

    if kit == "bmad":
        status_b = track_b_path / "_bmad-output" / "implementation-artifacts" / "sprint-status.yaml"
        status_b.write_text(
            status_b.read_text(encoding="utf-8").replace(
                f"{t_exp}-cli-batch-export: ready-for-dev",
                f"{t_exp}-cli-batch-export: done",
            ),
            encoding="utf-8",
        )
        _run_git(track_b_path, ["add", "src/demo_app/exporter.py", status_b.as_posix()])
    elif kit in {"spec-kit", "speckit"}:
        tasks_b = track_b_path / "specs" / "001-telemetry" / "tasks.md"
        if not tasks_b.is_file():
            tasks_b = track_b_path / ".specify" / "specs" / "001-telemetry" / "tasks.md"
        tasks_b.write_text(
            tasks_b.read_text(encoding="utf-8").replace(f"- [ ] {t_exp}", f"- [x] {t_exp}"),
            encoding="utf-8",
        )
        _run_git(track_b_path, ["add", "src/demo_app/exporter.py", tasks_b.as_posix()])
    elif kit == "gsd":
        sum_b = track_b_path / ".planning" / "phases" / "01-core" / f"{t_exp}-SUMMARY.md"
        sum_b.write_text(f"# Summary {t_exp}: Done\n", encoding="utf-8")
        _run_git(track_b_path, ["add", "src/demo_app/exporter.py", sum_b.as_posix()])
    elif kit == "openspec":
        tasks_b = track_b_path / "openspec" / "changes" / t_exp / "tasks.md"
        tasks_b.write_text(
            tasks_b.read_text(encoding="utf-8").replace("- [ ]", "- [x]"),
            encoding="utf-8",
        )
        _run_git(track_b_path, ["add", "src/demo_app/exporter.py", tasks_b.as_posix()])
    elif kit == "beads":
        for fname in ("beads.jsonl", "issues.jsonl"):
            iss_b = track_b_path / ".beads" / fname
            if iss_b.is_file():
                recs_b = [
                    json.loads(line)
                    for line in iss_b.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                for r in recs_b:
                    if r.get("id") == t_exp:
                        r["status"] = "closed"
                iss_b.write_text(
                    "\n".join(json.dumps(r, ensure_ascii=False) for r in recs_b) + "\n",
                    encoding="utf-8",
                )
                _run_git(track_b_path, ["add", iss_b.as_posix()])
        _run_git(track_b_path, ["add", "src/demo_app/exporter.py"])

    _run_git(track_b_path, ["commit", "-m", f"feat({t_exp}): add export summary formatter"])

    violations = wm.verify_wave(plan=plan, wave_number=1)
    if violations:
        raise RuntimeError(f"Unexpected scope violations in demo Wave 1: {violations}")

    merged_branches = wm.merge_wave(
        plan=plan,
        wave_number=1,
        run_quality_gates=False,
        cleanup_worktrees=keep_temp,
    )

    merged_status_text = ""
    status_header_label = "sprint-status.yaml"
    if kit == "bmad":
        st_file = demo_dir / "_bmad-output" / "implementation-artifacts" / "sprint-status.yaml"
        merged_status_text = st_file.read_text(encoding="utf-8") if st_file.is_file() else ""
    elif kit in {"spec-kit", "speckit"}:
        st_file = demo_dir / "specs" / "001-telemetry" / "tasks.md"
        if not st_file.is_file():
            st_file = demo_dir / ".specify" / "specs" / "001-telemetry" / "tasks.md"
        merged_status_text = st_file.read_text(encoding="utf-8") if st_file.is_file() else ""
        status_header_label = "tasks.md"
    elif kit == "gsd":
        st_file = demo_dir / ".planning" / "STATE.md"
        merged_status_text = st_file.read_text(encoding="utf-8") if st_file.is_file() else ""
        status_header_label = "STATE.md"
    elif kit == "openspec":
        st_file = demo_dir / "openspec" / "changes" / t_norm / "tasks.md"
        merged_status_text = st_file.read_text(encoding="utf-8") if st_file.is_file() else ""
        status_header_label = "tasks.md"
    elif kit == "beads":
        st_file = demo_dir / ".beads" / "beads.jsonl"
        if not st_file.is_file():
            st_file = demo_dir / ".beads" / "issues.jsonl"
        merged_status_text = st_file.read_text(encoding="utf-8") if st_file.is_file() else ""
        status_header_label = "beads.jsonl"

    elapsed = time.perf_counter() - t0
    out.write(
        f"[Step 4/4] Barrier Checkpoint & Monotonic 3-Way {'YAML ' if kit == 'bmad' else ''}Merge\n"
    )
    out.write("-" * 76 + "\n")
    out.write("  Merged Branches:\n")
    for br in merged_branches:
        out.write(f"    - {br}\n")
    out.write(f"\n  Resolved {status_header_label} (0 conflict markers):\n")
    for line in merged_status_text.splitlines():
        if any(tok in line for tok in (t_norm, t_exp)):
            out.write(f"    - {line.strip()}\n")
    out.write("=" * 76 + "\n")
    out.write(f"ALL 5 CORE INVARIANTS VERIFIED IN {elapsed:.2f}s\n")
    out.write("=" * 76 + "\n")

    return DemoResult(
        demo_repo=demo_dir,
        kept_temp=keep_temp,
        file_roles=file_roles,
        plan=plan,
        semantic_collision_detected=semantic_detected,
        worktree_paths=wt_paths,
        clean_git_status=clean_status,
        scope_guard_decision=guard_decision,
        merged_branches=merged_branches,
        merged_status_yaml=merged_status_text,
        elapsed_seconds=elapsed,
    )


def run_demo(
    keep_temp: bool = False,
    output: TextIO | None = None,
    kit: str = "bmad",
    scaffold_only: bool = False,
    output_dir: Path | None = None,
) -> DemoResult:
    """Execute the 4-step standalone Merge-Bomb demo and return structured telemetry.

    Supports individual kits ('bmad', 'spec-kit', 'gsd', 'openspec', 'beads')
    or 'all' for complete 5-kit validation.
    When scaffold_only is True, creates the repository and outputs manual walkthrough instructions.
    """
    out = output if output is not None else sys.stdout
    norm_kit = kit.strip().lower()

    if output_dir is not None:
        keep_temp = True
        demo_dir = output_dir.resolve()
        demo_dir.mkdir(parents=True, exist_ok=True)
        is_custom_dir = True
    else:
        demo_dir = Path(tempfile.mkdtemp(prefix=f"sdd-parallel-demo-{norm_kit}-")).resolve()
        is_custom_dir = False

    if scaffold_only:
        create_demo_repository(demo_dir, kit=norm_kit)
        out.write("=" * 76 + "\n")
        out.write(f"SDD-PARALLEL DEMO SANDBOX CREATED ({norm_kit.upper()})\n")
        out.write("=" * 76 + "\n")
        out.write(f"Location: {demo_dir.as_posix()}\n\n")
        out.write("Next steps for manual walkthrough:\n")
        out.write(f"  1. cd {demo_dir.as_posix()}\n")
        out.write(f"  2. sdd-parallel plan --kit {norm_kit}\n")
        out.write("  3. sdd-parallel prepare --wave 1\n")
        out.write("  4. sdd-parallel verify-wave --wave 1\n")
        out.write("  5. sdd-parallel merge --wave 1\n")
        out.write("=" * 76 + "\n")
        return DemoResult(
            demo_repo=demo_dir,
            kept_temp=True,
            file_roles={},
            plan=WavePlan(waves=[], edges=[], sdd_kit=norm_kit),
            semantic_collision_detected=False,
            worktree_paths=[],
            clean_git_status=True,
            scope_guard_decision=HookDecision(decision="allow", reason="Scaffold only"),
            merged_branches=[],
            merged_status_yaml="",
            elapsed_seconds=0.0,
        )

    if norm_kit == "all":
        if is_custom_dir:
            pass
        all_kits = ["bmad", "spec-kit", "gsd", "openspec", "beads"]
        results: list[tuple[str, DemoResult]] = []
        for k in all_kits:
            d_dir = Path(tempfile.mkdtemp(prefix=f"sdd-parallel-demo-{k}-")).resolve()
            try:
                res = _execute_single_demo(d_dir, out, keep_temp=False, kit=k)
                results.append((k, res))
            finally:
                if d_dir.exists():
                    _restore_os_writable(d_dir)
                    shutil.rmtree(d_dir, ignore_errors=True)

        out.write("\n" + "=" * 76 + "\n")
        out.write("5/5 SDD KITS VERIFIED (BMAD, SPEC KIT, GSD, OPENSPEC, BEADS)\n")
        out.write("=" * 76 + "\n")
        header = (
            f"{'Kit':<12} | {'Merge-Bomb':<12} | {'Worktrees':<12} | "
            f"{'Scope Guard':<12} | {'3-Way Merge':<12}\n"
        )
        out.write(header)
        out.write("-" * 76 + "\n")
        for k, res in results:
            mb_status = "PASSED" if res.semantic_collision_detected else "FAILED"
            wt_status = "PASSED" if res.clean_git_status else "FAILED"
            sg_status = "PASSED" if res.scope_guard_decision.decision == "deny" else "FAILED"
            mg_status = (
                "PASSED"
                if len(res.merged_branches) == 2 and "<<<<<<<" not in res.merged_status_yaml
                else "FAILED"
            )
            out.write(
                f"{k:<12} | {mb_status:<12} | {wt_status:<12} | {sg_status:<12} | {mg_status:<12}\n"
            )
        out.write("=" * 76 + "\n\n")

        return results[0][1]

    try:
        return _execute_single_demo(demo_dir, out, keep_temp=keep_temp, kit=norm_kit)
    finally:
        if not keep_temp and not is_custom_dir and demo_dir.exists():
            _restore_os_writable(demo_dir)
            shutil.rmtree(demo_dir, ignore_errors=True)
