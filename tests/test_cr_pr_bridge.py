"""Deterministic unit tests for scripts/cr_pr_bridge.py (0 network calls)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import cr_pr_bridge  # noqa: E402


def test_parse_coderabbit_critical_with_suggestion_and_ai_prompt() -> None:
    body = """
<!-- fingerprinting:phantom:poseidon:abcd -->
_⚠️ Potential issue_ | _🔴 Critical_

**LIFO rollback order can be skipped on Windows PermissionError.**

If `shutil.rmtree` fails mid-loop, remaining worktrees stay registered.

```suggestion
for wt in reversed(created_worktrees):
    _safe_remove_worktree(wt)
```

<details>
<summary>🤖 Prompt for AI Agents</summary>

```text
In src/sdd_parallel/worktree.py around line 412, iterate in reversed order and collect errors.
```

</details>
"""
    parsed = cr_pr_bridge.parse_coderabbit_comment_body(body)
    assert parsed["severity"] == "CRITICAL"
    assert parsed["bucket"] == "BLOCKER"
    assert "LIFO rollback order" in parsed["title"]
    assert "shutil.rmtree" in parsed["summary"]
    assert "<details>" not in parsed["summary"]
    assert "fingerprinting" not in parsed["summary"]
    assert parsed["suggested_diff"] == (
        "for wt in reversed(created_worktrees):\n    _safe_remove_worktree(wt)"
    )
    assert parsed["ai_prompt"] == (
        "In src/sdd_parallel/worktree.py around line 412, "
        "iterate in reversed order and collect errors."
    )


def test_parse_coderabbit_buckets_out_of_scope_and_diminishing_returns() -> None:
    nitpick_body = """
_🧹 Nitpick_

**Import order could be grouped by ruff.**

Consider sorting imports alphabetically for style consistency.
"""
    parsed_oos = cr_pr_bridge.parse_coderabbit_comment_body(nitpick_body)
    assert parsed_oos["severity"] == "NITPICK"
    assert parsed_oos["bucket"] == "OUT_OF_SCOPE"

    minor_body = """
_🟡 Minor_

**Consider caching regex compilation in critical section helper.**

Small performance optimization on rare fallback branch in critical section.
"""
    parsed_dr = cr_pr_bridge.parse_coderabbit_comment_body(minor_body)
    assert parsed_dr["severity"] == "MEDIUM"
    assert parsed_dr["bucket"] == "DIMINISHING_RETURNS"

    major_2026_body = """
**Major** · **Functional Correctness** · **Quick win**

**Build the audit head on the commit that removes the cluster files.**

