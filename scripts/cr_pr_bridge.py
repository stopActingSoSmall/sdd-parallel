#!/usr/bin/env python3
"""Autonomous GitHub PR & CodeRabbit Triage Bridge (Task 53).

Zero-dependency stdlib bridge over `git credential fill` and GitHub REST/GraphQL APIs:
1. Idempotent PR creation, CI + CodeRabbit status polling, and token-compressed thread parsing.
2. 3-round convergence triage with `WHACK_A_MOLE` detection.
3. Safe squash auto-merge + local `master` fast-forward synchronization.
4. 5-cluster retrospective audit orchestration via isolated Draft PRs.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GITHUB_API_BASE = "https://api.github.com"
GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"
CODERABBIT_LOGINS = frozenset({"coderabbitai[bot]", "coderabbitai"})

_FORMATTING_HINTS_RE = re.compile(
    r"\b(ruff|mypy|flake8|pylint|formatting|whitespace|line length|import order|"
    r"unused import|type annotation|trailing comma|docstring style)\b",
    re.IGNORECASE,
)
_SUGGESTION_BLOCK_RE = re.compile(r"```suggestion\r?\n(.*?)```", re.DOTALL)
_AI_PROMPT_DETAILS_RE = re.compile(
    r"<details>\s*<summary>[^<]*Prompt for AI Agents[^<]*</summary>(.*?)</details>",
    re.DOTALL | re.IGNORECASE,
)
_DETAILS_BLOCK_RE = re.compile(r"<details>.*?</details>", re.DOTALL | re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


@dataclass(frozen=True)
class ReviewFinding:
    """Normalized, token-compressed CodeRabbit review thread finding."""

    thread_id: str
    comment_id: int
    path: str
    line: int | None
    severity: str
    bucket: str
    title: str
    summary: str
    suggested_diff: str | None
    ai_prompt: str | None
    is_resolved: bool


@dataclass(frozen=True)
class PRStatusReport:
    """Consolidated PR CI, CodeRabbit completion, and triage readiness report."""

    pr_number: int
    head_branch: str
    base_branch: str
    head_sha: str
    state: str
    draft: bool
    mergeable_state: str
    ci_completed: bool
    ci_passed: bool
    cr_completed: bool
    round_num: int
    checks: list[dict[str, Any]]
    findings: list[ReviewFinding]
    unresolved_blockers: int
    whack_a_mole_paths: list[str]
    ready_to_merge: bool
    threads_verified: bool = True
    head_repo_matches_base: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Serialize report into a JSON-compatible dictionary."""
        data = asdict(self)
        data["findings"] = [asdict(f) for f in self.findings]
        return data


@dataclass(frozen=True)
class ClusterSpec:
    """Architectural cluster definition for retrospective CodeRabbit lens audits."""

    id: int
    slug: str
    title: str
    files: tuple[str, ...]
    lens_instructions: str


CLUSTER_REGISTRY: dict[int, ClusterSpec] = {
    1: ClusterSpec(
        id=1,
        slug="worktree_and_hooks",
        title="Git, Worktree, Rollback & Blast-Radius Hooks",
        files=(
            "src/sdd_parallel/worktree.py",
            "src/sdd_parallel/plugin_installer.py",
            "src/sdd_parallel/hooks/blast_radius_guard.py",
        ),
        lens_instructions=(
            "RETRO AUDIT LENS 1 (Transactions, OS Locks & Hook Security): "
            "Report ONLY Critical/High bugs in partial git worktree failures, "
            "WorktreeRollbackManager LIFO rollback violations, Windows PermissionError/Junction "
            "handling, .parallel-lock.json bypasses, path traversal, or silent fail-open "
            "in PreToolUse hooks."
        ),
    ),
    2: ClusterSpec(
        id=2,
        slug="ast_crg_scheduler",
        title="AST Scanner, Diff Gate, CRG Sidecar & Wave Scheduler",
        files=(
            "src/sdd_parallel/ast_scanner.py",
            "src/sdd_parallel/ast_diff_gate.py",
            "src/sdd_parallel/crg_sidecar.py",
            "src/sdd_parallel/scheduler.py",
            "src/sdd_parallel/models.py",
        ),
        lens_instructions=(
            "RETRO AUDIT LENS 2 (Graph Determinism & Fail-Closed AST Boundaries): "
            "Report ONLY Critical/High bugs in nondeterministic set iteration order, "
            "DAG cycle detection gaps, false fail-open paths in AST collision detection, "
            "unclosed SQLite connections in CRGSidecarAdapter, or invariant violations."
        ),
    ),
    3: ClusterSpec(
        id=3,
        slug="mergers_and_manifest",
        title="Monotonic 3-Way Mergers & Run Manifest",
        files=(
            "src/sdd_parallel/status_merger.py",
            "src/sdd_parallel/registry_merger.py",
            "src/sdd_parallel/manifest.py",
        ),
        lens_instructions=(
            "RETRO AUDIT LENS 3 (Data Consistency & Atomic Writes): "
            "Report ONLY Critical/High bugs causing status loss during 3-way merge, "
            "non-monotonic status regressions, unsafe AST merging of non-additive edits "
            "in registry_merger, or partial file corruption without atomic replacement."
        ),
    ),
    4: ClusterSpec(
        id=4,
        slug="sdd_adapters",
        title="SDD Kit Adapters & Read-Only ACL Boundary",
        files=(
            "src/sdd_parallel/adapters/__init__.py",
            "src/sdd_parallel/adapters/bmad.py",
            "src/sdd_parallel/adapters/openspec.py",
            "src/sdd_parallel/adapters/custom.py",
            "src/sdd_parallel/adapters/spec_kit.py",
            "src/sdd_parallel/adapters/gsd_hijack.py",
            "src/sdd_parallel/adapters/beads.py",
        ),
        lens_instructions=(
            "RETRO AUDIT LENS 4 (Read-Only ACL & Parser Robustness): "
            "Report ONLY Critical/High bugs where adapters mutate upstream kit files outside "
            "explicit merge hooks, catastrophic regex backtracking (ReDoS), task ID collisions "
            "across specs, or Scoped ID Rollback mismatches."
        ),
    ),
    5: ClusterSpec(
        id=5,
        slug="runner_and_cli",
        title="Depth-0 Runner, Autopilot, CLI & Inspector",
        files=(
            "src/sdd_parallel/runner.py",
            "src/sdd_parallel/autopilot.py",
            "src/sdd_parallel/cli.py",
            "src/sdd_parallel/inspector.py",
        ),
        lens_instructions=(
            "RETRO AUDIT LENS 5 (Subprocess Lifecycle, Deadlocks & CLI Contracts): "
            "Report ONLY Critical/High bugs causing zombie subprocesses on timeout, "
            "Windows stdout/stderr pipe deadlocks, violations of the Freeze Invariant "
            "on verdict.is_qualified, or incorrect CLI fail-closed exit codes."
        ),
    ),
}


