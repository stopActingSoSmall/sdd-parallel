"""Tests for automated historical benchmark CLI & confusion matrix engine."""

from __future__ import annotations

import io
import json
import subprocess
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from sdd_parallel.benchmark import (
    HistoricalBenchmarkResult,
    render_benchmark_report,
    run_demo_benchmark,
    run_git_benchmark,
)
from sdd_parallel.cli import main


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
