"""Interactive standalone HTML Wave Inspector UI for sdd-parallel.

Renders execution-plan-parallel.md, wave-plan.json, fixed track slots,
blast-radius locks, and DAG conflict graphs in an offline, zero-dependency HTML interface.
"""

from __future__ import annotations

import json
import webbrowser
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sdd_parallel.models import WavePlan


def build_sample_wave_plan() -> dict[str, Any]:
    """Construct a canonical sample WavePlan dictionary for fallback or demo visualizer."""
    return {
        "sdd_kit": "spec-kit",
        "is_demo": True,
        "wave_zero_blockers": [],
        "spine_files": ["src/demo_app/schema.py"],
        "additive_registries": [
            "src/demo_app/cli.py",
            "src/demo_app/__init__.py",
        ],
        "task_titles": {
            "T001": "Upgrade core telemetry schema",
            "T002": "Normalize telemetry payloads",
            "T003": "Add telemetry exporter",
            "T004": "Expose REST API endpoint",
            "T005": "Finalize schema v2 migration",
        },
        "waves": [
            {
                "wave_number": 1,
                "checkpoint_gates": [
                    "uv run pytest",
                    "uv run ruff check src/ tests/",
                    "uv run mypy --strict src/",
                ],
                "tracks": [
                    {
                        "track_id": "track-a",
                        "task_ids": ["T002"],
                        "effort": "high",
                        "exclusive_write_files": ["src/demo_app/normalizer.py"],
                        "additive_shared_files": ["src/demo_app/cli.py"],
                        "function_scope_notes": {
                            "src/demo_app/cli.py": "register_normalizer_command"
                        },
                    },
                    {
                        "track_id": "track-b",
                        "task_ids": ["T003"],
                        "effort": "medium",
                        "exclusive_write_files": ["src/demo_app/exporter.py"],
                        "additive_shared_files": ["src/demo_app/cli.py"],
                        "function_scope_notes": {
                            "src/demo_app/cli.py": "register_export_command"
                        },
                    },
                ],
            },
            {
                "wave_number": 2,
                "checkpoint_gates": [
                    "uv run pytest",
                    "uv run ruff check src/ tests/",
                    "uv run mypy --strict src/",
                ],
                "tracks": [
                    {
                        "track_id": "track-a",
                        "task_ids": ["T001"],
                        "effort": "medium",
                        "exclusive_write_files": ["src/demo_app/schema.py"],
                        "additive_shared_files": [],
                        "function_scope_notes": {},
                    },
                    {
                        "track_id": "track-b",
                        "task_ids": ["T004"],
                        "effort": "high",
                        "exclusive_write_files": ["src/demo_app/api.py"],
                        "additive_shared_files": [],
                        "function_scope_notes": {},
                    },
                ],
            },
            {
                "wave_number": 3,
                "checkpoint_gates": ["uv run pytest"],
                "tracks": [
                    {
                        "track_id": "track-a",
                        "task_ids": ["T005"],
                        "effort": "low",
                        "exclusive_write_files": ["src/demo_app/schema.py"],
                        "additive_shared_files": [],
                        "function_scope_notes": {},
                    },
                ],
            },
        ],
        "edges": [
            {
                "source": "T002",
                "target": "T004",
                "conflict_type": "semantic_collision",
                "reason": (
                    "Type-2 semantic collision: W(T002)={normalizer.py} intersects "
                    "I(T004)={api.py} via AST import"
                ),
                "shared_files": ["src/demo_app/api.py"],
                "directed": True,
            },
            {
                "source": "T001",
                "target": "T005",
                "conflict_type": "spine_order",
                "reason": "Serialized access to spine contract schema: src/demo_app/schema.py",
                "shared_files": ["src/demo_app/schema.py"],
                "directed": True,
            },
            {
                "source": "T002",
                "target": "T001",
                "conflict_type": "explicit_dep",
                "reason": "Explicit roadmap dependency declared in tasks.md",
                "shared_files": [],
                "directed": True,
            },
            {
                "source": "T003",
                "target": "T004",
                "conflict_type": "direct_collision",
                "reason": (
                    "Direct collision on shared specification document: "
                    "specs/001-telemetry/tasks.md"
                ),
                "shared_files": ["specs/001-telemetry/tasks.md"],
                "directed": True,
            },
        ],
    }


