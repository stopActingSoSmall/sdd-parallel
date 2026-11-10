#!/usr/bin/env python3
"""Dual-Remote Knowledge Vault Manager for sdd-parallel.

Maintains a complete backup snapshot (public tree + .gitignored private governance,
roadmaps, ADRs, and marketing docs) inside an isolated Git ref (`refs/vault/snapshot`)
using a sidecar index (`.git/vault.index`).

Guarantees:
1. `master` branch and `.git/index` are NEVER polluted with private files.
2. `git status --porcelain` on `master` remains 100% clean (Core Invariant 1).
3. `git checkout` never deletes private files from disk.
4. Hardware guard blocks pushing `refs/vault/snapshot` to public `origin`.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

VAULT_REF = "refs/vault/snapshot"

PRIVATE_KNOWLEDGE_PATHS: tuple[str, ...] = (
    "AGENTS.md",
    "CLAUDE.md",
    "MASTER_INDEX.md",
    "ARCHIVE_INDEX.md",
    "phases",
    "docs",
    ".agents",
    ".agent",
)

FORBIDDEN_PUBLIC_REMOTE_PATTERNS: tuple[str, ...] = (
    "stopactingsosmall/sdd-parallel",
)

CAVEMAN_SUBJECT_RE = re.compile(
    r"^(feat|fix|refactor|perf|docs|test|chore|build|ci|style|revert)"
    r"(\([a-z0-9_./-]+\))?!?:\s+.+$"
)


def validate_caveman_message(message: str) -> tuple[bool, str]:
    """Validate commit message against caveman-commit Conventional Commits rules."""
    lines = [line.rstrip() for line in message.strip().splitlines()]
    if not lines or not lines[0]:
        return False, "Commit message cannot be empty."
    subject = lines[0]
    if len(subject) > 72:
        return False, f"Subject exceeds 72 chars ({len(subject)} chars): '{subject}'"
    if subject.endswith("."):
        return False, "Subject must not end with a period."
    if not CAVEMAN_SUBJECT_RE.match(subject):
        return (
            False,
            "Subject must match Conventional Commits format: <type>(<scope>): <imperative summary>",
        )
    return True, "OK"


def _run_git(
    repo_root: Path,
    args: Sequence[str],
    env_overrides: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def collect_private_files(repo_root: Path) -> list[Path]:
    """Collect existing private governance and marketing files on disk."""
    collected: list[Path] = []
    for rel_item in PRIVATE_KNOWLEDGE_PATHS:
        target = repo_root / rel_item
        if target.is_file():
            collected.append(target)
        elif target.is_dir():
            for p in sorted(target.rglob("*")):
                if p.is_file() and "__pycache__" not in p.parts and not p.name.endswith(".pyc"):
                    collected.append(p)
    return collected


def create_vault_snapshot(repo_root: Path, message: str) -> dict[str, object]:
    """Create a commit on `refs/vault/snapshot` using isolated `.git/vault.index`."""
    valid, reason = validate_caveman_message(message)
    if not valid:
        raise ValueError(f"Invalid caveman-commit message: {reason}")

    vault_index = repo_root / ".git" / "vault.index"
    if vault_index.is_file():
        vault_index.unlink()

    env = {"GIT_INDEX_FILE": str(vault_index)}
    try:
        # 1. Seed sidecar index with current HEAD tree of master
        _run_git(repo_root, ["read-tree", "HEAD"], env_overrides=env)

        # 2. Force-add all private knowledge files into sidecar index only
        private_files = collect_private_files(repo_root)
        rel_paths = [p.relative_to(repo_root).as_posix() for p in private_files]
        if rel_paths:
            chunk_size = 50
            for idx in range(0, len(rel_paths), chunk_size):
                batch = rel_paths[idx : idx + chunk_size]
                _run_git(repo_root, ["add", "-f", "--", *batch], env_overrides=env)

        # 3. Write tree object from sidecar index
        tree_proc = _run_git(repo_root, ["write-tree"], env_overrides=env)
        tree_sha = tree_proc.stdout.strip()

        # 4. Determine parent commit on refs/vault/snapshot (or HEAD if initial)
        parent_proc = _run_git(
            repo_root,
            ["rev-parse", "--verify", VAULT_REF],
            check=False,
        )
        parent_sha = parent_proc.stdout.strip() if parent_proc.returncode == 0 else ""

        commit_args = ["commit-tree", tree_sha]
        if parent_sha:
            commit_args.extend(["-p", parent_sha])
        else:
            head_proc = _run_git(repo_root, ["rev-parse", "HEAD"])
            commit_args.extend(["-p", head_proc.stdout.strip()])
        commit_args.extend(["-m", message.strip()])

        commit_proc = _run_git(repo_root, commit_args, env_overrides=env)
        commit_sha = commit_proc.stdout.strip()

        # 5. Update isolated ref without touching working tree or HEAD
        _run_git(repo_root, ["update-ref", VAULT_REF, commit_sha])

        total_bytes = sum(p.stat().st_size for p in private_files if p.is_file())
        return {
            "ref": VAULT_REF,
            "commit_sha": commit_sha,
            "tree_sha": tree_sha,
            "private_files_count": len(private_files),
            "private_bytes": total_bytes,
            "message": message.strip().splitlines()[0],
        }
    finally:
        if vault_index.is_file():
            vault_index.unlink()


def audit_dual_remotes(repo_root: Path, vault_remote: str = "vault") -> dict[str, object]:
    """Generate Pre-Flight Audit report for public (`origin`) and private (`vault`) contours."""
    private_files = collect_private_files(repo_root)
    private_bytes = sum(p.stat().st_size for p in private_files if p.is_file())

    remotes_proc = _run_git(repo_root, ["remote", "-v"], check=False)
    remotes_map: dict[str, str] = {}
    for line in remotes_proc.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and "(push)" in line:
            remotes_map[parts[0]] = parts[1]

    vault_proc = _run_git(repo_root, ["rev-parse", "--short", VAULT_REF], check=False)
    vault_sha = vault_proc.stdout.strip() if vault_proc.returncode == 0 else "NOT_INITIALIZED"

    head_proc = _run_git(repo_root, ["rev-parse", "--short", "HEAD"], check=False)
    head_sha = head_proc.stdout.strip() if head_proc.returncode == 0 else "UNKNOWN"

    count_proc = _run_git(repo_root, ["rev-list", "--count", "HEAD"], check=False)
    commit_count = int(count_proc.stdout.strip()) if count_proc.returncode == 0 else 0

    return {
        "public_remote": remotes_map.get("origin", "NOT_CONFIGURED"),
        "public_head": head_sha,
        "public_commit_count": commit_count,
        "vault_remote_name": vault_remote,
        "vault_remote_url": remotes_map.get(vault_remote, "NOT_CONFIGURED"),
        "vault_ref": VAULT_REF,
        "vault_sha": vault_sha,
        "private_files_count": len(private_files),
        "private_size_kb": round(private_bytes / 1024.0, 2),
    }


def push_vault(repo_root: Path, remote_name: str = "vault", target_branch: str = "main") -> None:
    """Push `refs/vault/snapshot` strictly to a verified private remote."""
    if remote_name == "origin":
        raise RuntimeError(
            "SECURITY BLOCK: Refusing to push private vault snapshot to public 'origin' remote!"
        )

    url_proc = _run_git(repo_root, ["remote", "get-url", remote_name], check=False)
    if url_proc.returncode != 0:
        raise RuntimeError(
            f"Remote '{remote_name}' is not configured. Add it via:\n"
            f"  git remote add {remote_name} <private-repo-url>"
        )

    remote_url = url_proc.stdout.strip().lower()
    for forbidden in FORBIDDEN_PUBLIC_REMOTE_PATTERNS:
        if forbidden in remote_url:
            raise RuntimeError(
                f"SECURITY BLOCK: Remote '{remote_name}' ({remote_url}) points to the public "
                f"repository '{forbidden}'. Vault push aborted!"
            )

    _run_git(repo_root, ["push", remote_name, f"{VAULT_REF}:refs/heads/{target_branch}"])


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="vault_sync",
        description="Dual-Remote Private Knowledge Vault Manager for sdd-parallel",
    )
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Repository root path")
    sub = parser.add_subparsers(dest="cmd", required=True)

    audit_p = sub.add_parser("audit", help="Run Pre-Flight Audit Gate for origin and vault remotes")
    audit_p.add_argument("--remote", default="vault", help="Private remote name (default: vault)")

    snap_p = sub.add_parser(
        "snapshot",
        help="Commit public + .gitignored private files into refs/vault/snapshot",
    )
    snap_p.add_argument(
        "-m",
        "--message",
        required=True,
        help="Caveman-commit formatted message (<type>(<scope>): <summary>)",
    )

    push_p = sub.add_parser("push", help="Push refs/vault/snapshot to private vault remote")
    push_p.add_argument("--remote", default="vault", help="Private remote name (default: vault)")
    push_p.add_argument("--branch", default="main", help="Target branch on private remote")

    args = parser.parse_args(argv)
    repo_root = args.repo.resolve()

    try:
        if args.cmd == "audit":
            info = audit_dual_remotes(repo_root, vault_remote=args.remote)
            print("[PRE-FLIGHT DUAL-REMOTE AUDIT GATE]")
            print(
                f"- Public Remote (origin): {info['public_remote']} "
                f"(HEAD: {info['public_head']})"
            )
            print(f"- Public Commit Count:    {info['public_commit_count']}")
            print(
                f"- Private Vault Remote:   {info['vault_remote_name']} -> "
                f"{info['vault_remote_url']}"
            )
            print(f"- Vault Snapshot Ref:     {info['vault_ref']} ({info['vault_sha']})")
            print(
                f"- Protected Private IP:   {info['private_files_count']} files "
                f"({info['private_size_kb']} KB)"
            )
            return 0

        if args.cmd == "snapshot":
            res = create_vault_snapshot(repo_root, args.message)
            print(
                f"[vault_sync] Created snapshot {res['commit_sha']} on {res['ref']} "
                f"({res['private_files_count']} private files, {res['private_bytes']} B): "
                f"{res['message']}"
            )
            if os.environ.get("SKIP_DOCS_MIRROR") != "1":
                try:
                    try:
                        from scripts.sync_docs_mirror import sync_docs_mirror
                    except ModuleNotFoundError:
                        from sync_docs_mirror import sync_docs_mirror  # type: ignore[no-redef]
                    m_res = sync_docs_mirror(repo_root=repo_root)
                    print(
                        f"[vault_sync] Mirror synced ({m_res.total_source_files} files) -> "
                        f"{m_res.target_dir}"
                    )
                except Exception as m_err:
                    print(f"[vault_sync] Notice: Docs mirror sync skipped ({m_err})")
            return 0

        if args.cmd == "push":
            push_vault(repo_root, remote_name=args.remote, target_branch=args.branch)
            print(f"[vault_sync] Successfully pushed {VAULT_REF} to {args.remote}/{args.branch}")
            return 0
    except Exception as exc:
        sys.stderr.write(f"[vault_sync] ERROR: {exc}\n")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
