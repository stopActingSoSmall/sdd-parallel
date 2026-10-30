"""Command-line interface for sdd-parallel.

Subcommands: plan, prepare, claim-track, verify-wave, merge, merge-status,
check-lock, install-plugin, autopilot, abort-wave, rollback-wave, run-guide, demo.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from sdd_parallel.adapters import diagnose_multi_kit_coexistence, get_adapter
from sdd_parallel.ast_scanner import ASTHotspotScanner
from sdd_parallel.autopilot import AutopilotLauncher
from sdd_parallel.crg_sidecar import CRGSidecarAdapter
from sdd_parallel.demo import run_demo
from sdd_parallel.hooks.blast_radius_guard import evaluate_tool_access
from sdd_parallel.models import TaskNode, WavePlan
from sdd_parallel.plugin_installer import install_plugin
from sdd_parallel.scheduler import BSPWaveScheduler
from sdd_parallel.status_merger import merge_sprint_status_files
from sdd_parallel.worktree import (
    LIFORollbackViolationError,
    WorktreeManager,
    WorktreeRollbackManager,
    default_antigravity_worktrees_dir,
    read_parallel_state,
    write_parallel_state,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sdd-parallel",
        description="SDD-agnostic multi-track parallel roadmap orchestrator and worktree manager",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # plan
    p_plan = sub.add_parser("plan", help="Analyze SDD roadmap, AST/CRG graph, and build WavePlan")
    p_plan.add_argument("--repo", type=Path, default=Path.cwd(), help="Target repository root")
    p_plan.add_argument("--max-tracks", type=int, default=3, help="Max parallel tracks per wave")
    p_plan.add_argument(
        "--kit",
        type=str,
        default="auto",
        help="SDD kit adapter (auto, bmad, spec-kit, gsd, openspec, beads)",
    )
    p_plan.add_argument(
        "--include-done",
        action="store_true",
        help="Include already completed stories in the schedule analysis",
    )
    p_plan.add_argument(
        "--dry-run",
        action="store_true",
        help="Print execution plan without writing artifacts into the repository",
    )
    p_plan.add_argument(
        "--plan-source",
        type=str,
        default="auto",
        choices=["auto", "plan", "specs"],
        help="Source of tasks and file footprints (auto, plan, specs)",
    )
    p_plan.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Optional custom output directory for execution-plan-parallel.md and wave-plan.json",
    )

    # prepare
    p_prep = sub.add_parser("prepare", help="Provision git worktrees and inject boundary locks")
    p_prep.add_argument("--repo", type=Path, default=Path.cwd(), help="Target repository root")
    p_prep.add_argument("--wave", type=int, required=True, help="Wave number to prepare")
    p_prep.add_argument("--max-tracks", type=int, default=3, help="Max parallel tracks per wave")
    p_prep.add_argument("--kit", type=str, default="auto", help="SDD kit adapter")
    p_prep.add_argument("--headless", action="store_true", help="Enable Silent File-State Handoff")
    p_prep.add_argument(
        "--antigravity-layout",
        action="store_true",
        help="Place or link worktrees in ~/.gemini/antigravity/worktrees/<Project>/<track>",
    )
    p_prep.add_argument(
        "--dynamic-worktrees",
        action="store_true",
        help="Use dynamic wave-specific worktrees (wave-{W}-track-{id}) instead of permanent slots",
    )
    p_prep.add_argument(
        "--dry-run", action="store_true", help="Print worktree paths without git add"
    )

    # claim-track
    p_claim = sub.add_parser(
        "claim-track",
        help="Attach boundary locks to an existing worktree (e.g. Antigravity UI worktree)",
    )
    p_claim.add_argument("--repo", type=Path, default=Path.cwd(), help="Target repository root")
    p_claim.add_argument("--wave", type=int, required=True, help="Wave number")
    p_claim.add_argument("--track", type=str, required=True, help="Track ID (e.g. track-a)")
    p_claim.add_argument("--worktree", type=Path, required=True, help="Existing worktree path")
    p_claim.add_argument("--max-tracks", type=int, default=3, help="Max parallel tracks per wave")
    p_claim.add_argument("--kit", type=str, default="auto", help="SDD kit adapter")
    p_claim.add_argument("--headless", action="store_true", help="Enable Silent File-State Handoff")

    # verify-wave
    p_ver = sub.add_parser(
        "verify-wave",
        help="Verify that generated story specs in Wave K do not violate .parallel-lock.json",
    )
    p_ver.add_argument("--repo", type=Path, default=Path.cwd(), help="Target repository root")
    p_ver.add_argument("--wave", type=int, required=True, help="Wave number to verify")
    p_ver.add_argument("--max-tracks", type=int, default=3, help="Max parallel tracks per wave")
    p_ver.add_argument("--kit", type=str, default="auto", help="SDD kit adapter")
    p_ver.add_argument(
        "--dynamic-worktrees",
        action="store_true",
        help="Use dynamic wave-specific worktrees (wave-{W}-track-{id}) instead of permanent slots",
    )

    # merge
    p_merge = sub.add_parser(
        "merge",
        help="Barrier checkpoint merge for Wave K (3-way status merge + git merge --no-ff + gates)",
    )
    p_merge.add_argument("--repo", type=Path, default=Path.cwd(), help="Target repository root")
    p_merge.add_argument("--wave", type=int, required=True, help="Wave number to merge")
    p_merge.add_argument("--max-tracks", type=int, default=3, help="Max parallel tracks per wave")
    p_merge.add_argument("--kit", type=str, default="auto", help="SDD kit adapter")
    p_merge.add_argument(
        "--dynamic-worktrees",
        action="store_true",
        help="Use dynamic wave-specific worktrees (wave-{W}-track-{id}) instead of permanent slots",
    )
    p_merge.add_argument(
        "--skip-gates",
        action="store_true",
        help="Skip running checkpoint quality gates after merge",
    )
    p_merge.add_argument(
        "--strict-gates",
        action="store_true",
        help="Fail checkpoint gate even when 0 tests are collected in the repository",
    )
    p_merge.add_argument(
        "--cleanup",
        action="store_true",
        help="Remove finished track worktrees after successful merge",
    )
    p_merge.add_argument(
        "--dry-run",
        action="store_true",
        help="Print branches to be merged without modifying Git state",
    )

    # merge-status
    p_mstat = sub.add_parser(
        "merge-status", help="3-way deterministic merge for sprint-status.yaml"
    )
    p_mstat.add_argument("--base", type=Path, required=True, help="Base sprint-status.yaml")
    p_mstat.add_argument("--ours", type=Path, required=True, help="Ours sprint-status.yaml")
    p_mstat.add_argument("--theirs", type=Path, required=True, help="Theirs sprint-status.yaml")
    p_mstat.add_argument("--out", type=Path, required=True, help="Output merged YAML path")

    # check-lock
    p_lock = sub.add_parser("check-lock", help="Check if modifying a file is allowed in worktree")
    p_lock.add_argument("--file", type=str, required=True, help="Target file path")
    p_lock.add_argument("--tool", type=str, default="write_to_file", help="Tool name")

    # install-plugin
    p_inst = sub.add_parser(
        "install-plugin",
        help="Package and install portable sdd-parallel plugin for Google Antigravity",
    )
    p_inst.add_argument(
        "--target-dir",
        type=Path,
        default=None,
        help="Plugin output directory (defaults to ~/.gemini/config/plugins/sdd-parallel)",
    )

    # autopilot
    p_auto = sub.add_parser(
        "autopilot",
        help="Generate or execute Multi-Conversation Autopilot launch commands for Wave K",
    )
    p_auto.add_argument("--repo", type=Path, default=Path.cwd(), help="Target repository root")
    p_auto.add_argument("--wave", type=int, required=True, help="Wave number to launch")
    p_auto.add_argument("--max-tracks", type=int, default=3, help="Max parallel tracks per wave")
    p_auto.add_argument("--kit", type=str, default="auto", help="SDD kit adapter")
    p_auto.add_argument(
        "--harness",
        type=str,
        default="antigravity",
        choices=["antigravity", "claude-code", "codex"],
        help="Target AI coding harness",
    )
    p_auto.add_argument(
        "--antigravity-layout",
        action="store_true",
        help="Target worktrees in ~/.gemini/antigravity/worktrees/<Project>/<track>",
    )
    p_auto.add_argument(
        "--execute",
        action="store_true",
        help="Execute the generated launch commands via subprocess",
    )

    # abort-wave
    p_abort = sub.add_parser(
        "abort-wave",
        help="Abort wave before merge: remove worktrees, prune, and delete parallel branches",
    )
    p_abort.add_argument("--repo", type=Path, default=Path.cwd(), help="Target repository root")
    p_abort.add_argument("--wave", type=int, required=True, help="Wave number to abort")
    p_abort.add_argument(
        "--dynamic-worktrees",
        action="store_true",
        help="Use dynamic wave-specific worktrees (wave-{W}-track-{id}) instead of permanent slots",
    )

    # rollback-wave
    p_rb = sub.add_parser(
        "rollback-wave",
        help="Rollback merged wave in LIFO order with safety backup and status reset",
    )
    p_rb.add_argument("--repo", type=Path, default=Path.cwd(), help="Target repository root")
    p_rb.add_argument("--wave", type=int, required=True, help="Wave number to rollback")
    p_rb.add_argument(
        "--dynamic-worktrees",
        action="store_true",
        help="Use dynamic wave-specific worktrees (wave-{W}-track-{id}) instead of permanent slots",
    )
    p_rb.add_argument("--to-commit", type=str, default=None, help="Target commit hash to reset to")
    p_rb.add_argument(
        "--save-backup",
        action="store_true",
        default=True,
        help="Create safety backup branch before rollback",
    )
    p_rb.add_argument(
        "--no-backup",
        action="store_false",
        dest="save_backup",
        help="Skip creating safety backup branch",
    )
    p_rb.add_argument("--kit", type=str, default="auto", help="SDD kit adapter")

    # run-guide
    p_guide = sub.add_parser(
        "run-guide",
        help="Display operator run-guide with worktree paths and commands for Wave K",
    )
    p_guide.add_argument("--repo", type=Path, default=Path.cwd(), help="Target repository root")
    p_guide.add_argument("--wave", type=int, required=True, help="Wave number")
    p_guide.add_argument("--max-tracks", type=int, default=3, help="Max parallel tracks per wave")
    p_guide.add_argument("--kit", type=str, default="auto", help="SDD kit adapter")
    p_guide.add_argument(
        "--antigravity-layout",
        action="store_true",
        help="Show paths in ~/.gemini/antigravity/worktrees/<Project>/<track>",
    )

    # demo
    p_demo = sub.add_parser(
        "demo",
        help="Run standalone 5-second Merge-Bomb demonstration in a temporary repository",
    )
    p_demo.add_argument(
        "--kit",
        type=str,
        choices=["bmad", "spec-kit", "gsd", "openspec", "beads", "all"],
        default="bmad",
        help="Target SDD kit framework to demonstrate (or 'all' for complete 5-kit validation)",
    )
    p_demo.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep the temporary demonstration repository after completion",
    )
    p_demo.add_argument(
        "--scaffold-only",
        action="store_true",
        help=(
            "Generate the synthetic Git repository with SDD artifacts and hidden Merge-Bomb, "
            "then stop before Step 1 for manual E2E walkthrough"
        ),
    )
    p_demo.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Target directory for the demo repository (implies --keep-temp)",
    )

    return parser


def render_run_guide(
    repo_root: Path,
    plan: WavePlan,
    wave_number: int,
    antigravity_layout: bool = False,
) -> str:
    """Render operator instructions for Two-Stage Assisted wave execution."""
    target_wave = next((w for w in plan.waves if w.wave_number == wave_number), None)
    if target_wave is None:
        return f"Error: Wave {wave_number} not found in plan."

    lines: list[str] = [
        f"# 🧭 Two-Stage Assisted Operator Guide — Wave {wave_number} ({plan.sdd_kit.upper()})",
        "",
        "## 1. Worktree Tracks Execution Matrix",
        "",
        "| Track | Stories / Tasks | Effort | Worktree Directory | Worker Command |",
        "| :--- | :--- | :--- | :--- | :--- |",
    ]

    _, adapter = get_adapter(repo_root, kit=plan.sdd_kit)
    for track in target_wave.tracks:
        tasks_str = ", ".join(track.task_ids)
        if antigravity_layout:
            wt_path = (
                default_antigravity_worktrees_dir(repo_root.name)
                / f"wave-{wave_number}-{track.track_id}"
            )
        else:
            wt_path = repo_root / ".worktrees" / f"wave-{wave_number}-{track.track_id}"

        worker_cmd = adapter.render_worker_command(track)

        lines.append(
            f"| `{track.track_id}` | `{tasks_str}` | `{track.effort.value}` | "
            f"`{wt_path.as_posix()}` | {worker_cmd} |"
        )

    lines.extend(
        [
            "",
            "## 2. Operator Workflow Instructions",
            "",
            "1. **Open Windows:** Open each worktree in a separate Antigravity IDE window.",
            "2. **Launch Workers (Depth 0):** Run worker command in each window.",
            "3. **Verify Boundaries:** Run verification before barrier merge:",
            "   ```powershell",
            f"   sdd-parallel verify-wave --wave {wave_number}",
            "   ```",
            "4. **Barrier Checkpoint Merge:** After all workers pass quality gates:",
            "   ```powershell",
            f"   sdd-parallel merge --wave {wave_number} --cleanup",
            "   ```",
            "5. **Rollback & Safety (If Needed):**",
            f"   - Abort before merge: `sdd-parallel abort-wave --wave {wave_number}`",
            (
                "   - Rollback after merge: `sdd-parallel rollback-wave "
                f"--wave {wave_number} --save-backup`"
            ),
        ]
    )

    return "\n".join(lines)


def _compute_repo_plan(
    repo_root: Path,
    max_tracks: int,
    include_done: bool = False,
    kit: str = "auto",
    plan_source: str = "auto",
) -> tuple[BSPWaveScheduler, list[TaskNode], WavePlan]:
    kit_name, adapter = get_adapter(repo_root, kit=kit)
    scanner = ASTHotspotScanner(repo_root)
    meta_map = scanner.scan()
    file_roles = {rel: meta.role for rel, meta in meta_map.items()}
    crg = CRGSidecarAdapter(repo_root, ast_scanner=scanner)

    tasks = adapter.load_tasks(include_done=include_done, plan_source=plan_source)
    for task in tasks:
        if task.write_files and not task.impact_files:
            task.impact_files = sorted(crg.compute_impact_files(task.write_files))

    blockers = adapter.check_wave_zero_blockers()
    scheduler = BSPWaveScheduler(max_tracks=max_tracks, file_roles=file_roles)
    plan = scheduler.schedule(tasks, sdd_kit=kit_name, wave_zero_blockers=blockers)
    return scheduler, tasks, plan


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "plan":
        repo_root: Path = args.repo.resolve()
        scheduler, tasks, plan = _compute_repo_plan(
            repo_root=repo_root,
            max_tracks=args.max_tracks,
            include_done=args.include_done,
            kit=args.kit,
            plan_source=args.plan_source,
        )
        md_text = scheduler.render_markdown_plan(plan, tasks)
        if not args.dry_run:
            if args.output_dir is not None:
                out_dir = args.output_dir.resolve()
            else:
                _, adapter = get_adapter(repo_root, kit=plan.sdd_kit)
                out_dir = adapter.default_artifacts_dir()
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "execution-plan-parallel.md").write_text(md_text, encoding="utf-8")
            plan_dict = plan.to_dict()
            (out_dir / "wave-plan.json").write_text(
                json.dumps(plan_dict, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            # Monotonic history archive: .parallel/history/run-NNN-<timestamp>/wave-plan.json
            p_state = read_parallel_state(repo_root)
            run_id = int(p_state.get("run_counter", 0)) + 1
            p_state["run_counter"] = run_id
            write_parallel_state(repo_root, p_state)
            ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            hist_dir = repo_root / ".parallel" / "history" / f"run-{run_id:03d}-{ts}"
            hist_dir.mkdir(parents=True, exist_ok=True)
            (hist_dir / "wave-plan.json").write_text(
                json.dumps(plan_dict, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        for warn in diagnose_multi_kit_coexistence(repo_root, plan.sdd_kit):
            print(f"[NOTE] {warn}", file=sys.stderr)
        print(md_text)
        return 0

    if args.command == "prepare":
        repo_root = args.repo.resolve()
        _scheduler, _tasks, plan = _compute_repo_plan(
            repo_root=repo_root,
            max_tracks=args.max_tracks,
            kit=args.kit,
        )
        target_wave = next((w for w in plan.waves if w.wave_number == args.wave), None)
        if target_wave is None:
            print(f"Error: Wave {args.wave} not found in plan", file=sys.stderr)
            return 1

        # Pre-Flight Context Gate
        _, adapter = get_adapter(repo_root, kit=plan.sdd_kit)
        missing = adapter.check_preflight_context_gate(target_wave)
        if missing:
            print(
                "PRE-FLIGHT CONTEXT GATE BLOCKED: Missing pre-compiled epic context(s):",
                file=sys.stderr,
            )
            for path in missing:
                print(f"  - {path}", file=sys.stderr)
            print(
                "\nRecommendation: Compile the missing epic context file(s) on master "
                "(e.g. using compile-epic-context.md) before spawning parallel tracks "
                "to prevent fatal add/add git merge conflicts.",
                file=sys.stderr,
            )
            return 1

        layout = "antigravity" if args.antigravity_layout else "local"
        wm = WorktreeManager(
            repo_root,
            layout=layout,
            fixed_slots=not getattr(args, "dynamic_worktrees", False),
        )
        wt_paths = wm.prepare_wave(
            plan=plan,
            wave_number=args.wave,
            headless=args.headless,
            dry_run=args.dry_run,
        )
        print(
            json.dumps(
                {
                    "wave": args.wave,
                    "dry_run": args.dry_run,
                    "layout": layout,
                    "worktrees": [p.as_posix() for p in wt_paths],
                },
                indent=2,
            )
        )
        return 0

    if args.command == "claim-track":
        repo_root = args.repo.resolve()
        _scheduler, _tasks, plan = _compute_repo_plan(
            repo_root=repo_root,
            max_tracks=args.max_tracks,
            kit=args.kit,
        )
        wm = WorktreeManager(repo_root)
        lock = wm.claim_track(
            plan=plan,
            wave_number=args.wave,
            track_id=args.track,
            worktree_path=args.worktree.resolve(),
            headless=args.headless,
        )
        print(json.dumps(lock.to_dict(), indent=2, ensure_ascii=False))
        return 0

    if args.command == "verify-wave":
        repo_root = args.repo.resolve()
        _scheduler, _tasks, plan = _compute_repo_plan(
            repo_root=repo_root,
            max_tracks=args.max_tracks,
            kit=args.kit,
        )
        wm = WorktreeManager(
            repo_root,
            fixed_slots=not getattr(args, "dynamic_worktrees", False),
        )
        try:
            violations = wm.verify_wave(plan=plan, wave_number=args.wave)
        except (RuntimeError, ValueError, LIFORollbackViolationError) as exc:
            err_payload = {
                "status": "error",
                "command": args.command,
                "wave": args.wave,
                "error": str(exc),
            }
            print(
                json.dumps(err_payload, indent=2, ensure_ascii=False),
                file=sys.stderr,
            )
            return 2
        print(
            json.dumps(
                {
                    "wave": args.wave,
                    "compliant": len(violations) == 0,
                    "violations": violations,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0 if not violations else 2

    if args.command == "merge":
        repo_root = args.repo.resolve()
        _scheduler, _tasks, plan = _compute_repo_plan(
            repo_root=repo_root,
            max_tracks=args.max_tracks,
            kit=args.kit,
        )
        wm = WorktreeManager(
            repo_root,
            fixed_slots=not getattr(args, "dynamic_worktrees", False),
        )
        try:
            merged = wm.merge_wave(
                plan=plan,
                wave_number=args.wave,
                run_quality_gates=not args.skip_gates,
                cleanup_worktrees=args.cleanup,
                dry_run=args.dry_run,
                strict_gates=args.strict_gates,
            )
        except (RuntimeError, ValueError, LIFORollbackViolationError) as exc:
            err_payload = {
                "status": "error",
                "command": args.command,
                "wave": args.wave,
                "error": str(exc),
            }
            print(
                json.dumps(err_payload, indent=2, ensure_ascii=False),
                file=sys.stderr,
            )
            return 2
        print(
            json.dumps(
                {
                    "wave": args.wave,
                    "dry_run": args.dry_run,
                    "merged_branches": merged,
                },
                indent=2,
            )
        )
        return 0

    if args.command == "merge-status":
        merge_sprint_status_files(
            base_path=args.base,
            ours_path=args.ours,
            theirs_path=args.theirs,
            output_path=args.out,
        )
        print(f"Merged sprint-status.yaml written to {args.out}")
        return 0

    if args.command == "check-lock":
        decision = evaluate_tool_access(
            tool_name=args.tool,
            tool_input={"TargetFile": args.file},
        )
        print(json.dumps(decision.to_dict(), indent=2, ensure_ascii=False))
        return 0 if decision.decision == "allow" else 2

    if args.command == "install-plugin":
        manifest_paths = install_plugin(target_dir=args.target_dir)
        print(json.dumps(manifest_paths, indent=2, ensure_ascii=False))
        return 0

    if args.command == "autopilot":
        repo_root = args.repo.resolve()
        _scheduler, _tasks, plan = _compute_repo_plan(
            repo_root=repo_root,
            max_tracks=args.max_tracks,
            kit=args.kit,
        )
        if not any(w.wave_number == args.wave for w in plan.waves):
            available = [w.wave_number for w in plan.waves]
            err_msg = f"Wave {args.wave} not found in plan for repository {repo_root.as_posix()}."
            if not available:
                err_msg += (
                    " No active SDD tasks found in repository. "
                    "Use --repo <path> targeting an SDD repository (e.g. --repo /path/to/project)."
                )
            print(
                json.dumps(
                    {"error": err_msg, "available_waves": available},
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return 1
        launcher = AutopilotLauncher(repo_root)
        if getattr(args, "antigravity_layout", False):
            launcher.worktrees_dir = default_antigravity_worktrees_dir(repo_root.name)
        results = launcher.launch_wave(
            plan=plan,
            wave_number=args.wave,
            harness=args.harness,
            execute=args.execute,
        )
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return 0

    if args.command == "abort-wave":
        repo_root = args.repo.resolve()
        rm = WorktreeRollbackManager(
            repo_root,
            fixed_slots=not getattr(args, "dynamic_worktrees", False),
        )
        deleted = rm.abort_wave(wave_id=args.wave)
        print(
            json.dumps(
                {
                    "action": "abort-wave",
                    "wave": args.wave,
                    "deleted_branches": deleted,
                    "status": "aborted",
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0

    if args.command == "rollback-wave":
        repo_root = args.repo.resolve()
        rm = WorktreeRollbackManager(
            repo_root,
            fixed_slots=not getattr(args, "dynamic_worktrees", False),
        )
        try:
            backup_branch = rm.rollback_wave(
                wave_id=args.wave,
                save_backup=args.save_backup,
                to_commit=args.to_commit,
                kit=args.kit,
            )
        except (RuntimeError, ValueError, LIFORollbackViolationError) as exc:
            err_payload = {
                "status": "error",
                "command": args.command,
                "wave": args.wave,
                "error": str(exc),
            }
            print(
                json.dumps(err_payload, indent=2, ensure_ascii=False),
                file=sys.stderr,
            )
            return 2

        print(
            json.dumps(
                {
                    "action": "rollback-wave",
                    "wave": args.wave,
                    "backup_branch": backup_branch if args.save_backup else None,
                    "status": "rolled_back",
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0

    if args.command == "run-guide":
        repo_root = args.repo.resolve()
        _scheduler, _tasks, plan = _compute_repo_plan(
            repo_root=repo_root,
            max_tracks=args.max_tracks,
            kit=args.kit,
        )
        guide_md = render_run_guide(
            repo_root=repo_root,
            plan=plan,
            wave_number=args.wave,
            antigravity_layout=args.antigravity_layout,
        )
        print(guide_md)
        return 0

    if args.command == "demo":
        run_demo(
            keep_temp=args.keep_temp,
            kit=args.kit,
            scaffold_only=args.scaffold_only,
            output_dir=args.output_dir,
        )
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