def _load_inspector_template() -> str:
    """Load standalone HTML template from package resources or local fallback."""
    try:
        import importlib.resources

        return (
            importlib.resources.files("sdd_parallel.resources")
            .joinpath("inspector_template.html")
            .read_text(encoding="utf-8")
        )
    except Exception:
        fallback = Path(__file__).resolve().parent / "resources" / "inspector_template.html"
        if fallback.is_file():
            return fallback.read_text(encoding="utf-8")
        raise FileNotFoundError("Could not locate inspector_template.html resource")


def generate_inspector_html(
    plan_data: dict[str, Any] | WavePlan | None = None,
    title: str = "SDD-Parallel Wave Inspector",
) -> str:
    """Generate self-contained, zero-dependency HTML for the interactive Wave Inspector."""
    if plan_data is None:
        raw_dict = build_sample_wave_plan()
    elif isinstance(plan_data, WavePlan):
        raw_dict = plan_data.to_dict()
    else:
        raw_dict = dict(plan_data)

    if "task_titles" not in raw_dict:
        extracted_titles: dict[str, str] = {}
        if "tasks" in raw_dict and isinstance(raw_dict["tasks"], list):
            for t in raw_dict["tasks"]:
                if isinstance(t, dict) and "id" in t and "title" in t and t["title"]:
                    extracted_titles[str(t["id"])] = str(t["title"])
        if extracted_titles:
            raw_dict["task_titles"] = extracted_titles

    serialized_json = json.dumps(raw_dict, indent=2, ensure_ascii=False)
    # Prevent closing script tag injection
    safe_json = serialized_json.replace("</script>", "<\\/script>")

    template = _load_inspector_template()
    return template.replace("__PAGE_TITLE__", title).replace("__PRELOADED_DATA__", safe_json)


def save_inspector_html(
    output_path: Path,
    plan_data: dict[str, Any] | WavePlan | None = None,
    title: str = "SDD-Parallel Wave Inspector",
) -> Path:
    """Serialize and write standalone Wave Inspector HTML to disk."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    content = generate_inspector_html(plan_data=plan_data, title=title)
    output_path.write_text(content, encoding="utf-8")
    return output_path


def inspect_plan(
    repo_root: Path,
    plan_path: Path | None = None,
    output_path: Path | None = None,
    open_browser: bool = False,
    kit: str = "auto",
) -> Path:
    """Inspect or generate interactive HTML viewer for the repository wave plan."""
    plan_dict: dict[str, Any] | None = None

    if plan_path is not None:
        if not plan_path.is_file():
            raise FileNotFoundError(f"Specified wave plan file not found: {plan_path}")
        raw_text = plan_path.read_text(encoding="utf-8")
        parsed = json.loads(raw_text)
        if isinstance(parsed, dict):
            plan_dict = parsed
    else:
        # Search canonical locations
        candidates: Sequence[Path] = (
            repo_root / ".parallel" / "artifacts" / "wave-plan.json",
            repo_root / ".parallel" / "wave-plan.json",
            repo_root / "wave-plan.json",
            repo_root / "_bmad-output" / "parallel-artifacts" / "wave-plan.json",
        )
        for cand in candidates:
            if cand.is_file():
                try:
                    loaded = json.loads(cand.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        plan_dict = loaded
                        break
                except Exception:
                    continue

    if plan_dict is None:
        # Fall back to sample demo plan
        plan_dict = build_sample_wave_plan()
        if kit != "auto":
            plan_dict["sdd_kit"] = kit

    target_out = (
        output_path
        if output_path is not None
        else repo_root / ".parallel" / "wave-inspector.html"
    )

    saved_path = save_inspector_html(
        target_out,
        plan_data=plan_dict,
        title=f"SDD-Parallel Wave Inspector — {repo_root.name}",
    )

    if open_browser:
        webbrowser.open(saved_path.resolve().as_uri())

    return saved_path
