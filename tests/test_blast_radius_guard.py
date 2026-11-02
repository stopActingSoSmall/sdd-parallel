"""Unit tests for PreToolUse blast-radius hook enforcement (.parallel-lock.json)."""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path
from typing import Any

from sdd_parallel.hooks.blast_radius_guard import (
    evaluate_tool_access,
    record_scope_escalation,
    run_stdin_hook,
)
from sdd_parallel.models import (
    EffortTier,
    LockManifest,
    TaskNode,
    TaskStatus,
    Wave,
    WavePlan,
    WaveTrack,
)
from sdd_parallel.scheduler import BSPWaveScheduler
from sdd_parallel.worktree import WorktreeManager


def _setup_worktree_lock(wt_root: Path) -> LockManifest:
    wt_root.mkdir(parents=True, exist_ok=True)
    lock = LockManifest(
        wave_id=1,
        track_id="track-a",
        stories=["11-2"],
        repo_root=wt_root.parent.as_posix(),
        worktree_root=wt_root.as_posix(),
        allowed_write_globs=[
            "src/agentlens/canonical/intervals.py",
            "src/agentlens/canonical/normalizer.py",
            "tests/**",
            "_bmad-output/implementation-artifacts/spec-11-2*.md",
        ],
        additive_shared_files=["src/agentlens/canonical/normalizer.py"],
        forbidden_files={
            "src/agentlens/canonical/manifest.py": "Locked by sibling track-b (story 11-3)",
            "src/agentlens/storage/schema.py": "Locked by Spine Chain invariant",
        },
    )
    (wt_root / ".parallel-lock.json").write_text(
        json.dumps(lock.to_dict(), indent=2),
        encoding="utf-8",
    )
    return lock


def test_hook_allows_read_only_tools(tmp_path: Path) -> None:
    wt_root = tmp_path / "wt-a"
    _setup_worktree_lock(wt_root)
    target = wt_root / "src/agentlens/canonical/manifest.py"

    decision = evaluate_tool_access(
        tool_name="view_file",
        tool_input={"AbsolutePath": str(target)},
        cwd=wt_root,
    )
    assert decision.decision == "allow"


def test_hook_allows_writes_within_allowed_scope(tmp_path: Path) -> None:
    wt_root = tmp_path / "wt-a"
    _setup_worktree_lock(wt_root)
    allowed_file = wt_root / "src/agentlens/canonical/intervals.py"
    allowed_test = wt_root / "tests/canonical/test_intervals.py"
    additive_file = wt_root / "src/agentlens/canonical/normalizer.py"

    d1 = evaluate_tool_access(
        tool_name="replace_file_content",
        tool_input={"TargetFile": str(allowed_file)},
        cwd=wt_root,
    )
    d2 = evaluate_tool_access(
        tool_name="write_to_file",
        tool_input={"TargetFile": str(allowed_test)},
        cwd=wt_root,
    )
    d3 = evaluate_tool_access(
        tool_name="replace_file_content",
        tool_input={"TargetFile": str(additive_file)},
        cwd=wt_root,
    )
    assert d1.decision == "allow"
    assert d1.reason is None
    assert d2.decision == "allow"
    assert d3.decision == "allow"
    assert d3.reason is not None
    assert "ADDITIVE REGISTRY MODE" in d3.reason


def test_hook_denies_forbidden_sibling_and_spine_files(tmp_path: Path) -> None:
    wt_root = tmp_path / "wt-a"
    _setup_worktree_lock(wt_root)
    sibling_file = wt_root / "src/agentlens/canonical/manifest.py"
    spine_file = wt_root / "src/agentlens/storage/schema.py"

    d_sibling = evaluate_tool_access(
        tool_name="replace_file_content",
        tool_input={"TargetFile": str(sibling_file)},
        cwd=wt_root,
    )
    d_spine = evaluate_tool_access(
        tool_name="write_to_file",
        tool_input={"TargetFile": str(spine_file)},
        cwd=wt_root,
    )
    assert d_sibling.decision == "deny"
    assert d_sibling.reason is not None
    assert "Locked by sibling track-b" in d_sibling.reason

    assert d_spine.decision == "deny"
    assert d_spine.reason is not None
    assert "Spine Chain" in d_spine.reason


