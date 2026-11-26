"""Unit and integration tests for Pre-Merge AST Diff Gate (Classes B, F, G, H)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from sdd_parallel.ast_diff_gate import (
    check_dependency_manifest_collision,
    check_divergent_helper_duplication,
    check_signature_and_default_drift,
    check_virtual_import_cycles,
)
from sdd_parallel.ast_scanner import ASTHotspotScanner
from sdd_parallel.models import EffortTier, TaskNode, Wave, WavePlan, WaveTrack
from sdd_parallel.worktree import WorktreeManager


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test Runner"], cwd=root, check=True)


def _git_commit_all(root: Path, message: str) -> str:
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", message], cwd=root, check=True, capture_output=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_virtual_import_cycle_detected(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "src" / "pkg").mkdir(parents=True)
    (repo / "src" / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "src" / "pkg" / "mod_a.py").write_text("VALUE_A = 1\n", encoding="utf-8")
    (repo / "src" / "pkg" / "mod_b.py").write_text("VALUE_B = 2\n", encoding="utf-8")

    scanner = ASTHotspotScanner(repo)
    scanner.scan()

    wt_a = tmp_path / "wt_a"
    (wt_a / "src" / "pkg").mkdir(parents=True)
    (wt_a / "src" / "pkg" / "mod_a.py").write_text(
        "from pkg import mod_b\n\nVALUE_A = mod_b.VALUE_B + 1\n",
        encoding="utf-8",
    )

    wt_b = tmp_path / "wt_b"
    (wt_b / "src" / "pkg").mkdir(parents=True)
    (wt_b / "src" / "pkg" / "mod_b.py").write_text(
        "from pkg import mod_a\n\nVALUE_B = mod_a.VALUE_A + 2\n",
        encoding="utf-8",
    )

    findings = check_virtual_import_cycles(
        repo_root=repo,
        base_scanner=scanner,
        track_worktrees={"track-a": wt_a, "track-b": wt_b},
        track_changed_files={
            "track-a": ["src/pkg/mod_a.py"],
            "track-b": ["src/pkg/mod_b.py"],
        },
    )

    assert len(findings) == 1
    finding = findings[0]
    assert finding.category == "IMPORT_CYCLE"
    assert finding.severity == "ERROR"
    assert set(finding.tracks) == {"track-a", "track-b"}
    assert set(finding.files) == {"src/pkg/mod_a.py", "src/pkg/mod_b.py"}


def test_virtual_import_cycle_clean_dag(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "src" / "pkg").mkdir(parents=True)
    (repo / "src" / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "src" / "pkg" / "mod_a.py").write_text("VALUE_A = 1\n", encoding="utf-8")
    (repo / "src" / "pkg" / "mod_b.py").write_text("VALUE_B = 2\n", encoding="utf-8")

    scanner = ASTHotspotScanner(repo)
    scanner.scan()

    wt_a = tmp_path / "wt_a"
    (wt_a / "src" / "pkg").mkdir(parents=True)
    (wt_a / "src" / "pkg" / "mod_a.py").write_text(
        "from pkg import mod_b\n\nVALUE_A = mod_b.VALUE_B + 1\n",
        encoding="utf-8",
    )

    wt_b = tmp_path / "wt_b"
    (wt_b / "src" / "pkg").mkdir(parents=True)
    (wt_b / "src" / "pkg" / "mod_b.py").write_text(
        "VALUE_B = 42\n",
        encoding="utf-8",
    )

    findings = check_virtual_import_cycles(
        repo_root=repo,
        base_scanner=scanner,
        track_worktrees={"track-a": wt_a, "track-b": wt_b},
        track_changed_files={
            "track-a": ["src/pkg/mod_a.py"],
            "track-b": ["src/pkg/mod_b.py"],
        },
    )
    assert findings == []


def test_signature_and_default_drift_detected(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    _init_git_repo(repo)
    (repo / "src" / "core.py").write_text(
        "def filter_runs(min_steps: int = 1) -> list[str]:\n"
        "    return [str(min_steps)]\n",
        encoding="utf-8",
    )
    (repo / "src" / "consumer.py").write_text(
        "from src.core import filter_runs\n\n"
        "def run() -> list[str]:\n"
        "    return filter_runs()\n",
        encoding="utf-8",
    )
    base_sha = _git_commit_all(repo, "seed")

    wt_a = tmp_path / "wt_a"
    (wt_a / "src").mkdir(parents=True)
    (wt_a / "src" / "core.py").write_text(
        "def filter_runs(min_steps: int = 2) -> list[str]:\n"
        "    return [str(min_steps)]\n",
        encoding="utf-8",
    )

    wt_b = tmp_path / "wt_b"
    (wt_b / "src").mkdir(parents=True)
    (wt_b / "src" / "consumer.py").write_text(
        "from src.core import filter_runs\n\n"
        "def run() -> list[str]:\n"
        "    return [x.upper() for x in filter_runs()]\n",
        encoding="utf-8",
    )

    tracks = [
        WaveTrack(track_id="track-a", task_ids=["T1"], effort=EffortTier.LOW),
        WaveTrack(track_id="track-b", task_ids=["T2"], effort=EffortTier.LOW),
    ]
    tasks_by_id = {
        "T1": TaskNode(id="T1", title="Update filter", write_files=["src/core.py"]),
        "T2": TaskNode(
            id="T2",
            title="Consume filter",
            write_files=["src/consumer.py"],
            impact_files=["src/core.py"],
        ),
    }

    findings = check_signature_and_default_drift(
        repo_root=repo,
        merge_base_map={"track-a": base_sha, "track-b": base_sha},
        tracks=tracks,
        track_worktrees={"track-a": wt_a, "track-b": wt_b},
        track_changed_files={
            "track-a": ["src/core.py"],
            "track-b": ["src/consumer.py"],
        },
        tasks_by_id=tasks_by_id,
    )

    assert len(findings) == 1
    finding = findings[0]
    assert finding.category == "SIGNATURE_DEFAULT_DRIFT"
    assert finding.severity == "ERROR"
    assert finding.tracks == ("track-a", "track-b")
    assert finding.files == ("src/core.py",)
    assert "filter_runs" in finding.symbols


def test_divergent_helper_duplication_catches_uri_to_path_case(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    wt_a = tmp_path / "wt_a"
    (wt_a / "src").mkdir(parents=True)
    (wt_a / "src" / "alpha.py").write_text(
        "import urllib.parse\n\n"
        "def _uri_to_path(uri: str) -> str:\n"
        '    """Convert file URI to OS path."""\n'
        "    parsed = urllib.parse.urlparse(uri)\n"
        '    if parsed.scheme != "file":\n'
        "        return uri\n"
        "    path = urllib.parse.unquote(parsed.path)\n"
        '    if len(path) >= 3 and path[0] == "/" and path[2] == ":":\n'
        "        path = path[1:]\n"
        "    return path\n",
        encoding="utf-8",
    )

    wt_b = tmp_path / "wt_b"
    (wt_b / "src").mkdir(parents=True)
    (wt_b / "src" / "beta.py").write_text(
        "from urllib.parse import unquote, urlparse\n\n"
        "def _uri_to_workspace_path(raw_uri: str) -> str:\n"
        "    url = urlparse(raw_uri)\n"
        '    if url.scheme != "file":\n'
        "        return raw_uri\n"
        "    decoded = unquote(url.path)\n"
        '    if len(decoded) >= 3 and decoded[0] == "/" and decoded[2] == ":":\n'
        "        decoded = decoded[1:]\n"
        "    return decoded\n",
        encoding="utf-8",
    )

    findings = check_divergent_helper_duplication(
        repo_root=repo,
        merge_base_map={"track-a": "HEAD", "track-b": "HEAD"},
        track_worktrees={"track-a": wt_a, "track-b": wt_b},
        track_changed_files={
            "track-a": ["src/alpha.py"],
            "track-b": ["src/beta.py"],
        },
        similarity_threshold=0.75,
    )

    assert len(findings) == 1
    finding = findings[0]
    assert finding.category == "DIVERGENT_HELPER"
    assert finding.severity == "ERROR"
    assert set(finding.tracks) == {"track-a", "track-b"}
    assert set(finding.symbols) == {"_uri_to_path", "_uri_to_workspace_path"}
    assert "Semantic duplicate helper detected" in finding.message


def test_divergent_helper_ignores_distinct_functions(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    wt_a = tmp_path / "wt_a"
    (wt_a / "src").mkdir(parents=True)
    (wt_a / "src" / "parser.py").write_text(
        "import yaml\n\n"
        "def load_config_sections(raw_text: str) -> dict[str, str]:\n"
        "    loaded = yaml.safe_load(raw_text)\n"
        "    if not isinstance(loaded, dict):\n"
        "        return {}\n"
        "    result: dict[str, str] = {}\n"
        "    for key, val in loaded.items():\n"
        "        result[str(key).strip()] = str(val)\n"
        "    return result\n",
        encoding="utf-8",
    )

    wt_b = tmp_path / "wt_b"
    (wt_b / "src").mkdir(parents=True)
    (wt_b / "src" / "metrics.py").write_text(
        "import math\n\n"
        "def compute_rms_variance(samples: list[float]) -> float:\n"
        "    if not samples:\n"
        "        return 0.0\n"
        "    mean = sum(samples) / len(samples)\n"
        "    sq_diffs = [(val - mean) ** 2 for val in samples]\n"
        "    return math.sqrt(sum(sq_diffs) / len(samples))\n",
        encoding="utf-8",
    )

    findings = check_divergent_helper_duplication(
        repo_root=repo,
        merge_base_map={"track-a": "HEAD", "track-b": "HEAD"},
        track_worktrees={"track-a": wt_a, "track-b": wt_b},
        track_changed_files={
            "track-a": ["src/parser.py"],
            "track-b": ["src/metrics.py"],
        },
        similarity_threshold=0.75,
    )
    assert findings == []


def test_dependency_manifest_collision_detected() -> None:
    findings = check_dependency_manifest_collision(
        track_changed_files={
            "track-a": ["pyproject.toml", "src/a.py"],
            "track-b": ["pyproject.toml", "src/b.py"],
        }
    )

    assert len(findings) == 1
    finding = findings[0]
    assert finding.category == "DEPENDENCY_DRIFT"
    assert finding.severity == "ERROR"
    assert set(finding.tracks) == {"track-a", "track-b"}
    assert finding.files == ("pyproject.toml",)


def test_verify_wave_blocks_on_ast_diff_gate(tmp_path: Path) -> None:
    repo = tmp_path / "verify_ast_repo"
    repo.mkdir()
    _init_git_repo(repo)
    (repo / "src" / "pkg").mkdir(parents=True)
    (repo / "src" / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "src" / "pkg" / "mod_a.py").write_text("A = 1\n", encoding="utf-8")
    (repo / "src" / "pkg" / "mod_b.py").write_text("B = 2\n", encoding="utf-8")
    _git_commit_all(repo, "seed pkg")

    plan = WavePlan(
        sdd_kit="spec-kit",
        tasks=[
            TaskNode(
                id="T001",
                title="Mod A",
                write_files=["src/pkg/mod_a.py"],
            ),
            TaskNode(
                id="T002",
                title="Mod B",
                write_files=["src/pkg/mod_b.py"],
            ),
        ],
        waves=[
            Wave(
                wave_number=1,
                tracks=[
                    WaveTrack(
                        track_id="track-a",
                        task_ids=["T001"],
                        effort=EffortTier.LOW,
                        exclusive_write_files=["src/pkg/mod_a.py"],
                    ),
                    WaveTrack(
                        track_id="track-b",
                        task_ids=["T002"],
                        effort=EffortTier.LOW,
                        exclusive_write_files=["src/pkg/mod_b.py"],
                    ),
                ],
            )
        ],
        edges=[],
    )

    wm = WorktreeManager(repo, fixed_slots=True)
    wt_a, wt_b = wm.prepare_wave(plan, wave_number=1, headless=True)

    (wt_a / "src" / "pkg" / "mod_a.py").write_text(
        "from pkg import mod_b\n\n"
        "A = mod_b.B + 1\n\n"
        "def _uri_to_path(uri: str) -> str:\n"
        "    cleaned = uri.strip()\n"
        '    if cleaned.startswith("file://"):\n'
        "        cleaned = cleaned[7:]\n"
        "    return cleaned.replace('\\\\', '/')\n",
        encoding="utf-8",
    )
    _git_commit_all(wt_a, "track-a changes")

    (wt_b / "src" / "pkg" / "mod_b.py").write_text(
        "from pkg import mod_a\n\n"
        "B = mod_a.A + 2\n\n"
        "def _uri_to_workspace_path(raw_uri: str) -> str:\n"
        "    text = raw_uri.strip()\n"
        '    if text.startswith("file://"):\n'
        "        text = text[7:]\n"
        "    return text.replace('\\\\', '/')\n",
        encoding="utf-8",
    )
    _git_commit_all(wt_b, "track-b changes")

    violations = wm.verify_wave(plan, wave_number=1)
    assert any("[IMPORT_CYCLE]" in v for v in violations)
    assert any("[DIVERGENT_HELPER]" in v for v in violations)


def test_ast_diff_gate_flags_deleted_module_imported_by_sibling(tmp_path: Path) -> None:
    """Deleting an entire .py module in one track triggers SIGNATURE_DEFAULT_DRIFT for sibling."""
    repo = tmp_path / "repo"
    (repo / "src" / "pkg").mkdir(parents=True)
    _init_git_repo(repo)
    (repo / "src" / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "src" / "pkg" / "api.py").write_text(
        "def fetch_user(uid: int) -> str:\n"
        "    return str(uid)\n",
        encoding="utf-8",
    )
    (repo / "src" / "pkg" / "client.py").write_text(
        "from pkg.api import fetch_user\n\n"
        "def render_user(uid: int) -> str:\n"
        "    return fetch_user(uid)\n",
        encoding="utf-8",
    )
    base_sha = _git_commit_all(repo, "seed api and client")

    wt_a = tmp_path / "wt_a"
    (wt_a / "src" / "pkg").mkdir(parents=True)
    (wt_a / "src" / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    # src/pkg/api.py is deleted in track-a

    wt_b = tmp_path / "wt_b"
    (wt_b / "src" / "pkg").mkdir(parents=True)
    (wt_b / "src" / "pkg" / "client.py").write_text(
        "from pkg.api import fetch_user\n\n"
        "def render_user(uid: int) -> str:\n"
        "    return fetch_user(uid).upper()\n",
        encoding="utf-8",
    )

    tracks = [
        WaveTrack(track_id="track-a", task_ids=["T1"], effort=EffortTier.LOW),
        WaveTrack(track_id="track-b", task_ids=["T2"], effort=EffortTier.LOW),
    ]
    tasks_by_id = {
        "T1": TaskNode(id="T1", title="Remove legacy api", write_files=["src/pkg/api.py"]),
        "T2": TaskNode(id="T2", title="Update client", write_files=["src/pkg/client.py"]),
    }

    findings = check_signature_and_default_drift(
        repo_root=repo,
        merge_base_map={"track-a": base_sha, "track-b": base_sha},
        tracks=tracks,
        track_worktrees={"track-a": wt_a, "track-b": wt_b},
        track_changed_files={
            "track-a": ["src/pkg/api.py"],
            "track-b": ["src/pkg/client.py"],
        },
        tasks_by_id=tasks_by_id,
    )

    assert len(findings) == 1
    finding = findings[0]
    assert finding.category == "SIGNATURE_DEFAULT_DRIFT"
    assert finding.severity == "ERROR"
    assert finding.tracks == ("track-a", "track-b")
    assert finding.files == ("src/pkg/api.py",)
    assert "fetch_user" in finding.symbols
