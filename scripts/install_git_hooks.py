#!/usr/bin/env python3
"""Git Hooks Installer for sdd-parallel (Task 15j).

Installs or updates `.git/hooks/pre-commit` and `.git/hooks/pre-push`
to automatically enforce architecture drift invariants and leak prevention.
"""

from __future__ import annotations

import argparse
import stat
import sys
from collections.abc import Sequence
from pathlib import Path

_PRE_COMMIT_SCRIPT = """#!/bin/sh
# sdd-parallel pre-commit hook (installed by scripts/install_git_hooks.py)

if [ "$SKIP_DRIFT_SENTINEL" = "1" ]; then
    echo "[drift-sentinel] Skipping pre-commit gate (SKIP_DRIFT_SENTINEL=1)"
    exit 0
fi

echo "[drift-sentinel] Running staged delta inspection..."
uv run python -X utf8 scripts/drift_sentinel.py --strict --staged-only
"""

_PRE_PUSH_SCRIPT = """#!/bin/sh
# sdd-parallel pre-push hook (installed by scripts/install_git_hooks.py)

remote_name="$1"
remote_url="$2"

# Hardware Guard: Never allow private governance/marketing paths to leak to public origin
case "$remote_name $remote_url" in
    *origin*|*stopActingSoSmall/sdd-parallel*)
        priv_re='^(AGENTS\\.md|CLAUDE\\.md|MASTER_INDEX\\.md|ARCHIVE_INDEX\\.md'
        priv_re="$priv_re|phases/|docs/marketing/|docs/adr/|docs/research/"
        priv_re="$priv_re|docs/case-studies/|\\.agents/)"
        leaked_files=$(git ls-tree -r --name-only HEAD | grep -E "$priv_re")
        if [ -n "$leaked_files" ]; then
            echo "[SECURITY BLOCK] Private files in HEAD! Push to '$remote_name' aborted:" >&2
            echo "$leaked_files" >&2
            exit 1
        fi
        ;;
esac

if [ "$SKIP_DRIFT_SENTINEL" = "1" ]; then
    echo "[drift-sentinel] Skipping pre-push gate (SKIP_DRIFT_SENTINEL=1)"
    exit 0
fi

echo "[drift-sentinel] Running repository integrity and architecture drift check..."
uv run python -X utf8 scripts/drift_sentinel.py --strict
"""

_POST_COMMIT_SCRIPT = """#!/bin/sh
# sdd-parallel post-commit hook (installed by scripts/install_git_hooks.py)
# Automatically syncs decoupled documentation mirror to Google Drive (<0.5s)

if [ "$SKIP_DOCS_MIRROR" = "1" ]; then
    exit 0
fi

uv run python -X utf8 scripts/sync_docs_mirror.py
"""


def install_hooks(repo_root: Path) -> tuple[Path, Path, Path]:
    """Install pre-commit, pre-push, and post-commit hooks in .git/hooks directory."""
    git_dir = repo_root / ".git"
    if not git_dir.is_dir():
        raise FileNotFoundError(
            f"Directory '.git' not found in {repo_root}. Cannot install git hooks."
        )

    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    pre_commit = hooks_dir / "pre-commit"
    pre_push = hooks_dir / "pre-push"
    post_commit = hooks_dir / "post-commit"

    # Write hook contents using LF line endings for bash compatibility
    pre_commit.write_bytes(_PRE_COMMIT_SCRIPT.encode("utf-8"))
    pre_push.write_bytes(_PRE_PUSH_SCRIPT.encode("utf-8"))
    post_commit.write_bytes(_POST_COMMIT_SCRIPT.encode("utf-8"))

    # Make hooks executable
    for p in (pre_commit, pre_push, post_commit):
        current_mode = p.stat().st_mode
        p.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    return pre_commit, pre_push, post_commit


def uninstall_hooks(repo_root: Path) -> None:
    """Remove installed sdd-parallel git hooks."""
    hooks_dir = repo_root / ".git" / "hooks"
    for hook_name in ("pre-commit", "pre-push", "post-commit"):
        hook_path = hooks_dir / hook_name
        if hook_path.is_file():
            text = hook_path.read_text(encoding="utf-8", errors="ignore")
            if any(k in text for k in ("sdd-parallel", "drift_sentinel", "sync_docs_mirror")):
                hook_path.unlink()
                print(f"[install_git_hooks] Removed hook: {hook_path}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="install_git_hooks",
        description="Install Git pre-commit and pre-push gates for sdd-parallel",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path.cwd(),
        help="Target repository root path",
    )
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="Uninstall git hooks previously installed by sdd-parallel",
    )

    args = parser.parse_args(argv)
    repo_root = args.repo.resolve()

    if args.uninstall:
        uninstall_hooks(repo_root)
        print("Git hooks uninstalled.")
        return 0

    try:
        pre_commit, pre_push, post_commit = install_hooks(repo_root)
        print(f"[install_git_hooks] Installed pre-commit hook:  {pre_commit}")
        print(f"[install_git_hooks] Installed pre-push hook:    {pre_push}")
        print(f"[install_git_hooks] Installed post-commit hook:  {post_commit}")
        print(
            "[install_git_hooks] Hooks are active "
            "(bypass sentinel with SKIP_DRIFT_SENTINEL=1, docs mirror with SKIP_DOCS_MIRROR=1)."
        )
        return 0
    except Exception as e:
        sys.stderr.write(f"[install_git_hooks] Error installing hooks: {e}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
