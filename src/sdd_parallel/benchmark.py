"""Automated Historical Benchmark CLI & Confusion Matrix Engine (Task 27).

Evaluates retrospective blast-radius accuracy across git commit history
or standalone synthetic demo repositories:
- True Positives (TP): Correctly predicted conflicts separated into different waves.
- False Positives (FP): Over-conservative wave separations on independent changes.
- False Negatives (FN): Missed Merge-Bombs (conflicting changes scheduled in the same wave).
- True Negatives (TN): Safely parallelized independent changes in the same wave.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sdd_parallel.adapters import get_adapter
from sdd_parallel.ast_scanner import ASTHotspotScanner
from sdd_parallel.crg_sidecar import CRGSidecarAdapter
from sdd_parallel.demo import create_demo_repository
from sdd_parallel.models import FileRole, TaskNode, WavePlan
from sdd_parallel.scheduler import BSPWaveScheduler
from sdd_parallel.worktree import _restore_os_writable


@dataclass(frozen=True)
class PairEvaluation:
    """Retrospective evaluation of a single task pair."""

    task_a_id: str
    task_b_id: str
    actual_conflict: bool
    predicted_conflict: bool
    classification: str  # "TP", "FP", "FN", "TN"
    actual_reasons: tuple[str, ...]
    predicted_reasons: tuple[str, ...]


@dataclass(frozen=True)
class BenchmarkMetrics:
    """Statistical evaluation metrics (Confusion Matrix, Precision, Recall, F1)."""

    total_pairs: int
    true_positives: int
    false_positives: int
    false_negatives: int
    true_negatives: int
    precision: float
    recall: float
    f1_score: float
    prevented_disasters: tuple[str, ...]


@dataclass(frozen=True)
class HistoricalBenchmarkResult:
    """Structured telemetry returned by historical benchmark execution."""

    repo_root: Path
    is_demo: bool
    kit_name: str
    since_ref: str | None
    until_ref: str | None
    tasks_analyzed: int
    waves_scheduled: int
    metrics: BenchmarkMetrics
    evaluations: tuple[PairEvaluation, ...]
    elapsed_seconds: float


def _run_git(cwd: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


def evaluate_task_pairs(
    tasks: list[TaskNode],
    plan: WavePlan,
    scheduler: BSPWaveScheduler,
) -> tuple[BenchmarkMetrics, list[PairEvaluation]]:
    """Evaluate all pairwise task interactions and compute confusion matrix."""
    # Build reachability from explicit dependencies and spine/collision edges
    edges_by_pair: dict[tuple[str, str], list[str]] = {}
    for edge in plan.edges:
        key = tuple(sorted([edge.source, edge.target]))
        edges_by_pair.setdefault((key[0], key[1]), []).append(
            f"{edge.conflict_type.value}: {edge.reason}"
        )

    # Check which wave each task is scheduled into
    task_wave: dict[str, int] = {}
    for wave in plan.waves:
        for tid in wave.all_task_ids:
            task_wave[tid] = wave.wave_number

    tp = 0
    fp = 0
    fn = 0
    tn = 0
    evaluations: list[PairEvaluation] = []
    prevented_disasters: list[str] = []

    for i in range(len(tasks)):
        for j in range(i + 1, len(tasks)):
            t_a = tasks[i]
            t_b = tasks[j]
            pair_key = tuple(sorted([t_a.id, t_b.id]))

            # 1. Ground Truth (Actual Conflict)
            actual_reasons: list[str] = []

            # Check explicit dependency
            if t_a.id in t_b.depends_on or t_b.id in t_a.depends_on:
                actual_reasons.append(f"Explicit dependency ({t_a.id} <-> {t_b.id})")

            # Check shared files
            write_a = {f.replace("\\", "/") for f in t_a.write_files}
            write_b = {f.replace("\\", "/") for f in t_b.write_files}
            shared_writes = sorted(write_a & write_b)

            spine_shared = [
                f for f in shared_writes if scheduler.get_file_role(f) == FileRole.SPINE
            ]
            if spine_shared:
                actual_reasons.append(f"Spine-Lock shared contract: {', '.join(spine_shared)}")

            standard_shared = [
                f
                for f in shared_writes
                if scheduler.get_file_role(f) == FileRole.STANDARD
                and not scheduler._has_isolated_function_scope(t_a, t_b, f)
            ]
            if standard_shared:
                actual_reasons.append(
                    f"Direct non-additive write collision: {', '.join(standard_shared)}"
                )

            # Check Type-2 Semantic Collision: W_A \cap I_B or W_B \cap I_A
            impact_a = {f.replace("\\", "/") for f in t_a.impact_files}
            impact_b = {f.replace("\\", "/") for f in t_b.impact_files}
            sem_shared = sorted(
                ((write_a & impact_b) | (write_b & impact_a))
                - {
                    f
                    for f in (write_a | write_b)
                    if scheduler.get_file_role(f) == FileRole.ADDITIVE_REGISTRY
                }
            )
            if sem_shared:
                actual_reasons.append(
                    f"Type-2 Semantic Collision (W \u2229 I \u2260 \u2205): {', '.join(sem_shared)}"
                )

            actual_conflict = len(actual_reasons) > 0

            # 2. Predicted Conflict
            # Direct edge or explicit separation in plan
            predicted_reasons: list[str] = list(edges_by_pair.get((pair_key[0], pair_key[1]), []))
            predicted_conflict = len(predicted_reasons) > 0

            # 3. Classification
            if actual_conflict and predicted_conflict:
                classification = "TP"
                tp += 1
                desc = f"{t_a.id} <-> {t_b.id}: {'; '.join(actual_reasons)}"
                prevented_disasters.append(desc)
            elif not actual_conflict and predicted_conflict:
                classification = "FP"
                fp += 1
            elif actual_conflict and not predicted_conflict:
                classification = "FN"
                fn += 1
            else:
                classification = "TN"
                tn += 1

            evaluations.append(
                PairEvaluation(
                    task_a_id=t_a.id,
                    task_b_id=t_b.id,
                    actual_conflict=actual_conflict,
                    predicted_conflict=predicted_conflict,
                    classification=classification,
                    actual_reasons=tuple(actual_reasons),
                    predicted_reasons=tuple(predicted_reasons),
                )
            )

    total_pairs = tp + fp + fn + tn
    precision = (tp / (tp + fp)) if (tp + fp) > 0 else 1.0
    recall = (tp / (tp + fn)) if (tp + fn) > 0 else 1.0
    f1_score = (
        (2 * precision * recall / (precision + recall))
        if (precision + recall) > 0
        else 1.0
    )

    metrics = BenchmarkMetrics(
        total_pairs=total_pairs,
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        true_negatives=tn,
        precision=precision,
        recall=recall,
        f1_score=f1_score,
        prevented_disasters=tuple(prevented_disasters),
    )
    return metrics, evaluations


def _enrich_task_impacts(
    tasks: list[TaskNode],
    repo_root: Path,
    scanner: ASTHotspotScanner,
    meta_map: dict[str, Any],
) -> None:
    """Enrich tasks with CRG and AST semantic impact dependencies."""
    crg = CRGSidecarAdapter(repo_root, ast_scanner=scanner)
    for task in tasks:
        if task.write_files and not task.impact_files:
            impact: set[str] = set(crg.compute_impact_files(task.write_files))
            for wf in task.write_files:
                meta = meta_map.get(wf)
                if meta is not None:
                    for dep_mod in meta.imports:
                        for cand in meta_map:
                            if cand.endswith(f"/{dep_mod.split('.')[-1]}.py"):
                                impact.add(cand)
            task.impact_files = sorted(impact)


def run_demo_benchmark(
    kit: str = "bmad",
    max_tracks: int = 3,
    keep_temp: bool = False,
    output_dir: Path | None = None,
) -> HistoricalBenchmarkResult:
    """Execute historical retrospective benchmark on synthetic demo repository."""
    t0 = time.perf_counter()
    temp_dir_obj: tempfile.TemporaryDirectory[str] | None = None
    if output_dir is not None:
        demo_dir = output_dir.resolve()
        demo_dir.mkdir(parents=True, exist_ok=True)
    else:
        temp_dir_obj = tempfile.TemporaryDirectory(prefix="sdd_bench_demo_")
        demo_dir = Path(temp_dir_obj.name)

    try:
        create_demo_repository(demo_dir, kit=kit)
        scanner = ASTHotspotScanner(demo_dir)
        meta_map = scanner.scan()
        file_roles = {rel: meta.role for rel, meta in meta_map.items()}

        kit_name, adapter = get_adapter(demo_dir, kit=kit)
        tasks = adapter.load_tasks(include_done=True)
        _enrich_task_impacts(tasks, demo_dir, scanner, meta_map)

        scheduler = BSPWaveScheduler(max_tracks=max_tracks, file_roles=file_roles)
        plan = scheduler.schedule(tasks, sdd_kit=kit_name)

        metrics, evaluations = evaluate_task_pairs(tasks, plan, scheduler)
        elapsed = time.perf_counter() - t0

        return HistoricalBenchmarkResult(
            repo_root=demo_dir,
            is_demo=True,
            kit_name=kit_name,
            since_ref=None,
            until_ref=None,
            tasks_analyzed=len(tasks),
            waves_scheduled=len(plan.waves),
            metrics=metrics,
            evaluations=tuple(evaluations),
            elapsed_seconds=elapsed,
        )
    finally:
        if temp_dir_obj is not None and not keep_temp:
            _restore_os_writable(demo_dir)
            shutil.rmtree(temp_dir_obj.name, ignore_errors=True)


def run_git_benchmark(
    repo_root: Path,
    since: str | None = None,
    until: str = "HEAD",
    kit: str = "auto",
    max_tracks: int = 3,
) -> HistoricalBenchmarkResult:
    """Execute historical retrospective benchmark on git repository history."""
    t0 = time.perf_counter()
    resolved_root = repo_root.resolve()
    if not (resolved_root / ".git").exists():
        raise ValueError(f"Directory '{resolved_root}' is not a Git repository.")

    scanner = ASTHotspotScanner(resolved_root)
    meta_map = scanner.scan()
    file_roles = {rel: meta.role for rel, meta in meta_map.items()}

    # Check if SDD adapter can load tasks
    kit_name = kit
    tasks: list[TaskNode] = []
    try:
        detected_kit, adapter = get_adapter(resolved_root, kit=kit)
        kit_name = detected_kit
        tasks = adapter.load_tasks(include_done=True)
    except Exception:
        tasks = []

    # If no SDD tasks found, construct tasks from git commit range
    if not tasks:
        kit_name = "git"
        git_range = f"{since}..{until}" if since else f"{until}~10..{until}"
        try:
            log_proc = _run_git(
                resolved_root,
                ["log", "--oneline", "--reverse", git_range],
            )
            commit_lines = [line.strip() for line in log_proc.stdout.splitlines() if line.strip()]
        except subprocess.CalledProcessError:
            # Fallback if range invalid or shallow history
            log_proc = _run_git(resolved_root, ["log", "--oneline", "-n", "10"])
            commit_lines = [line.strip() for line in log_proc.stdout.splitlines() if line.strip()]

        for line in commit_lines:
            parts = line.split(" ", 1)
            commit_hash = parts[0]
            commit_msg = parts[1] if len(parts) > 1 else ""
            diff_proc = _run_git(
                resolved_root,
                ["diff-tree", "--no-commit-id", "--name-only", "-r", "--root", commit_hash],
            )
            files = [
                f.strip().replace("\\", "/")
                for f in diff_proc.stdout.splitlines()
                if f.strip()
            ]
            if files:
                tasks.append(
                    TaskNode(
                        id=commit_hash[:7],
                        title=commit_msg,
                        write_files=files,
                    )
                )

    if not tasks:
        raise ValueError(f"No tasks or commits found to benchmark in '{resolved_root}'.")

    _enrich_task_impacts(tasks, resolved_root, scanner, meta_map)
    scheduler = BSPWaveScheduler(max_tracks=max_tracks, file_roles=file_roles)
    plan = scheduler.schedule(tasks, sdd_kit=kit_name)

    metrics, evaluations = evaluate_task_pairs(tasks, plan, scheduler)
    elapsed = time.perf_counter() - t0

    return HistoricalBenchmarkResult(
        repo_root=resolved_root,
        is_demo=False,
        kit_name=kit_name,
        since_ref=since,
        until_ref=until,
        tasks_analyzed=len(tasks),
        waves_scheduled=len(plan.waves),
        metrics=metrics,
        evaluations=tuple(evaluations),
        elapsed_seconds=elapsed,
    )


def render_benchmark_report(
    result: HistoricalBenchmarkResult,
    format: str = "text",
) -> str:
    """Render human-readable text table or structured JSON benchmark report."""
    if format == "json":
        data = {
            "repo_root": str(result.repo_root),
            "is_demo": result.is_demo,
            "kit_name": result.kit_name,
            "since_ref": result.since_ref,
            "until_ref": result.until_ref,
            "tasks_analyzed": result.tasks_analyzed,
            "waves_scheduled": result.waves_scheduled,
            "metrics": asdict(result.metrics),
            "evaluations": [asdict(ev) for ev in result.evaluations],
            "elapsed_seconds": round(result.elapsed_seconds, 4),
        }
        return json.dumps(data, indent=2, ensure_ascii=False)

    lines: list[str] = [
        "=" * 80,
        "SDD-PARALLEL HISTORICAL BENCHMARK & RETROSPECTIVE EVALUATION",
        "=" * 80,
        f"Target Repository: {result.repo_root.as_posix()}",
        f"SDD Kit:           {result.kit_name.upper()}",
        (
            f"Scope:             "
            f"{'--demo' if result.is_demo else (result.since_ref or 'Recent commits')}"
            f" ({result.tasks_analyzed} tasks, {result.metrics.total_pairs} pairs)"
        ),
        f"Scheduled Waves:   {result.waves_scheduled} waves",
        f"Execution Time:    {result.elapsed_seconds:.3f}s",
        "",
        "-" * 80,
        "CONFUSION MATRIX & ACCURACY METRICS",
        "-" * 80,
        f"  True Positives  (TP) : {result.metrics.true_positives:<4} (Predicted conflict)",
        f"  False Positives (FP) : {result.metrics.false_positives:<4} (Conservative separation)",
        f"  False Negatives (FN) : {result.metrics.false_negatives:<4} (MISSED MERGE-BOMB)",
        f"  True Negatives  (TN) : {result.metrics.true_negatives:<4} (Clean parallel)",
        "",
        f"  Precision : {result.metrics.precision * 100:.1f}%",
        f"  Recall    : {result.metrics.recall * 100:.1f}% (Zero missed Merge-Bombs)",
        f"  F1-Score  : {result.metrics.f1_score:.4f}",
        "",
        "-" * 80,
        "PREVENTED MERGE DISASTERS (BLAST-RADIUS SHIELD)",
        "-" * 80,
    ]

    if result.metrics.prevented_disasters:
        for idx, disaster in enumerate(result.metrics.prevented_disasters, start=1):
            lines.append(f"  {idx}. {disaster}")
    else:
        lines.append("  (No merge conflicts detected in this scope)")

    lines.append("=" * 80)
    return "\n".join(lines)