def test_hook_denies_undeclared_file_outside_allowed_globs(tmp_path: Path) -> None:
    wt_root = tmp_path / "wt-a"
    _setup_worktree_lock(wt_root)
    random_file = wt_root / "src/agentlens/adapters/antigravity.py"

    decision = evaluate_tool_access(
        tool_name="replace_file_content",
        tool_input={"TargetFile": str(random_file)},
        cwd=wt_root,
    )
    assert decision.decision == "deny"
    assert decision.reason is not None
    assert "outside the declared Allowed Write Scope" in decision.reason


def test_stdin_hook_antigravity_protojson_format(
    tmp_path: Path,
    monkeypatch: Any,
    capsys: Any,
) -> None:
    wt_root = tmp_path / "wt-a"
    _setup_worktree_lock(wt_root)
    target_forbidden = wt_root / "src/agentlens/canonical/manifest.py"

    payload = {
        "conversationId": "test-uuid-1234",
        "workspacePaths": [str(wt_root)],
        "toolCall": {
            "name": "replace_file_content",
            "args": {
                "TargetFile": str(target_forbidden),
                "ReplacementContent": "foo",
            },
        },
    }

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    exit_code = run_stdin_hook()
    captured = capsys.readouterr()

    assert exit_code == 0
    resp = json.loads(captured.out)
    assert resp["decision"] == "deny"
    assert "Locked by sibling track-b" in resp["reason"]


def test_stdin_hook_empty_and_malformed_fails_open(
    monkeypatch: Any,
    capsys: Any,
) -> None:
    # Empty stdin
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    code_empty = run_stdin_hook()
    assert code_empty == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "allow"

    # Malformed JSON
    monkeypatch.setattr("sys.stdin", io.StringIO("not-valid-json{"))
    code_malformed = run_stdin_hook()
    assert code_malformed == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "allow"


def test_hook_blocks_writes_in_isolated_git_worktree(tmp_path: Path) -> None:
    def _git(*args: str, cwd: Path = tmp_path) -> None:

        subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )

    _git("init")
    _git("config", "user.email", "test@guard.local")
    _git("config", "user.name", "Guard Test")

    pkg_dir = tmp_path / "src" / "pkg"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "alpha.py").write_text("x = 1\n", encoding="utf-8")
    (pkg_dir / "beta.py").write_text("y = 1\n", encoding="utf-8")
    (pkg_dir / "spine.py").write_text("s = 1\n", encoding="utf-8")
    _git("add", ".")
    _git("commit", "-m", "init repo")

    tasks = [
        TaskNode(id="1-1", title="Alpha", write_files=["src/pkg/alpha.py"]),
        TaskNode(id="1-2", title="Beta", write_files=["src/pkg/beta.py"]),
        TaskNode(
            id="2-1",
            title="Spine",
            write_files=["src/pkg/spine.py"],
            depends_on=["1-1"],
        ),
    ]
    scheduler = BSPWaveScheduler(
        max_tracks=2,
        file_roles={"src/pkg/spine.py": "spine"},
    )
    plan = scheduler.schedule(tasks)

    wm = WorktreeManager(tmp_path)
    wt_paths = wm.prepare_wave(plan, wave_number=1, headless=True)
    assert len(wt_paths) == 2

    wt_a = wt_paths[0]
    # Check 100% clean git status and injected files
    proc = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=wt_a,
        check=True,
        capture_output=True,
        text=True,
    )
    assert proc.stdout.strip() == ""
    assert (wt_a / ".parallel-lock.json").is_file()
    assert (wt_a / ".tmp" / "track-context.md").is_file()
    assert (wt_a / "_bmad" / "custom" / "bmad-build-auto.toml").is_file()

    # 1. Deny write to sibling's exclusive file
    d_sibling = evaluate_tool_access(
        tool_name="replace_file_content",
        tool_input={"TargetFile": str(wt_a / "src" / "pkg" / "beta.py")},
        cwd=wt_a,
    )
    assert d_sibling.decision == "deny"
    assert "Locked by sibling track-b" in (d_sibling.reason or "")

    # 2. Deny write to spine file
    d_spine = evaluate_tool_access(
        tool_name="write_to_file",
        tool_input={"TargetFile": str(wt_a / "src" / "pkg" / "spine.py")},
        cwd=wt_a,
    )
    assert d_spine.decision == "deny"
    assert "Spine Chain" in (d_spine.reason or "")

    # 3. Deny write to unlisted file
    d_unlisted = evaluate_tool_access(
        tool_name="write_to_file",
        tool_input={"TargetFile": str(wt_a / "src" / "pkg" / "untracked.py")},
        cwd=wt_a,
    )
    assert d_unlisted.decision == "deny"
    assert "outside the declared Allowed Write Scope" in (d_unlisted.reason or "")

    # 4. Allow write to track's own exclusive file
    d_alpha = evaluate_tool_access(
        tool_name="replace_file_content",
        tool_input={"TargetFile": str(wt_a / "src" / "pkg" / "alpha.py")},
        cwd=wt_a,
    )
    assert d_alpha.decision == "allow"

    # 5. Allow write to tests/**
    d_test = evaluate_tool_access(
        tool_name="write_to_file",
        tool_input={"TargetFile": str(wt_a / "tests" / "test_alpha.py")},
        cwd=wt_a,
    )
    assert d_test.decision == "allow"