Start the head at the synthetic base.
"""
    parsed_major = cr_pr_bridge.parse_coderabbit_comment_body(major_2026_body)
    assert parsed_major["severity"] == "HIGH"
    assert parsed_major["bucket"] == "BLOCKER"
    assert parsed_major["title"] == (
        "Build the audit head on the commit that removes the cluster files."
    )


def test_detect_whack_a_mole_on_round_2_plus() -> None:
    findings = [
        cr_pr_bridge.ReviewFinding(
            thread_id="T1",
            comment_id=101,
            path="src/sdd_parallel/ast_scanner.py",
            line=120,
            severity="HIGH",
            bucket="BLOCKER",
            title="Repeated heuristic gap",
            summary="Another dynamic call form",
            suggested_diff=None,
            ai_prompt=None,
            is_resolved=False,
        ),
        cr_pr_bridge.ReviewFinding(
            thread_id="T2",
            comment_id=102,
            path="src/sdd_parallel/worktree.py",
            line=50,
            severity="HIGH",
            bucket="BLOCKER",
            title="Already resolved",
            summary="Fixed",
            suggested_diff=None,
            ai_prompt=None,
            is_resolved=True,
        ),
    ]

    assert (
        cr_pr_bridge.detect_whack_a_mole(
            findings, ["src/sdd_parallel/ast_scanner.py"], round_num=1
        )
        == []
    )
    assert cr_pr_bridge.detect_whack_a_mole(
        findings,
        ["src\\sdd_parallel\\ast_scanner.py", "src/sdd_parallel/worktree.py"],
        round_num=2,
    ) == ["src/sdd_parallel/ast_scanner.py"]


def test_cluster_registry_disjoint_and_valid_files() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    assert set(cr_pr_bridge.CLUSTER_REGISTRY.keys()) == {1, 2, 3, 4, 5}

    seen_files: set[str] = set()
    for cid, spec in cr_pr_bridge.CLUSTER_REGISTRY.items():
        assert spec.id == cid
        assert spec.slug
        assert spec.lens_instructions.strip()
        assert len(spec.files) >= 3
        for rel_path in spec.files:
            assert (repo_root / rel_path).is_file(), f"Missing cluster file: {rel_path}"
            assert rel_path not in seen_files, f"Duplicate file across clusters: {rel_path}"
            seen_files.add(rel_path)


def test_merge_pr_and_sync_master_guards_and_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = cr_pr_bridge.GitHubPRClient(
        owner="stopActingSoSmall",
        repo="sdd-parallel",
        token="dummy-token",
        repo_root=tmp_path,
    )

    # 1. Fail-closed when CI failed or blockers remain
    failing_status = cr_pr_bridge.PRStatusReport(
        pr_number=48,
        head_branch="feat/demo",
        base_branch="master",
        head_sha="abc123",
        state="open",
        draft=False,
        mergeable_state="clean",
        ci_completed=True,
        ci_passed=False,
        cr_completed=True,
        round_num=1,
        checks=[],
        findings=[],
        unresolved_blockers=0,
        whack_a_mole_paths=[],
        ready_to_merge=False,
    )
    monkeypatch.setattr(client, "get_pr_status", lambda pr_number: failing_status)
    with pytest.raises(RuntimeError, match="CI checks not green"):
        client.merge_pr_and_sync_master(48)

    blocker_status = cr_pr_bridge.PRStatusReport(
        pr_number=48,
        head_branch="feat/demo",
        base_branch="master",
        head_sha="abc123",
        state="open",
        draft=False,
        mergeable_state="clean",
        ci_completed=True,
        ci_passed=True,
        cr_completed=True,
        round_num=1,
        checks=[],
        findings=[],
        unresolved_blockers=1,
        whack_a_mole_paths=[],
        ready_to_merge=False,
    )
    monkeypatch.setattr(client, "get_pr_status", lambda pr_number: blocker_status)
    with pytest.raises(RuntimeError, match="unresolved BLOCKER"):
        client.merge_pr_and_sync_master(48)

    # 2. Success path: squash merge, remote branch deletion, local master sync
    green_status = cr_pr_bridge.PRStatusReport(
        pr_number=48,
        head_branch="feat/demo",
        base_branch="master",
        head_sha="abc123",
        state="open",
        draft=False,
        mergeable_state="clean",
        ci_completed=True,
        ci_passed=True,
        cr_completed=True,
        round_num=1,
        checks=[],
        findings=[],
        unresolved_blockers=0,
        whack_a_mole_paths=[],
        ready_to_merge=True,
    )
    monkeypatch.setattr(client, "get_pr_status", lambda pr_number: green_status)

    rest_calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def fake_rest(
        method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        rest_calls.append((method, path, payload))
        if method == "PUT":
            return {"merged": True, "sha": "merged999"}
        return {}

    monkeypatch.setattr(client, "_rest", fake_rest)

    git_calls: list[list[str]] = []

    class FakeProc:
        def __init__(self, stdout: str = "") -> None:
            self.returncode = 0
            self.stdout = stdout
            self.stderr = ""

    def fake_run_git(
        args: list[str], cwd: Path, *, input_text: str | None = None, check: bool = True
    ) -> FakeProc:
        git_calls.append(list(args))
        if args[:2] == ["rev-parse", "--verify"]:
            return FakeProc("abc123\n")
        return FakeProc("")

    monkeypatch.setattr(cr_pr_bridge, "_run_git", fake_run_git)

    result = client.merge_pr_and_sync_master(48, method="squash", delete_branch=True)
    assert result["merged"] is True
    assert result["sha"] == "merged999"
    assert result["remote_branch_deleted"] is True
    assert result["local_master_synced"] is True
    assert (
        "PUT",
        "/repos/stopActingSoSmall/sdd-parallel/pulls/48/merge",
        {"merge_method": "squash", "sha": "abc123"},
    ) in rest_calls
    assert (
        "DELETE",
        "/repos/stopActingSoSmall/sdd-parallel/git/refs/heads/feat%2Fdemo",
        None,
    ) in rest_calls
    assert ["checkout", "master"] in git_calls
    assert ["pull", "--ff-only", "origin", "master"] in git_calls
    assert ["branch", "-D", "feat/demo"] in git_calls


def test_cli_audit_list_runs_offline(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cr_pr_bridge.main(["audit", "--action", "list"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert len(out["clusters"]) == 5


def test_run_all_clusters_audit_orchestration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    audit_dir = tmp_path / ".tmp" / "cr-audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    started_ids: list[int] = []
    harvested_ids: list[int] = []
    poll_counts: dict[int, int] = {}

    class DummyClient:
        def get_pr_status(self, pr_number: int) -> cr_pr_bridge.PRStatusReport:
            poll_counts[pr_number] = poll_counts.get(pr_number, 0) + 1
            return cr_pr_bridge.PRStatusReport(
                pr_number=pr_number,
                head_branch=f"audit/head-cluster-{pr_number - 100}",
                base_branch=f"audit/base-cluster-{pr_number - 100}",
                head_sha="sha123",
                state="open",
                draft=True,
                mergeable_state="clean",
                ci_completed=True,
                ci_passed=True,
                cr_completed=poll_counts[pr_number] >= 2,
                round_num=1,
                checks=[],
                findings=[],
                unresolved_blockers=0,
                whack_a_mole_paths=[],
                ready_to_merge=False,
            )

    def fake_prepare(
        cluster_id: int, repo_root: Path, client: Any = None
    ) -> dict[str, Any]:
        started_ids.append(cluster_id)
        return {"cluster_id": cluster_id, "pr_number": 100 + cluster_id}

    def fake_harvest(
        cluster_id: int,
        pr_number: int,
        repo_root: Path,
        client: Any = None,
        *,
        cleanup: bool = True,
    ) -> dict[str, Any]:
        assert cleanup is True
        harvested_ids.append(cluster_id)
        out_json = audit_dir / f"cluster-{cluster_id}-findings.json"
        out_json.write_text(
            json.dumps(
                {
                    "cluster_id": cluster_id,
                    "title": f"Cluster {cluster_id}",
                    "pr_number": pr_number,
                    "findings": [],
                }
            ),
            encoding="utf-8",
        )
        return {
            "cluster_id": cluster_id,
            "pr_number": pr_number,
            "cr_completed": True,
            "findings_count": 0,
            "blockers_count": 0,
            "cleaned_up": True,
        }

    monkeypatch.setattr(cr_pr_bridge, "prepare_cluster_audit_pr", fake_prepare)
    monkeypatch.setattr(cr_pr_bridge, "harvest_and_cleanup_cluster_audit", fake_harvest)

    summary = cr_pr_bridge.run_all_clusters_audit(
        tmp_path,
        client=DummyClient(),  # type: ignore[arg-type]
        clusters=(1, 2),
        poll_interval_sec=0,
        max_wait_sec=5,
        cooldown_sec=0,
    )
    assert started_ids == [1, 2]
    assert harvested_ids == [1, 2]
    assert poll_counts == {101: 2, 102: 2}
    assert (tmp_path / summary["report_path"]).is_file()
    assert (tmp_path / summary["log_path"]).is_file()


def test_request_json_retries_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    client = cr_pr_bridge.GitHubPRClient("owner", "repo", "tok")
    attempts = 0

    class DummyResp:
        def __enter__(self) -> DummyResp:
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def read(self) -> bytes:
            return b'{"ok": true}'

    def fake_urlopen(req: Any, timeout: int = 30) -> DummyResp:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise TimeoutError("The read operation timed out")
        return DummyResp()

    monkeypatch.setattr(cr_pr_bridge.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(cr_pr_bridge.time, "sleep", lambda _: None)

    res = client._request_json("GET", "https://api.github.com/rate_limit")
    assert res == {"ok": True}
    assert attempts == 3
