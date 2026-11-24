"""Tests for automated historical benchmark CLI & confusion matrix engine."""

from __future__ import annotations

import io
import json
import subprocess
import time
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from sdd_parallel.benchmark import (
    HistoricalBenchmarkResult,
    evaluate_task_pairs,
    render_benchmark_report,
    run_demo_benchmark,
    run_eight_class_collision_suite,
    run_git_benchmark,
)
from sdd_parallel.cli import main
from sdd_parallel.models import TaskNode
from sdd_parallel.scheduler import BSPWaveScheduler


def test_demo_benchmark_bmad_executes_under_reasonable_time() -> None:
    """Verify demo benchmark completes under 6.0s with 0 FN on BMAD (safe for CI)."""
    res = run_demo_benchmark(kit="bmad", max_tracks=3)
    assert isinstance(res, HistoricalBenchmarkResult)
    assert res.is_demo is True
    assert res.kit_name == "bmad"
    assert res.tasks_analyzed == 5
    assert res.waves_scheduled >= 2
    assert res.elapsed_seconds < 6.0

    # Accuracy checks
    assert res.metrics.true_positives >= 3
    assert res.metrics.false_negatives == 0
    assert res.metrics.precision == 1.0
    assert res.metrics.recall == 1.0
    assert res.metrics.f1_score == 1.0
    assert len(res.metrics.prevented_disasters) >= 3


def test_demo_benchmark_across_all_five_sdd_kits() -> None:
    """Verify demo benchmark works identically across all 5 SDD kit frameworks."""
    for kit in ["bmad", "spec-kit", "gsd", "openspec", "beads"]:
        res = run_demo_benchmark(kit=kit, max_tracks=3)
        assert res.metrics.false_negatives == 0, f"Kit {kit} had false negatives"
        assert res.metrics.true_positives >= 3
        assert res.metrics.recall == 1.0


def test_run_git_benchmark_direct() -> None:
    """Verify direct invocation of run_git_benchmark."""
    repo_root = Path(__file__).resolve().parent.parent
    check = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD~2"],
        cwd=repo_root,
        capture_output=True,
    )
    if check.returncode != 0:
        pytest.skip("Insufficient git commit depth (shallow clone)")
    res = run_git_benchmark(repo_root, since="HEAD~2")
    assert isinstance(res, HistoricalBenchmarkResult)
    assert res.is_demo is False
    assert res.metrics.false_negatives == 0



def test_render_benchmark_report_text_and_json() -> None:
    """Verify text table rendering and JSON serialization."""
    res = run_demo_benchmark(kit="bmad", max_tracks=3)

    text_rep = render_benchmark_report(res, format="text")
    assert "CONFUSION MATRIX & ACCURACY METRICS" in text_rep
    assert "True Positives  (TP) : 3" in text_rep
    assert "False Negatives (FN) : 0" in text_rep
    assert "Precision : 100.0%" in text_rep
    assert "Recall    : 100.0%" in text_rep

    json_rep = render_benchmark_report(res, format="json")
    data = json.loads(json_rep)
    assert data["is_demo"] is True
    assert data["metrics"]["true_positives"] == 3
    assert data["metrics"]["false_negatives"] == 0
    assert data["metrics"]["precision"] == 1.0
    assert len(data["evaluations"]) == 10


def test_cli_bench_demo_command_text(capsys) -> None:
    """Verify CLI `sdd-parallel bench --demo`."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        exit_code = main(["bench", "--demo"])

    assert exit_code == 0
    output = buf.getvalue()
    assert "SDD-PARALLEL HISTORICAL BENCHMARK" in output
    assert "CONFUSION MATRIX & ACCURACY METRICS" in output
    assert "Recall    : 100.0%" in output


def test_cli_bench_demo_command_json() -> None:
    """Verify CLI `sdd-parallel bench --demo --format json`."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        exit_code = main(["bench", "--demo", "--format", "json"])

    assert exit_code == 0
    data = json.loads(buf.getvalue())
    assert data["is_demo"] is True
    assert data["metrics"]["false_negatives"] == 0
    assert data["metrics"]["recall"] == 1.0


def test_cli_bench_git_range_on_current_repo() -> None:
    """Verify CLI `sdd-parallel bench --since HEAD~3` executes on git history."""
    repo_root = Path(__file__).resolve().parent.parent
    check = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD~3"],
        cwd=repo_root,
        capture_output=True,
    )
    if check.returncode != 0:
        pytest.skip("Insufficient git commit depth (shallow clone)")
    buf = io.StringIO()
    with redirect_stdout(buf):
        exit_code = main(["bench", "--repo", str(repo_root), "--since", "HEAD~3"])

    assert exit_code == 0
    output = buf.getvalue()
    assert "SDD-PARALLEL HISTORICAL BENCHMARK" in output
    assert "Target Repository" in output
    assert "CONFUSION MATRIX" in output


def test_evaluate_task_pairs_detects_spine_bucket_and_namespace_collisions() -> None:
    """Verify evaluate_task_pairs detects Class C (SPINE_BUCKET) and Class D (NAMESPACE)."""
    tasks = [
        TaskNode(
            id="T1",
            title="Migration 1",
            write_files=["db/migrations/001_a.sql"],
            namespace_tokens=["env:APP_MODE"],
        ),
        TaskNode(
            id="T2",
            title="Migration 2",
            write_files=["db/migrations/001_b.sql"],
        ),
        TaskNode(
            id="T3",
            title="Config consumer",
            write_files=["src/cfg.py"],
            namespace_tokens=["env:APP_MODE"],
        ),
    ]
    scheduler = BSPWaveScheduler(max_tracks=3)
    plan = scheduler.schedule(tasks, sdd_kit="bmad")
    metrics, _ = evaluate_task_pairs(tasks, plan, scheduler)

    assert metrics.false_negatives == 0
    assert metrics.true_positives == 2
    assert metrics.recall == 1.0


def test_eight_class_collision_suite_zero_fn() -> None:
    """Verify 8-class deterministic collision suite catches all A-H traps under 2.0s."""
    t0 = time.perf_counter()
    results = run_eight_class_collision_suite()
    elapsed = time.perf_counter() - t0

    assert elapsed < 2.0
    assert set(results.keys()) == {"A", "B", "C", "D", "E", "F", "G", "H"}
    assert all(results.values()), f"Missed collision classes: {results}"