def test_terminal_guard_denies_bash_write_to_forbidden_files(tmp_path: Path) -> None:
    wt_root = tmp_path / "wt-a"
    _setup_worktree_lock(wt_root)

    # 1. Deny Python write to Spine file
    d_py_spine = evaluate_tool_access(
        tool_name="Bash",
        tool_input={"command": "python -c \"open('src/agentlens/storage/schema.py', 'w')\""},
        cwd=wt_root,
    )
    assert d_py_spine.decision == "deny"
    assert "Terminal Guard" in (d_py_spine.reason or "")
    assert "schema.py" in (d_py_spine.reason or "")

    # 2. Deny shell redirection to sibling's exclusive file
    d_redirect_sibling = evaluate_tool_access(
        tool_name="Bash",
        tool_input={"command": "echo 'hack' > src/agentlens/canonical/manifest.py"},
        cwd=wt_root,
    )
    assert d_redirect_sibling.decision == "deny"
    assert "Terminal Guard" in (d_redirect_sibling.reason or "")
    assert "manifest.py" in (d_redirect_sibling.reason or "")

    # 3. Deny rm command targeting forbidden file
    d_rm = evaluate_tool_access(
        tool_name="run_command",
        tool_input={"CommandLine": "rm src/agentlens/canonical/manifest.py"},
        cwd=wt_root,
    )
    assert d_rm.decision == "deny"

    # 4. Deny sed -i in-place editing of spine file
    d_sed = evaluate_tool_access(
        tool_name="Bash",
        tool_input={"command": "sed -i 's/foo/bar/g' src/agentlens/storage/schema.py"},
        cwd=wt_root,
    )
    assert d_sed.decision == "deny"

    # 5. Allow read-only commands targeting forbidden file
    d_cat = evaluate_tool_access(
        tool_name="Bash",
        tool_input={"command": "cat src/agentlens/storage/schema.py"},
        cwd=wt_root,
    )
    assert d_cat.decision == "allow"

    d_pytest = evaluate_tool_access(
        tool_name="run_command",
        tool_input={"CommandLine": "pytest tests/canonical/test_intervals.py"},
        cwd=wt_root,
    )
    assert d_pytest.decision == "allow"


def test_terminal_guard_denies_redirection_outside_allowed_scope(tmp_path: Path) -> None:
    wt_root = tmp_path / "wt-a"
    _setup_worktree_lock(wt_root)

    # Redirection to unlisted file outside allowed write globs
    d_out = evaluate_tool_access(
        tool_name="Bash",
        tool_input={"command": "echo 'hack' > unlisted_rogue_file.py"},
        cwd=wt_root,
    )
    assert d_out.decision == "deny"
    assert "outside declared Allowed Write Scope" in (d_out.reason or "")

    # Redirection to file within tests/** (allowed)
    d_test = evaluate_tool_access(
        tool_name="Bash",
        tool_input={"command": "echo 'def test_foo(): pass' > tests/test_new.py"},
        cwd=wt_root,
    )
    assert d_test.decision == "allow"


