"""Tests for the interactive Wave Inspector module and CLI command."""

from __future__ import annotations

import json
from graphlib import TopologicalSorter
from pathlib import Path

import pytest

from sdd_parallel.cli import main
from sdd_parallel.inspector import (
    build_sample_wave_plan,
    generate_inspector_html,
    inspect_plan,
    save_inspector_html,
)
from sdd_parallel.models import (
    ConflictEdge,
    ConflictType,
    EffortTier,
    Wave,
    WavePlan,
    WaveTrack,
)


def test_build_sample_wave_plan() -> None:
    plan = build_sample_wave_plan()
    assert "waves" in plan
    assert "edges" in plan
    assert "spine_files" in plan
    assert "additive_registries" in plan
    assert len(plan["waves"]) >= 2
    assert len(plan["edges"]) >= 3

    # Check edge conflict types
    types = {e["conflict_type"] for e in plan["edges"]}
    assert "direct_collision" in types
    assert "semantic_collision" in types
    assert "spine_order" in types
    assert "explicit_dep" in types


def test_generate_inspector_html_offline_and_self_contained() -> None:
    html = generate_inspector_html()

    assert html.startswith("<!DOCTYPE html>")
    assert "SDD-Parallel Wave Inspector" in html
    assert '<svg id="dag-svg"' in html
    assert 'id="waves-container"' in html
    assert 'id="matrix-tbody"' in html
    assert '<script id="wave-plan-data" type="application/json">' in html

    # Strict offline guarantee: no remote scripts or stylesheets
    assert 'src="http' not in html
    assert 'href="http' not in html
    assert 'src="https' not in html
    assert 'href="https' not in html


def test_generate_inspector_html_with_wave_plan_dataclass() -> None:
    wave1 = Wave(
        wave_number=1,
        tracks=[
            WaveTrack(
                track_id="track-a",
                task_ids=["1-1"],
                effort=EffortTier.HIGH,
                exclusive_write_files=["src/core.py"],
            )
        ],
        checkpoint_gates=["pytest"],
    )
    edge = ConflictEdge(
        source="1-1",
        target="1-2",
        conflict_type=ConflictType.SEMANTIC_COLLISION,
        reason="Impact collision test",
        shared_files=("src/api.py",),
    )
    plan = WavePlan(
        sdd_kit="bmad",
        waves=[wave1],
        edges=[edge],
        spine_files=["src/schema.py"],
        additive_registries=["src/registry.py"],
    )

    html = generate_inspector_html(plan_data=plan, title="Custom Title Test")
    assert "Custom Title Test" in html
    assert "bmad" in html
    assert "src/core.py" in html
    assert "Impact collision test" in html


def test_save_inspector_html(tmp_path: Path) -> None:
    out_file = tmp_path / "subdir" / "inspector.html"
    saved = save_inspector_html(out_file)
    assert saved == out_file
    assert out_file.is_file()
    content = out_file.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in content
    assert len(content) > 1000


def test_inspect_plan_with_explicit_file(tmp_path: Path) -> None:
    plan_file = tmp_path / "custom-plan.json"
    sample_data = build_sample_wave_plan()
    sample_data["sdd_kit"] = "openspec"
    plan_file.write_text(json.dumps(sample_data), encoding="utf-8")

    out_file = tmp_path / "view.html"
    res = inspect_plan(
        repo_root=tmp_path,
        plan_path=plan_file,
        output_path=out_file,
        open_browser=False,
    )
    assert res == out_file
    assert out_file.is_file()
    text = out_file.read_text(encoding="utf-8")
    assert "openspec" in text


def test_inspect_plan_file_not_found(tmp_path: Path) -> None:
    missing_file = tmp_path / "non_existent.json"
    with pytest.raises(FileNotFoundError, match="not found"):
        inspect_plan(
            repo_root=tmp_path,
            plan_path=missing_file,
            open_browser=False,
        )


def test_inspect_plan_fallback_to_sample(tmp_path: Path) -> None:
    # Empty repo root, no wave-plan.json
    out_file = tmp_path / "fallback.html"
    res = inspect_plan(
        repo_root=tmp_path,
        output_path=out_file,
        open_browser=False,
        kit="gsd",
    )
    assert res == out_file
    assert out_file.is_file()
    text = out_file.read_text(encoding="utf-8")
    assert "gsd" in text


def test_cli_inspect_subcommand(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target_out = tmp_path / "cli_inspect.html"
    rc = main(["inspect", "--repo", str(tmp_path), "--output", str(target_out)])
    assert rc == 0
    captured = capsys.readouterr()
    assert "[SDD-Parallel] Wave Inspector generated at:" in captured.out
    assert target_out.is_file()


def test_sample_wave_plan_dag_is_strictly_acyclic() -> None:
    plan = build_sample_wave_plan()
    edges = plan["edges"]
    graph: dict[str, set[str]] = {}
    for e in edges:
        graph.setdefault(e["target"], set()).add(e["source"])
        graph.setdefault(e["source"], set())

    ts = TopologicalSorter(graph)
    order = list(ts.static_order())
    assert len(order) >= 4

    # All 4 conflict types present
    conflict_types = {e["conflict_type"] for e in edges}
    assert conflict_types == {
        "direct_collision",
        "semantic_collision",
        "spine_order",
        "explicit_dep",
    }


def test_inspector_html_v2_features() -> None:
    plan = build_sample_wave_plan()
    plan["wave_zero_blockers"] = ["Critical syntax error in spec.md"]
    html = generate_inspector_html(plan_data=plan)

    # 1. No raw LaTeX formulas in DAG legend
    assert "$W_A" not in html
    assert "$W_A \\cap W_B" not in html
    assert "$W_A \\cap I_B" not in html
    assert "Wₐ ∩ Wᵦ ≠ ∅" in html
    assert "Wₐ ∩ Iᵦ ≠ ∅" in html

    # 2. CLI Action Bridge & Pipeline strip
    assert "Deterministic SDD Cycle:" in html
    assert "pipeline-strip" in html
    assert "Copy: prepare --wave" in html
    assert "Copy: merge --wave" in html
    assert "copyCliCommand" in html

    # 3. Wave 0 Blockers & function scope notes
    assert "Wave 0 Blockers (pre-flight checks failed)" in html
    assert "Critical syntax error in spec.md" in html
    assert "register_normalizer_command" in html
    assert "fn-badge" in html

    # 4. Color semantics: shared vs spine
    assert ".file-pill.shared" in html
    assert ".file-pill.spine" in html
    assert "--accent-purple" in html

    # 5. Product-Led B2B Funnel
    assert "Parallel SDD Readiness &amp; Monorepo Blast-Radius Audit" in html