def _run_git(
    args: Sequence[str],
    cwd: Path,
    *,
    input_text: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a git command with explicit UTF-8 decoding."""
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def get_github_token(repo_root: Path | None = None) -> str:
    """Resolve GitHub token from environment or system `git credential fill`."""
    for env_var in ("GITHUB_TOKEN", "GH_TOKEN"):
        val = os.environ.get(env_var, "").strip()
        if val:
            return val

    cwd = repo_root or Path.cwd()
    proc = _run_git(
        ["credential", "fill"],
        cwd=cwd,
        input_text="protocol=https\nhost=github.com\n\n",
        check=False,
    )
    if proc.returncode == 0:
        for line in proc.stdout.splitlines():
            if line.startswith("password="):
                token = line.split("=", 1)[1].strip()
                if token:
                    return token

    raise RuntimeError(
        "GitHub token not found in GITHUB_TOKEN/GH_TOKEN or `git credential fill`."
    )


def parse_origin_repo(repo_root: Path) -> tuple[str, str]:
    """Parse `(owner, repo)` from the `origin` remote URL."""
    proc = _run_git(["remote", "get-url", "origin"], cwd=repo_root, check=True)
    url = proc.stdout.strip()

    # Match https://github.com/owner/repo(.git) or git@github.com:owner/repo(.git)
    match = re.search(
        r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$",
        url,
    )
    if not match:
        raise ValueError(f"Unsupported GitHub origin remote URL: {url!r}")
    return match.group("owner"), match.group("repo")


_SEVERITY_BADGE_LINE_RE = re.compile(
    r"^(?:_⚠️\s*Potential issue_|_🧹\s*Nitpick_|"
    r"\*\*(?:Critical|Major|High|Minor|Medium|Trivial|Low|Nitpick)\*\*\s*·)",
    re.IGNORECASE,
)


def _extract_header_severity(header_lines: Sequence[str]) -> str:
    """Parse severity strictly from the finding header line(s), never from body prose."""
    header = " ".join(header_lines[:2]).lower()
    if (
        "🔴 critical" in header
        or "_critical_" in header
        or "**critical**" in header
        or "[blocker]" in header
        or re.search(r"\bseverity\s*:\s*critical\b", header)
    ):
        return "CRITICAL"
    if (
        "🟠 major" in header
        or "_major_" in header
        or "**major**" in header
        or "_high_" in header
        or "**high**" in header
        or re.search(r"\bseverity\s*:\s*(?:major|high)\b", header)
    ):
        return "HIGH"
    if (
        "🧹 nitpick" in header
        or "_nitpick_" in header
        or "**nitpick**" in header
        or re.search(r"\bseverity\s*:\s*nitpick\b", header)
    ):
        return "NITPICK"
    if (
        "🔵 trivial" in header
        or "_trivial_" in header
        or "**trivial**" in header
        or "_low_" in header
        or "**low**" in header
        or re.search(r"\bseverity\s*:\s*(?:trivial|low)\b", header)
    ):
        return "LOW"
    if (
        "🟡 minor" in header
        or "_minor_" in header
        or "**minor**" in header
        or "_medium_" in header
        or "**medium**" in header
        or re.search(r"\bseverity\s*:\s*(?:minor|medium)\b", header)
    ):
        return "MEDIUM"
    return "MEDIUM"


def parse_coderabbit_comment_body(body: str) -> dict[str, Any]:
    """Extract severity, triage bucket, suggestion diff, AI prompt, and clean text."""
    text = body.strip()
    no_comments = _HTML_COMMENT_RE.sub("", text).strip()
    raw_non_empty = [ln.strip() for ln in no_comments.splitlines() if ln.strip()]
    severity = _extract_header_severity(raw_non_empty)

    suggestion_match = _SUGGESTION_BLOCK_RE.search(text)
    suggested_diff = suggestion_match.group(1).strip("\r\n") if suggestion_match else None

    ai_prompt_match = _AI_PROMPT_DETAILS_RE.search(text)
    ai_prompt: str | None = None
    if ai_prompt_match:
        raw_prompt = ai_prompt_match.group(1).strip()
        fenced = re.search(r"```[a-zA-Z0-9_-]*\r?\n(.*?)```", raw_prompt, re.DOTALL)
        ai_prompt = (fenced.group(1) if fenced else raw_prompt).strip()

    cleaned = _DETAILS_BLOCK_RE.sub("", no_comments)
    cleaned = _SUGGESTION_BLOCK_RE.sub("", cleaned)
    cleaned = _HTML_TAG_RE.sub("", cleaned)

    lines: list[str] = []
    for raw_line in cleaned.splitlines():
        s = raw_line.strip()
        if not s or s.startswith("<!--"):
            continue
        if _SEVERITY_BADGE_LINE_RE.match(s):
            continue
        lines.append(s)

    title = lines[0].strip("*# ").strip() if lines else "CodeRabbit finding"
    if len(title) > 120:
        title = title[:117] + "..."
    summary = "\n".join(lines[1:] if len(lines) > 1 else lines).strip()
    if len(summary) > 1200:
        summary = summary[:1197] + "..."

    if severity in {"CRITICAL", "HIGH"}:
        bucket = "BLOCKER"
    elif _FORMATTING_HINTS_RE.search(f"{title}\n{summary}"):
        bucket = "OUT_OF_SCOPE"
    else:
        bucket = "DIMINISHING_RETURNS"

    return {
        "severity": severity,
        "bucket": bucket,
        "title": title,
        "summary": summary,
        "suggested_diff": suggested_diff,
        "ai_prompt": ai_prompt,
    }


def detect_whack_a_mole(
    findings: Sequence[ReviewFinding],
    previous_paths: Sequence[str],
    round_num: int,
) -> list[str]:
    """Detect repeated unresolved issues targeting previously edited files on round >= 2."""
    if round_num < 2 or not previous_paths:
        return []

    prev_set = {p.replace("\\", "/").strip() for p in previous_paths if p.strip()}
    repeated: set[str] = set()
    for finding in findings:
        if finding.is_resolved:
            continue
        norm_path = finding.path.replace("\\", "/").strip()
        if norm_path in prev_set:
            repeated.add(norm_path)
    return sorted(repeated)


class GitHubPRClient:
    """Thin GitHub REST + GraphQL API client using stdlib `urllib`."""

    def __init__(
        self,
        owner: str,
        repo: str,
        token: str,
        repo_root: Path | None = None,
    ) -> None:
        self.owner = owner
        self.repo = repo
        self.token = token
        self.repo_root = repo_root

    def _request_json(
        self,
        method: str,
        url: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "sdd-parallel-cr-pr-bridge/1.0",
        }
        data: bytes | None = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        retry_delays = (5, 15, 30)
        last_exc: Exception | None = None
        for attempt in range(len(retry_delays) + 1):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    raw = resp.read().decode("utf-8")
                    return json.loads(raw) if raw.strip() else {}
            except urllib.error.HTTPError as exc:
                err_body = exc.read().decode("utf-8", errors="replace")
                if exc.code in {502, 503, 504} and attempt < len(retry_delays):
                    last_exc = RuntimeError(
                        f"GitHub API {method} {url} failed ({exc.code}): {err_body}"
                    )
                    time.sleep(retry_delays[attempt])
                    continue
                raise RuntimeError(
                    f"GitHub API {method} {url} failed ({exc.code}): {err_body}"
                ) from exc
            except (TimeoutError, urllib.error.URLError, OSError) as exc:
                last_exc = exc
                if attempt < len(retry_delays):
                    time.sleep(retry_delays[attempt])
                    continue
                raise RuntimeError(
                    f"GitHub API {method} {url} network error after retries: {exc}"
                ) from exc
        raise RuntimeError(f"GitHub API {method} {url} failed: {last_exc}")

    def _rest(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{GITHUB_API_BASE}{path}"
        return self._request_json(method, url, payload)

    def _graphql(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        res = self._request_json(
            "POST",
            GITHUB_GRAPHQL_URL,
            {"query": query, "variables": variables or {}},
        )
        if not isinstance(res, dict):
            raise RuntimeError("Unexpected GraphQL response type")
        if res.get("errors"):
            raise RuntimeError(f"GitHub GraphQL errors: {res['errors']}")
        return res

    def find_open_pr(
        self, head_branch: str, base_branch: str | None = None
    ) -> dict[str, Any] | None:
        """Find an open PR for `head_branch` (optionally filtered by `base_branch`)."""
        params: dict[str, str] = {
            "state": "open",
            "head": f"{self.owner}:{head_branch}",
            "per_page": "20",
        }
        if base_branch:
            params["base"] = base_branch
        qs = urllib.parse.urlencode(params)
        prs = self._rest("GET", f"/repos/{self.owner}/{self.repo}/pulls?{qs}")
        if isinstance(prs, list) and prs:
            first = prs[0]
            if isinstance(first, dict):
                return first
        return None

    def ensure_pr(
        self,
        head_branch: str,
        base_branch: str = "master",
        title: str | None = None,
        body: str = "",
        draft: bool = False,
    ) -> dict[str, Any]:
        """Return existing open PR for `head_branch -> base_branch`, or create one."""
        existing = self.find_open_pr(head_branch, base_branch=base_branch)
        if existing is not None:
            return existing

        resolved_title = title
        if not resolved_title:
            if self.repo_root is not None:
                proc = _run_git(["log", "-1", "--pretty=%s"], cwd=self.repo_root, check=False)
                if proc.returncode == 0 and proc.stdout.strip():
                    resolved_title = proc.stdout.strip()
            if not resolved_title:
                resolved_title = head_branch

        payload = {
            "title": resolved_title,
            "head": head_branch,
            "base": base_branch,
            "body": body,
            "draft": draft,
        }
        created = self._rest("POST", f"/repos/{self.owner}/{self.repo}/pulls", payload)
        if not isinstance(created, dict):
            raise RuntimeError("Unexpected response when creating PR")
        return created

    def _fetch_review_threads_graphql(self, pr_number: int) -> list[ReviewFinding]:
        query = """
        query($owner: String!, $repo: String!, $pr: Int!, $cursor: String) {
          repository(owner: $owner, name: $repo) {
            pullRequest(number: $pr) {
              reviewThreads(first: 100, after: $cursor) {
                pageInfo {
                  hasNextPage
                  endCursor
                }
                nodes {
                  id
                  isResolved
                  path
                  line
                  originalLine
                  comments(first: 20) {
                    nodes {
                      databaseId
                      body
                      author {
                        login
                      }
                    }
                  }
                }
              }
            }
          }
        }
        """
        findings: list[ReviewFinding] = []
        cursor: str | None = None

        while True:
            res = self._graphql(
                query,
                {
                    "owner": self.owner,
                    "repo": self.repo,
                    "pr": pr_number,
                    "cursor": cursor,
                },
            )
            rt = (
                res.get("data", {})
                .get("repository", {})
                .get("pullRequest", {})
                .get("reviewThreads", {})
            )
            for node in rt.get("nodes", []):
                comments = node.get("comments", {}).get("nodes", [])
                cr_comment = next(
                    (
                        c
                        for c in comments
                        if str((c.get("author") or {}).get("login", "")).lower()
                        in {"coderabbitai", "coderabbitai[bot]"}
                    ),
                    None,
                )
                if cr_comment is None:
                    continue
                parsed = parse_coderabbit_comment_body(str(cr_comment.get("body", "")))
                findings.append(
                    ReviewFinding(
                        thread_id=str(node.get("id", "")),
                        comment_id=int(cr_comment.get("databaseId") or 0),
                        path=str(node.get("path") or ""),
                        line=node.get("line") or node.get("originalLine"),
                        severity=str(parsed["severity"]),
                        bucket=str(parsed["bucket"]),
                        title=str(parsed["title"]),
                        summary=str(parsed["summary"]),
                        suggested_diff=parsed["suggested_diff"],
                        ai_prompt=parsed["ai_prompt"],
                        is_resolved=bool(node.get("isResolved", False)),
                    )
                )

            page_info = rt.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")

        return findings

    def _fetch_review_comments_rest(self, pr_number: int) -> list[ReviewFinding]:
        comments = self._rest(
            "GET",
            f"/repos/{self.owner}/{self.repo}/pulls/{pr_number}/comments?per_page=100",
        )
        findings: list[ReviewFinding] = []
        if not isinstance(comments, list):
            return findings

        for c in comments:
            login = str((c.get("user") or {}).get("login", "")).lower()
            if login not in CODERABBIT_LOGINS:
                continue
            parsed = parse_coderabbit_comment_body(str(c.get("body", "")))
            findings.append(
                ReviewFinding(
                    thread_id=str(c.get("node_id") or c.get("id") or ""),
                    comment_id=int(c.get("id") or 0),
                    path=str(c.get("path") or ""),
                    line=c.get("line") or c.get("original_line"),
                    severity=str(parsed["severity"]),
                    bucket=str(parsed["bucket"]),
                    title=str(parsed["title"]),
                    summary=str(parsed["summary"]),
                    suggested_diff=parsed["suggested_diff"],
                    ai_prompt=parsed["ai_prompt"],
                    is_resolved=False,
                )
            )
        return findings

    def _fetch_all_check_runs(self, head_sha: str) -> list[dict[str, Any]]:
        """Fetch all check-run pages for `head_sha` without truncation at 100 runs."""
        raw_runs: list[dict[str, Any]] = []
        page = 1
        while True:
            payload = self._rest(
                "GET",
                f"/repos/{self.owner}/{self.repo}/commits/{head_sha}/check-runs"
                f"?per_page=100&page={page}",
            )
            if not isinstance(payload, dict):
                break
            page_runs = payload.get("check_runs", [])
            if not isinstance(page_runs, list) or not page_runs:
                break
            raw_runs.extend(page_runs)
            total_count = int(payload.get("total_count") or len(raw_runs))
            if len(page_runs) < 100 or len(raw_runs) >= total_count:
                break
            page += 1
        return raw_runs

    def get_pr_status(
        self,
        pr_number: int,
        previous_paths: Sequence[str] = (),
    ) -> PRStatusReport:
        """Fetch PR checks, CodeRabbit review completion, and unresolved findings."""
        pr = self._rest("GET", f"/repos/{self.owner}/{self.repo}/pulls/{pr_number}")
        head = pr.get("head") or {}
        base = pr.get("base") or {}
        head_sha = str(head.get("sha") or "")
        head_branch = str(head.get("ref") or "")
        base_branch = str(base.get("ref") or "")
        state = str(pr.get("state") or "")
        draft = bool(pr.get("draft", False))
        mergeable_state = str(pr.get("mergeable_state") or "unknown")
        round_num = int(pr.get("commits") or 1)

        head_repo = head.get("repo")
        base_repo = base.get("repo")
        if isinstance(head_repo, dict) and isinstance(base_repo, dict):
            head_repo_matches_base = head_repo.get("id") == base_repo.get("id")
        else:
            head_repo_matches_base = True

        raw_runs = self._fetch_all_check_runs(head_sha) if head_sha else []

        checks: list[dict[str, Any]] = []
        cr_runs: list[dict[str, Any]] = []
        ci_runs: list[dict[str, Any]] = []

        for run in raw_runs:
            name = str(run.get("name") or "")
            app_slug = str((run.get("app") or {}).get("slug") or "").lower()
            entry = {
                "name": name,
                "status": str(run.get("status") or ""),
                "conclusion": run.get("conclusion"),
                "app": app_slug,
            }
            checks.append(entry)
            if "coderabbit" in app_slug or "coderabbit" in name.lower():
                cr_runs.append(entry)
            else:
                ci_runs.append(entry)

        if head_sha:
            status_payload = self._rest(
                "GET",
                f"/repos/{self.owner}/{self.repo}/commits/{head_sha}/status",
            )
            raw_statuses = (
                status_payload.get("statuses", [])
                if isinstance(status_payload, dict)
                else []
            )
            for st in raw_statuses if isinstance(raw_statuses, list) else []:
                ctx = str(st.get("context") or "")
                st_state = str(st.get("state") or "").lower()
                mapped_status = "in_progress" if st_state == "pending" else "completed"
                mapped_conclusion = (
                    "success"
                    if st_state == "success"
                    else (None if st_state == "pending" else "failure")
                )
                st_entry = {
                    "name": ctx,
                    "status": mapped_status,
                    "conclusion": mapped_conclusion,
                    "app": "commit-status",
                }
                checks.append(st_entry)
                if "coderabbit" in ctx.lower():
                    cr_runs.append(st_entry)
                else:
                    ci_runs.append(st_entry)

        if ci_runs:
            ci_completed = all(r["status"] == "completed" for r in ci_runs)
            ci_passed = ci_completed and all(
                r["conclusion"] in {"success", "neutral", "skipped"} for r in ci_runs
            )
        else:
            ci_completed = False
            ci_passed = False

        reviews = self._rest(
            "GET",
            f"/repos/{self.owner}/{self.repo}/pulls/{pr_number}/reviews?per_page=100",
        )
        cr_review_for_sha = any(
            str((rev.get("user") or {}).get("login", "")).lower() in CODERABBIT_LOGINS
            and str(rev.get("commit_id") or "") == head_sha
            for rev in (reviews if isinstance(reviews, list) else [])
        )
        issue_comments = self._rest(
            "GET",
            f"/repos/{self.owner}/{self.repo}/issues/{pr_number}/comments?per_page=100",
        )
        short_sha = head_sha[:7].lower() if len(head_sha) >= 7 else ""
        cr_summary_present = False
        if head_sha and isinstance(issue_comments, list):
            for ic in issue_comments:
                login = str((ic.get("user") or {}).get("login", "")).lower()
                if login not in CODERABBIT_LOGINS:
                    continue
                body_low = str(ic.get("body", "")).lower()
                sha_matched = head_sha.lower() in body_low or (
                    bool(short_sha) and short_sha in body_low
                )
                if sha_matched and (
                    "walkthrough" in body_low or "auto_reviewed_commits" in body_low
                ):
                    cr_summary_present = True
                    break

        if cr_runs:
            cr_runs_ok = all(
                r["status"] == "completed"
                and r["conclusion"] in {"success", "neutral"}
                for r in cr_runs
            )
            cr_completed = cr_runs_ok or cr_review_for_sha
        else:
            cr_completed = cr_review_for_sha or cr_summary_present

        threads_verified = True
        try:
            findings = self._fetch_review_threads_graphql(pr_number)
        except Exception:
            try:
                findings = self._fetch_review_threads_graphql(pr_number)
            except Exception:
                threads_verified = False
                findings = self._fetch_review_comments_rest(pr_number)

        unresolved_blockers = sum(
            1 for f in findings if not f.is_resolved and f.bucket == "BLOCKER"
        )
        whack_a_mole_paths = detect_whack_a_mole(findings, previous_paths, round_num)
        ready_to_merge = (
            state == "open"
            and not draft
            and ci_completed
            and ci_passed
            and cr_completed
            and threads_verified
            and unresolved_blockers == 0
        )

        return PRStatusReport(
            pr_number=pr_number,
            head_branch=head_branch,
            base_branch=base_branch,
            head_sha=head_sha,
            state=state,
            draft=draft,
            mergeable_state=mergeable_state,
            ci_completed=ci_completed,
            ci_passed=ci_passed,
            cr_completed=cr_completed,
            round_num=round_num,
            checks=checks,
            findings=findings,
            unresolved_blockers=unresolved_blockers,
            whack_a_mole_paths=whack_a_mole_paths,
            ready_to_merge=ready_to_merge,
            threads_verified=threads_verified,
            head_repo_matches_base=head_repo_matches_base,
        )

    def resolve_thread(self, thread_id: str) -> bool:
        """Resolve a GraphQL review thread by node ID."""
        if not thread_id or thread_id.isdigit():
            return False
        mutation = """
        mutation($threadId: ID!) {
          resolveReviewThread(input: {threadId: $threadId}) {
            thread {
              isResolved
            }
          }
        }
        """
        res = self._graphql(mutation, {"threadId": thread_id})
        resolved = (
            res.get("data", {})
            .get("resolveReviewThread", {})
            .get("thread", {})
            .get("isResolved", False)
        )
        return bool(resolved)

    def reply_to_pr(self, pr_number: int, message: str) -> dict[str, Any]:
        """Post an issue comment on a pull request (e.g. `@coderabbitai` learnings)."""
        res = self._rest(
            "POST",
            f"/repos/{self.owner}/{self.repo}/issues/{pr_number}/comments",
            {"body": message},
        )
        if not isinstance(res, dict):
            raise RuntimeError("Unexpected response when posting PR comment")
        return res

    def close_pr(self, pr_number: int) -> None:
        """Close a pull request without merging."""
        self._rest(
            "PATCH",
            f"/repos/{self.owner}/{self.repo}/pulls/{pr_number}",
            {"state": "closed"},
        )

    def delete_remote_branch(self, branch: str) -> bool:
        """Delete a remote branch ref on origin via GitHub API."""
        encoded = urllib.parse.quote(branch, safe="")
        try:
            self._rest("DELETE", f"/repos/{self.owner}/{self.repo}/git/refs/heads/{encoded}")
            return True
        except RuntimeError as exc:
            if "(404)" in str(exc) or "(422)" in str(exc):
                return False
            raise

    def merge_pr_and_sync_master(
        self,
        pr_number: int,
        method: str = "squash",
        delete_branch: bool = True,
    ) -> dict[str, Any]:
        """Merge a verified green PR and fast-forward local `master`."""
        status = self.get_pr_status(pr_number)
        if status.state != "open":
            raise RuntimeError(f"PR #{pr_number} is not open (state={status.state!r}).")
        if status.draft:
            raise RuntimeError(f"PR #{pr_number} is a draft and cannot be auto-merged.")
        if not status.ci_completed or not status.ci_passed:
            raise RuntimeError(
                f"PR #{pr_number} CI checks not green "
                f"(ci_completed={status.ci_completed}, ci_passed={status.ci_passed})."
            )
        if not status.cr_completed:
            raise RuntimeError(f"PR #{pr_number} CodeRabbit review has not completed yet.")
        if not status.threads_verified:
            raise RuntimeError(
                f"PR #{pr_number} review thread resolution could not be verified via GraphQL."
            )
        if status.unresolved_blockers > 0:
            raise RuntimeError(
                f"PR #{pr_number} still has {status.unresolved_blockers} unresolved BLOCKER(s)."
            )
        if not status.head_sha:
            raise RuntimeError(f"PR #{pr_number} has an empty head_sha.")

        merge_res = self._rest(
            "PUT",
            f"/repos/{self.owner}/{self.repo}/pulls/{pr_number}/merge",
            {"merge_method": method, "sha": status.head_sha},
        )

        remote_deleted = False
        if (
            delete_branch
            and status.head_repo_matches_base
            and status.head_branch
            and status.head_branch != "master"
        ):
            remote_deleted = self.delete_remote_branch(status.head_branch)

        local_synced = False
        if self.repo_root is not None:
            dirty = _run_git(["status", "--porcelain"], cwd=self.repo_root, check=True)
            if dirty.stdout.strip():
                raise RuntimeError(
                    "PR merged on GitHub, but local working tree is dirty; "
                    "skipped local `master` checkout/pull."
                )
            _run_git(["checkout", "master"], cwd=self.repo_root, check=True)
            _run_git(["pull", "--ff-only", "origin", "master"], cwd=self.repo_root, check=True)
            if delete_branch and status.head_branch and status.head_branch != "master":
                local_ref = _run_git(
                    ["rev-parse", "--verify", status.head_branch],
                    cwd=self.repo_root,
                    check=False,
                )
                if local_ref.returncode == 0 and local_ref.stdout.strip() == status.head_sha:
                    _run_git(
                        ["branch", "-D", status.head_branch],
                        cwd=self.repo_root,
                        check=False,
                    )
            _run_git(["remote", "prune", "origin"], cwd=self.repo_root, check=False)
            local_synced = True

        return {
            "pr_number": pr_number,
            "merged": bool(isinstance(merge_res, dict) and merge_res.get("merged", True)),
            "merge_method": method,
            "sha": merge_res.get("sha") if isinstance(merge_res, dict) else None,
            "remote_branch_deleted": remote_deleted,
            "local_master_synced": local_synced,
        }


def _inject_audit_lens_yaml(yaml_text: str, spec: ClusterSpec) -> str:
    """Create a cluster-focused `.coderabbit.yaml` variant for Draft audit PRs."""
    # Enable draft review and force single-commit pause to protect Fair Usage
    updated = re.sub(r"drafts:\s*false", "drafts: true", yaml_text)
    updated = re.sub(
        r"auto_pause_after_reviewed_commits:\s*\d+",
        "auto_pause_after_reviewed_commits: 1",
        updated,
    )
    lens_block_lines = [
        f'    - path: "{f}"\n      instructions: |\n        {spec.lens_instructions}'
        for f in spec.files
    ]
    lens_section = "\n".join(lens_block_lines)
    if "path_instructions:" in updated:
        updated = updated.replace(
            "path_instructions:\n",
            f"path_instructions:\n{lens_section}\n",
            1,
        )
    else:
        updated += f"\nreviews:\n  path_instructions:\n{lens_section}\n"
    return updated


def prepare_cluster_audit_pr(
    cluster_id: int,
    repo_root: Path,
    client: GitHubPRClient | None = None,
) -> dict[str, Any]:
    """Provision isolated `audit/base-cluster-<id>` & `audit/head-cluster-<id>` Draft PR."""
    if cluster_id not in CLUSTER_REGISTRY:
        raise ValueError(f"Unknown cluster ID {cluster_id}; expected 1..{len(CLUSTER_REGISTRY)}")

    spec = CLUSTER_REGISTRY[cluster_id]
    gh = client
    if gh is None:
        owner, repo = parse_origin_repo(repo_root)
        gh = GitHubPRClient(owner, repo, get_github_token(repo_root), repo_root=repo_root)

    base_branch = f"audit/base-cluster-{cluster_id}"
    head_branch = f"audit/head-cluster-{cluster_id}"

    existing_pr = gh.find_open_pr(head_branch, base_branch=base_branch)
    if existing_pr is not None:
        return {
            "cluster_id": cluster_id,
            "slug": spec.slug,
            "pr_number": existing_pr.get("number"),
            "pr_url": existing_pr.get("html_url"),
            "base_branch": base_branch,
            "head_branch": head_branch,
            "reused_existing": True,
        }

    _run_git(["fetch", "origin", "master"], cwd=repo_root, check=True)
    wt_root = repo_root / ".tmp" / f"cr-audit-wt-{cluster_id}"
    if wt_root.exists():
        _run_git(["worktree", "remove", "--force", str(wt_root)], cwd=repo_root, check=False)
        shutil.rmtree(wt_root, ignore_errors=True)
    wt_root.parent.mkdir(parents=True, exist_ok=True)

    try:
        # 1. Create empty-target base branch in isolated worktree
        _run_git(
            ["worktree", "add", "--detach", str(wt_root), "origin/master"],
            cwd=repo_root,
            check=True,
        )
        master_sha = _run_git(
            ["rev-parse", "origin/master"], cwd=wt_root, check=True
        ).stdout.strip()
        _run_git(["checkout", "-B", base_branch, master_sha], cwd=wt_root, check=True)
        existing_targets = [f for f in spec.files if (wt_root / f).exists()]
        if not existing_targets:
            raise RuntimeError(f"No cluster {cluster_id} files found on origin/master.")
        _run_git(["rm", "-f", "--", *existing_targets], cwd=wt_root, check=True)
        _run_git(
            [
                "commit",
                "-m",
                f"chore(audit): empty base for cluster {cluster_id} ({spec.slug})",
            ],
            cwd=wt_root,
            check=True,
        )
        _run_git(["push", "-f", "origin", f"{base_branch}:{base_branch}"], cwd=wt_root, check=True)

        # 2. Build head branch on top of base_branch so 3-dot merge-base is base_branch
        _run_git(["checkout", "-B", head_branch, base_branch], cwd=wt_root, check=True)
        _run_git(["checkout", master_sha, "--", *existing_targets], cwd=wt_root, check=True)
        cr_yaml = wt_root / ".coderabbit.yaml"
        if cr_yaml.is_file():
            original_yaml = cr_yaml.read_text(encoding="utf-8")
            cr_yaml.write_text(_inject_audit_lens_yaml(original_yaml, spec), encoding="utf-8")
            _run_git(["add", ".coderabbit.yaml"], cwd=wt_root, check=True)
        _run_git(["add", "--", *existing_targets], cwd=wt_root, check=True)
        _run_git(
            [
                "commit",
                "-m",
                f"chore(audit): restore cluster {cluster_id} ({spec.slug}) with audit lens",
            ],
            cwd=wt_root,
            check=True,
        )
        three_dot_files = set(
            _run_git(
                ["diff", "--name-only", f"{base_branch}...{head_branch}"],
                cwd=wt_root,
                check=True,
            ).stdout.splitlines()
        )
        missing_in_diff = [f for f in existing_targets if f not in three_dot_files]
        if missing_in_diff:
            raise RuntimeError(
                f"Cluster {cluster_id} 3-dot diff missing target files: {missing_in_diff}"
            )
        _run_git(["push", "-f", "origin", f"{head_branch}:{head_branch}"], cwd=wt_root, check=True)
    finally:
        _run_git(["worktree", "remove", "--force", str(wt_root)], cwd=repo_root, check=False)
        shutil.rmtree(wt_root, ignore_errors=True)
        _run_git(["branch", "-D", base_branch], cwd=repo_root, check=False)
        _run_git(["branch", "-D", head_branch], cwd=repo_root, check=False)

    pr = gh.ensure_pr(
        head_branch=head_branch,
        base_branch=base_branch,
        title=f"[Retro Audit Cluster {cluster_id}] {spec.title}",
        body=(
            f"Automated retrospective lens audit for Cluster {cluster_id} (`{spec.slug}`).\n\n"
            f"**Files under review:**\n"
            + "\n".join(f"- `{f}`" for f in spec.files)
            + f"\n\n**Lens:**\n{spec.lens_instructions}"
        ),
        draft=True,
    )
    pr_num = pr.get("number")
    if pr_num is not None:
        gh.reply_to_pr(int(pr_num), "@coderabbitai full review")
    return {
        "cluster_id": cluster_id,
        "slug": spec.slug,
        "pr_number": pr_num,
        "pr_url": pr.get("html_url"),
        "base_branch": base_branch,
        "head_branch": head_branch,
    }


def _render_retro_audit_report(audit_dir: Path) -> Path:
    """Aggregate all cluster JSON findings into `.tmp/cr-audit/RETRO_AUDIT_REPORT.md`."""
    report_path = audit_dir / "RETRO_AUDIT_REPORT.md"
    lines: list[str] = [
        "# Retrospective CodeRabbit Audit Report",
        "",
        f"Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
    ]

    total_blockers = 0
    total_findings = 0

    for cid in sorted(CLUSTER_REGISTRY):
        cluster_file = audit_dir / f"cluster-{cid}-findings.json"
        if not cluster_file.is_file():
            continue
        data = json.loads(cluster_file.read_text(encoding="utf-8"))
        findings = data.get("findings", [])
        blockers = [f for f in findings if f.get("bucket") == "BLOCKER"]
        total_blockers += len(blockers)
        total_findings += len(findings)

        lines.append(f"## Cluster {cid}: {data.get('title', CLUSTER_REGISTRY[cid].title)}")
        lines.append(
            f"- PR: #{data.get('pr_number')} | Total: {len(findings)} | Blockers: {len(blockers)}"
        )
        lines.append("")
        if not findings:
            lines.append("_No findings reported._")
            lines.append("")
            continue

        for f in findings:
            loc = f"`{f.get('path')}:{f.get('line')}`" if f.get("line") else f"`{f.get('path')}`"
            lines.append(
                f"- **[{f.get('bucket')}/{f.get('severity')}]** {loc} — {f.get('title')}"
            )
            if f.get("summary"):
                lines.append(f"  - {str(f['summary']).splitlines()[0]}")
        lines.append("")

    lines.insert(
        4,
        f"- **Total Findings:** {total_findings} | **Total Blockers:** {total_blockers}",
    )
    report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return report_path


def _validate_cluster_audit_pr(status: PRStatusReport, cluster_id: int) -> None:
    """Ensure a PR number actually belongs to the requested audit cluster."""
    expected_head = f"audit/head-cluster-{cluster_id}"
    expected_base = f"audit/base-cluster-{cluster_id}"
    if (
        not status.head_repo_matches_base
        or status.head_branch != expected_head
        or status.base_branch != expected_base
    ):
        raise RuntimeError(
            f"PR #{status.pr_number} ({status.head_branch} -> {status.base_branch}) "
            f"does not match Cluster {cluster_id} audit refs ({expected_head} -> {expected_base})."
        )


def harvest_and_cleanup_cluster_audit(
    cluster_id: int,
    pr_number: int,
    repo_root: Path,
    client: GitHubPRClient | None = None,
    *,
    cleanup: bool = True,
) -> dict[str, Any]:
    """Download CodeRabbit findings for a cluster PR, rebuild summary, and cleanup branches."""
    if cluster_id not in CLUSTER_REGISTRY:
        raise ValueError(f"Unknown cluster ID {cluster_id}; expected 1..{len(CLUSTER_REGISTRY)}")

    spec = CLUSTER_REGISTRY[cluster_id]
    gh = client
    if gh is None:
        owner, repo = parse_origin_repo(repo_root)
        gh = GitHubPRClient(owner, repo, get_github_token(repo_root), repo_root=repo_root)

    status = gh.get_pr_status(pr_number)
    _validate_cluster_audit_pr(status, cluster_id)
    audit_dir = repo_root / ".tmp" / "cr-audit"
    audit_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "cluster_id": cluster_id,
        "slug": spec.slug,
        "title": spec.title,
        "pr_number": pr_number,
        "cr_completed": status.cr_completed,
        "total_findings": len(status.findings),
        "unresolved_blockers": status.unresolved_blockers,
        "findings": [asdict(f) for f in status.findings],
    }
    out_json = audit_dir / f"cluster-{cluster_id}-findings.json"
    out_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path = _render_retro_audit_report(audit_dir)

    cleaned = False
    if cleanup and status.cr_completed:
        gh.close_pr(pr_number)
        gh.delete_remote_branch(f"audit/head-cluster-{cluster_id}")
        gh.delete_remote_branch(f"audit/base-cluster-{cluster_id}")
        cleaned = True

    return {
        "cluster_id": cluster_id,
        "pr_number": pr_number,
        "cr_completed": status.cr_completed,
        "findings_count": len(status.findings),
        "blockers_count": status.unresolved_blockers,
        "json_path": out_json.relative_to(repo_root).as_posix(),
        "report_path": report_path.relative_to(repo_root).as_posix(),
        "cleaned_up": cleaned,
    }


def _append_audit_log(log_path: Path, message: str) -> None:
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"[{ts}] {message}\n"
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(line)


def run_all_clusters_audit(
    repo_root: Path,
    client: GitHubPRClient | None = None,
    *,
    clusters: Sequence[int] = (1, 2, 3, 4, 5),
    poll_interval_sec: int = 45,
    max_wait_sec: int = 720,
    cooldown_sec: int = 600,
) -> dict[str, Any]:
    """Run retrospective audits sequentially across clusters with Fair Usage cooldowns."""
    gh = client
    if gh is None:
        owner, repo = parse_origin_repo(repo_root)
        gh = GitHubPRClient(owner, repo, get_github_token(repo_root), repo_root=repo_root)

    audit_dir = repo_root / ".tmp" / "cr-audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    log_path = audit_dir / "audit-runner.log"

    results: list[dict[str, Any]] = []
    cluster_list = list(clusters)
    _append_audit_log(log_path, f"Starting sequential audit for clusters={cluster_list}")

    for idx, cid in enumerate(cluster_list):
        started = prepare_cluster_audit_pr(cid, repo_root, client=gh)
        pr_num = int(started["pr_number"])
        _append_audit_log(log_path, f"Cluster {cid}: opened Draft PR #{pr_num}")

        elapsed = 0
        cr_done = False
        while True:
            status = gh.get_pr_status(pr_num)
            if status.cr_completed:
                cr_done = True
                break
            if elapsed >= max_wait_sec:
                _append_audit_log(
                    log_path,
                    f"Cluster {cid}: timed out waiting for CodeRabbit after {elapsed}s",
                )
                break
            if poll_interval_sec > 0:
                time.sleep(poll_interval_sec)
            elapsed += max(poll_interval_sec, 1)

        harvested = harvest_and_cleanup_cluster_audit(
            cid, pr_num, repo_root, client=gh, cleanup=cr_done
        )
        harvested["timed_out"] = not cr_done
        results.append(harvested)
        _append_audit_log(
            log_path,
            f"Cluster {cid}: harvested findings={harvested['findings_count']} "
            f"blockers={harvested['blockers_count']} cr_done={cr_done}",
        )

        if idx < len(cluster_list) - 1 and cooldown_sec > 0:
            _append_audit_log(
                log_path,
                f"Cooling down {cooldown_sec}s before next cluster for Fair Usage safety",
            )
            time.sleep(cooldown_sec)

    report_path = _render_retro_audit_report(audit_dir)
    _append_audit_log(log_path, f"Audit run complete: {report_path.as_posix()}")
    return {
        "clusters": results,
        "report_path": report_path.relative_to(repo_root).as_posix(),
        "log_path": log_path.relative_to(repo_root).as_posix(),
    }


def _resolve_pr_number(
    client: GitHubPRClient,
    repo_root: Path,
    pr_number: int | None,
    branch: str | None,
) -> int:
    if pr_number is not None:
        return pr_number
    target_branch = branch
    if not target_branch:
        proc = _run_git(["branch", "--show-current"], cwd=repo_root, check=True)
        target_branch = proc.stdout.strip()
    if not target_branch:
        raise RuntimeError("Could not determine current branch for PR lookup.")
    pr = client.find_open_pr(target_branch)
    if pr is None:
        raise RuntimeError(f"No open PR found for branch {target_branch!r}.")
    return int(pr["number"])


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cr_pr_bridge",
        description="GitHub PR, CodeRabbit Triage & 5-Cluster Retro Audit Bridge",
    )
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Repository root path")
    sub = parser.add_subparsers(dest="command", required=True)

    p_status = sub.add_parser("status", help="Inspect CI + CodeRabbit status and findings")
    p_status.add_argument("--pr", type=int, default=None, help="PR number")
    p_status.add_argument("--branch", type=str, default=None, help="Branch name")
    p_status.add_argument(
        "--prev-paths",
        nargs="*",
        default=(),
        help="Previously edited paths for WHACK_A_MOLE detection",
    )
    p_status.add_argument("--json", action="store_true", help="Emit JSON output")

    p_ensure = sub.add_parser("ensure-pr", help="Create or return open PR for current branch")
    p_ensure.add_argument("--branch", type=str, default=None, help="Head branch")
    p_ensure.add_argument("--base", type=str, default="master", help="Base branch")
    p_ensure.add_argument("--title", type=str, default=None, help="PR title")
    p_ensure.add_argument("--body", type=str, default="", help="PR body")
    p_ensure.add_argument("--draft", action="store_true", help="Open as draft PR")

    p_merge = sub.add_parser("auto-merge", help="Squash-merge verified green PR and sync master")
    p_merge.add_argument("--pr", type=int, default=None, help="PR number")
    p_merge.add_argument("--branch", type=str, default=None, help="Head branch")
    p_merge.add_argument(
        "--method",
        choices=("squash", "merge", "rebase"),
        default="squash",
        help="GitHub merge method",
    )
    p_merge.add_argument("--keep-branch", action="store_true", help="Keep branch after merge")

    p_audit = sub.add_parser("audit", help="Manage 5-cluster retrospective CodeRabbit audit")
    p_audit.add_argument(
        "--action",
        choices=("list", "start", "harvest", "cleanup", "run-all"),
        required=True,
        help="Audit action",
    )
    p_audit.add_argument("--cluster", type=int, default=None, help="Cluster ID (1..5)")
    p_audit.add_argument("--pr", type=int, default=None, help="Audit Draft PR number")
    p_audit.add_argument(
        "--poll-interval",
        type=int,
        default=45,
        help="Polling interval in seconds for run-all",
    )
    p_audit.add_argument(
        "--max-wait",
        type=int,
        default=720,
        help="Max wait in seconds per cluster for run-all",
    )
    p_audit.add_argument(
        "--cooldown",
        type=int,
        default=600,
        help="Cooldown in seconds between clusters for Fair Usage",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = _build_cli_parser()
    args = parser.parse_args(argv)
    repo_root = args.repo.resolve()

    if args.command == "audit" and args.action == "list":
        clusters = [
            {
                "id": c.id,
                "slug": c.slug,
                "title": c.title,
                "files": list(c.files),
            }
            for c in CLUSTER_REGISTRY.values()
        ]
        print(json.dumps({"clusters": clusters}, indent=2, ensure_ascii=False))
        return 0

    owner, repo = parse_origin_repo(repo_root)
    token = get_github_token(repo_root)
    client = GitHubPRClient(owner, repo, token, repo_root=repo_root)

    if args.command == "ensure-pr":
        branch = args.branch
        if not branch:
            branch = _run_git(
                ["branch", "--show-current"], cwd=repo_root, check=True
            ).stdout.strip()
        pr = client.ensure_pr(
            head_branch=branch,
            base_branch=args.base,
            title=args.title,
            body=args.body,
            draft=args.draft,
        )
        print(
            json.dumps(
                {
                    "pr_number": pr.get("number"),
                    "url": pr.get("html_url"),
                    "state": pr.get("state"),
                    "draft": pr.get("draft"),
                },
                indent=2,
            )
        )
        return 0

    if args.command == "status":
        try:
            pr_num = _resolve_pr_number(client, repo_root, args.pr, args.branch)
        except RuntimeError as exc:
            out = {"open_pr": False, "message": str(exc)}
            print(json.dumps(out, indent=2, ensure_ascii=False))
            return 0 if args.pr is None and args.branch is None else 1

        report = client.get_pr_status(pr_num, previous_paths=args.prev_paths)
        if args.json:
            print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
        else:
            print(
                f"PR #{report.pr_number} ({report.head_branch} -> {report.base_branch}) | "
                f"round={report.round_num} | ci_completed={report.ci_completed} "
                f"ci_passed={report.ci_passed} | cr_completed={report.cr_completed} | "
                f"blockers={report.unresolved_blockers} | ready_to_merge={report.ready_to_merge}"
            )
            for f in report.findings:
                mark = "RESOLVED" if f.is_resolved else f.bucket
                print(f"  - [{mark}/{f.severity}] {f.path}:{f.line} — {f.title}")
        return 0

    if args.command == "auto-merge":
        pr_num = _resolve_pr_number(client, repo_root, args.pr, args.branch)
        res = client.merge_pr_and_sync_master(
            pr_num,
            method=args.method,
            delete_branch=not args.keep_branch,
        )
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0

    if args.command == "audit":
        if args.action == "run-all":
            target_clusters = [args.cluster] if args.cluster is not None else [1, 2, 3, 4, 5]
            cooldown = 0 if len(target_clusters) == 1 else args.cooldown
            res = run_all_clusters_audit(
                repo_root,
                client=client,
                clusters=target_clusters,
                poll_interval_sec=args.poll_interval,
                max_wait_sec=args.max_wait,
                cooldown_sec=cooldown,
            )
            print(json.dumps(res, indent=2, ensure_ascii=False))
            return 0
        if args.cluster is None:
            raise ValueError("--cluster <1..5> is required for audit start/harvest/cleanup")
        if args.action == "start":
            res = prepare_cluster_audit_pr(args.cluster, repo_root, client=client)
            print(json.dumps(res, indent=2, ensure_ascii=False))
            return 0
        if args.action == "harvest":
            pr_num = args.pr or _resolve_pr_number(
                client, repo_root, None, f"audit/head-cluster-{args.cluster}"
            )
            res = harvest_and_cleanup_cluster_audit(
                args.cluster, pr_num, repo_root, client=client, cleanup=True
            )
            print(json.dumps(res, indent=2, ensure_ascii=False))
            return 0
        if args.action == "cleanup":
            if args.pr is not None:
                st = client.get_pr_status(args.pr)
                _validate_cluster_audit_pr(st, args.cluster)
                client.close_pr(args.pr)
            else:
                existing = client.find_open_pr(
                    f"audit/head-cluster-{args.cluster}",
                    base_branch=f"audit/base-cluster-{args.cluster}",
                )
                if existing and existing.get("number"):
                    client.close_pr(int(existing["number"]))
            d1 = client.delete_remote_branch(f"audit/head-cluster-{args.cluster}")
            d2 = client.delete_remote_branch(f"audit/base-cluster-{args.cluster}")
            print(json.dumps({"cluster_id": args.cluster, "deleted_head": d1, "deleted_base": d2}))
            return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