def test_record_scope_escalation_helper(tmp_path: Path) -> None:
    """Verify record_scope_escalation creates and appends valid JSON requests."""
    esc_file = record_scope_escalation(
        target_dir=tmp_path,
        story_id="15-3",
        requested_file="src/common/types.py",
        reason="Required new dataclass definition for API payload",
    )
    assert esc_file.is_file()
    data = json.loads(esc_file.read_text(encoding="utf-8"))
    assert data["story_id"] == "15-3"
    assert data["requested_file"] == "src/common/types.py"
    assert "Required new dataclass" in data["reason"]
    assert "timestamp" in data

    # Append second escalation
    record_scope_escalation(
        target_dir=tmp_path,
        story_id="15-4",
        requested_file="src/db/models.py",
        reason="Required new ORM table definition",
    )
    data2 = json.loads(esc_file.read_text(encoding="utf-8"))
    assert isinstance(data2, list)
    assert len(data2) == 2
    assert data2[1]["story_id"] == "15-4"


def test_evaluate_tool_access_auto_records_scope_escalation(tmp_path: Path) -> None:
    """Verify denied file modification automatically writes .tmp/scope-escalation.json."""
    wt_root = tmp_path / "wt-a"
    _setup_worktree_lock(wt_root)
    forbidden_target = wt_root / "src/agentlens/canonical/manifest.py"

    decision = evaluate_tool_access(
        tool_name="replace_file_content",
        tool_input={"TargetFile": str(forbidden_target)},
        cwd=wt_root,
        record_escalation=True,
    )
    assert decision.decision == "deny"

    esc_file = wt_root / ".tmp" / "scope-escalation.json"
    assert esc_file.is_file()
    esc_data = json.loads(esc_file.read_text(encoding="utf-8"))
    assert esc_data["story_id"] == "11-2"
    assert esc_data["requested_file"] == "src/agentlens/canonical/manifest.py"
    assert "BLAST RADIUS VIOLATION" in esc_data["reason"]


def test_verify_wave_handles_scope_escalation_and_defers_to_next_wave(tmp_path: Path) -> None:
    """DoD 3: Creation of .tmp/scope-escalation.json is handled by verify-wave
    and defers task to Wave K+1."""
    # 1. Initialize git repo
    subprocess.run(["git", "init", "-b", "master"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)

    # Initial commit
    (tmp_path / "README.md").write_text("# Test Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True)

    wm = WorktreeManager(repo_root=tmp_path, fixed_slots=True)

    # 2. Setup 2-track Wave 1
    track_a = WaveTrack(
        track_id="track-a",
        task_ids=["11-1"],
        effort=EffortTier.LOW,
        exclusive_write_files=["src/pkg/a.py"],
    )
    track_b = WaveTrack(
        track_id="track-b",
        task_ids=["11-2"],
        effort=EffortTier.MEDIUM,
        exclusive_write_files=["src/pkg/b.py"],
    )
    wave_1 = Wave(wave_number=1, tracks=[track_a, track_b])
    plan = WavePlan(
        sdd_kit="bmad",
        waves=[wave_1],
        edges=[],
    )

    # 3. Simulate escalation in track-b slot: requesting src/common/types.py
    wt_b = wm.worktree_path_for_track(1, "track-b")
    wt_b.mkdir(parents=True, exist_ok=True)
    record_scope_escalation(
        target_dir=wt_b,
        story_id="11-2",
        requested_file="src/common/types.py",
        reason="Required new dataclass definition for API payload",
    )

    # 4. Run verify_wave
    violations = wm.verify_wave(plan=plan, wave_number=1)

    # Violations must be empty (escalated task does not crash or fail wave)
    assert violations == []

    # Escalation was captured
    assert len(wm.last_escalations) == 1
    esc_item = wm.last_escalations[0]
    assert esc_item["story_id"] == "11-2"
    assert esc_item["requested_file"] == "src/common/types.py"
    assert esc_item["status"] == TaskStatus.ESCALATED_DEFERRED.value
    assert esc_item["deferred_to_wave"] == 2

    # Verify task 11-2 removed from Wave 1 and scheduled into Wave 2
    assert "11-2" not in wave_1.tracks[1].task_ids
    assert len(plan.waves) == 2
    wave_2 = plan.waves[1]
    assert wave_2.wave_number == 2
    assert any("11-2" in trk.task_ids for trk in wave_2.tracks)
    target_trk = next(trk for trk in wave_2.tracks if "11-2" in trk.task_ids)
    assert "src/common/types.py" in target_trk.exclusive_write_files
