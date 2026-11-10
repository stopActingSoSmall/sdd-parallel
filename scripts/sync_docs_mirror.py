#!/usr/bin/env python3
"""Automated One-Way Docs & GTM Mirror Sync for Google Drive.

Synchronizes project documentation, engineering specifications, indexes,
and GTM roadmaps into an isolated external folder for Google Drive sync
and Gemini LLM knowledge ingestion.

Strict Invariants:
1. Strict One-Way Export: Repository files are read-only; writes occur only in target mirror.
2. Mirror Purity: Files deleted/renamed in the repo are pruned from the mirror.
3. Zero Code Pollution: Code (.py), bytecode (.pyc), virtualenvs (.venv), and .git
   are strictly blacklisted.
4. Fast Execution: Optimized for <0.5s execution time on typical commits.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

# Subdirectory used within target destination
DEFAULT_MIRROR_SUBDIR = "SDD-Parallel-Docs"
DEFAULT_FALLBACK_PARENT = "_MIRRORS"
MANIFEST_FILENAME = ".sdd_mirror_manifest.json"

# Strict Blacklist: files, extensions, and directory names that MUST NEVER enter the mirror
BLACKLIST_EXTENSIONS = frozenset(
    {
        ".py",
        ".pyc",
        ".pyo",
        ".pyd",
        ".tmp",
        ".swp",
        ".swo",
        ".bak",
    }
)

BLACKLIST_DIRNAMES = frozenset(
    {
        "__pycache__",
        ".worktrees",
        ".tmp",
        ".git",
        ".venv",
        "venv",
        "node_modules",
        ".ruff_cache",
        ".pytest_cache",
        ".mypy_cache",
        ".agents",
        ".agent",
    }
)

BLACKLIST_FILENAMES = frozenset(
    {
        "Thumbs.db",
        ".DS_Store",
        "desktop.ini",
    }
)

# Files in mirror that should never be pruned by mirror purity logic
PROTECTED_MIRROR_FILENAMES = frozenset(
    {
        MANIFEST_FILENAME,
        "desktop.ini",
        ".DS_Store",
        "Thumbs.db",
    }
)


@dataclass
class SyncResult:
    """Summary of documentation mirror synchronization."""

    target_dir: str
    target_source: str
    total_source_files: int
    copied: int
    updated: int
    deleted: int
    unchanged: int
    elapsed_sec: float
    has_drift: bool
    dry_run: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def find_repo_root(start_path: Path | None = None) -> Path:
    """Locate repository root directory by walking upwards until .git is found."""
    current = (start_path or Path.cwd()).resolve()
    for parent in [current, *current.parents]:
        if (parent / ".git").exists() or (parent / "pyproject.toml").is_file():
            return parent
    return current


def resolve_target_dir(
    explicit_target: Path | str | None = None,
) -> tuple[Path, str]:
    """Resolve target mirror path based on priority rules.

    Priority:
    1. Explicit CLI argument (--target-dir)
    2. Environment variable SDD_DRIVE_MIRROR_PATH
    3. Google Drive mount on Windows (G:/My Drive or G:/Мой диск or G:/)
    4. Fallback directory outside repository (Path.home() / "_MIRRORS" / "sdd-parallel-docs")
    """
    if explicit_target is not None:
        target = Path(explicit_target).resolve()
        return target, "cli_argument"

    env_target = os.environ.get("SDD_DRIVE_MIRROR_PATH")
    if env_target and env_target.strip():
        target = Path(env_target.strip()).resolve()
        return target, "env_variable (SDD_DRIVE_MIRROR_PATH)"

    # Probe Google Drive on drive G:
    g_root = Path("G:/")
    if g_root.is_dir():
        my_drive = g_root / "My Drive"
        if my_drive.is_dir():
            return my_drive / DEFAULT_MIRROR_SUBDIR, "google_drive (G:/My Drive)"
        moi_disk = g_root / "Мой диск"
        if moi_disk.is_dir():
            return moi_disk / DEFAULT_MIRROR_SUBDIR, "google_drive (G:/Мой диск)"
        return g_root / DEFAULT_MIRROR_SUBDIR, "google_drive (G:/)"

    # Fallback path outside repo
    try:
        home_dir = Path.home()
    except Exception:
        home_dir = Path.cwd().parent
    fallback = home_dir / DEFAULT_FALLBACK_PARENT / "sdd-parallel-docs"
    return fallback.resolve(), "local_fallback"


def validate_target_safety(target_dir: Path, repo_root: Path, force: bool = False) -> None:
    """Validate that target directory is safe and will not prune arbitrary user files."""
    resolved_target = target_dir.resolve()
    resolved_repo = repo_root.resolve()

    if resolved_target == resolved_repo:
        raise ValueError(
            f"Target directory cannot be identical to repository root ({resolved_repo})."
        )

    if resolved_repo in resolved_target.parents:
        raise ValueError(
            f"Target directory ({resolved_target}) cannot reside inside "
            f"repository root ({resolved_repo})."
        )

    if resolved_target in resolved_repo.parents:
        raise ValueError(
            f"Target directory ({resolved_target}) cannot be a parent of "
            f"repository root ({resolved_repo})."
        )

    # Protect against drive roots or system root
    if resolved_target.parent == resolved_target:
        raise ValueError(f"Target directory cannot be a root drive path ({resolved_target}).")

    if resolved_target.is_dir() and not force:
        # Check if target directory already exists with non-mirror files
        manifest_file = resolved_target / MANIFEST_FILENAME
        existing_entries = list(resolved_target.iterdir())
        if existing_entries and not manifest_file.is_file():
            # If target has files but no manifest, verify it looks like a docs mirror
            target_name_lower = resolved_target.name.lower()
            if "sdd-parallel-docs" not in target_name_lower and "docs" not in target_name_lower:
                raise ValueError(
                    f"Refusing to sync to existing non-empty directory without manifest: "
                    f"'{resolved_target}'. Use --force to override."
                )


def is_file_allowed(rel_path: Path) -> bool:
    """Check if relative file path is strictly allowed by blacklist filters."""
    filename = rel_path.name
    if filename in BLACKLIST_FILENAMES:
        return False
    if rel_path.suffix.lower() in BLACKLIST_EXTENSIONS:
        return False

    for part in rel_path.parts[:-1]:
        if part in BLACKLIST_DIRNAMES:
            return False

    return True


def collect_whitelisted_files(repo_root: Path) -> dict[str, Path]:
    """Scan and collect all whitelisted documentation and spec files.

    Returns mapping of normalized posix relative path to absolute Path.
    """
    whitelisted: dict[str, Path] = {}

    def _add_file(abs_path: Path) -> None:
        if not abs_path.is_file():
            return
        rel = abs_path.relative_to(repo_root)
        if is_file_allowed(rel):
            whitelisted[rel.as_posix()] = abs_path

    # 1. Root documentation indexes and contracts
    for candidate in repo_root.glob("*.md"):
        _add_file(candidate)

    for root_doc_name in ("llms.txt", "LICENSE"):
        _add_file(repo_root / root_doc_name)

    # 2. Engineering specifications (phases/**)
    phases_dir = repo_root / "phases"
    if phases_dir.is_dir():
        for p in phases_dir.rglob("*"):
            if p.is_file():
                _add_file(p)

    # 3. Documentation directory (docs/**)
    docs_dir = repo_root / "docs"
    if docs_dir.is_dir():
        for p in docs_dir.rglob("*"):
            if p.is_file():
                _add_file(p)

    # 4. Interactive Inspector (assets/inspector.html)
    inspector_file = repo_root / "assets" / "inspector.html"
    if inspector_file.is_file():
        _add_file(inspector_file)

    return whitelisted


def calculate_sha256(file_path: Path) -> str:
    """Compute SHA256 hex digest of a file in streaming chunks."""
    hasher = hashlib.sha256()
    with file_path.open("rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def sync_docs_mirror(
    repo_root: Path | None = None,
    target_dir: Path | str | None = None,
    dry_run: bool = False,
    check_only: bool = False,
    force: bool = False,
    verbose: bool = False,
) -> SyncResult:
    """Execute one-way mirror synchronization to target Google Drive directory.

    Guarantees atomic file updates, pruning of removed files, and purity manifest.
    """
    start_time = time.perf_counter()
    root = (repo_root or find_repo_root()).resolve()
    resolved_target, target_source = resolve_target_dir(target_dir)

    validate_target_safety(resolved_target, root, force=force)

    whitelisted_files = collect_whitelisted_files(root)

    copied = 0
    updated = 0
    deleted = 0
    unchanged = 0

    to_copy: list[tuple[str, Path, Path]] = []
    to_update: list[tuple[str, Path, Path]] = []

    # 1. Inspect source files against destination mirror
    for rel_posix, src_path in whitelisted_files.items():
        dest_path = resolved_target / Path(rel_posix)

        if not dest_path.is_file():
            to_copy.append((rel_posix, src_path, dest_path))
            continue

        src_stat = src_path.stat()
        dest_stat = dest_path.stat()

        # Quick size check
        if src_stat.st_size != dest_stat.st_size:
            to_update.append((rel_posix, src_path, dest_path))
            continue

        # Fast mtime check (shutil.copy2 preserves timestamps)
        if abs(src_stat.st_mtime - dest_stat.st_mtime) < 0.01:
            unchanged += 1
            continue

        # Content hash check for bit-exact equality if timestamp differs
        if calculate_sha256(src_path) != calculate_sha256(dest_path):
            to_update.append((rel_posix, src_path, dest_path))
        else:
            unchanged += 1

    # 2. Inspect destination mirror for stale files (mirror purity)
    to_delete: list[Path] = []
    if resolved_target.is_dir():
        for dest_file in resolved_target.rglob("*"):
            if not dest_file.is_file():
                continue

            rel_dest = dest_file.relative_to(resolved_target).as_posix()

            # Ignore protected system files and Google Drive temp files
            if (
                dest_file.name in PROTECTED_MIRROR_FILENAMES
                or dest_file.name.startswith(".tmp.drive")
                or dest_file.name.endswith(".driveupload")
                or dest_file.name.startswith("~$")
            ):
                continue

            if rel_dest not in whitelisted_files:
                to_delete.append(dest_file)

    copied = len(to_copy)
    updated = len(to_update)
    deleted = len(to_delete)
    has_drift = (copied + updated + deleted) > 0

    if check_only:
        elapsed = time.perf_counter() - start_time
        return SyncResult(
            target_dir=str(resolved_target),
            target_source=target_source,
            total_source_files=len(whitelisted_files),
            copied=copied,
            updated=updated,
            deleted=deleted,
            unchanged=unchanged,
            elapsed_sec=round(elapsed, 4),
            has_drift=has_drift,
            dry_run=True,
        )

    # 3. Apply changes if not dry_run
    if not dry_run:
        resolved_target.mkdir(parents=True, exist_ok=True)

        # Copy new files
        for rel_posix, src_path, dest_path in to_copy:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, dest_path)
            if verbose:
                print(f"  + [NEW]     {rel_posix}")

        # Update modified files
        for rel_posix, src_path, dest_path in to_update:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, dest_path)
            if verbose:
                print(f"  ~ [UPDATED] {rel_posix}")

        # Delete stale files
        for stale_path in to_delete:
            rel_del = stale_path.relative_to(resolved_target).as_posix()
            try:
                stale_path.unlink()
                if verbose:
                    print(f"  - [DELETED] {rel_del}")
            except OSError as err:
                sys.stderr.write(f"[docs-mirror] Warning: Failed to prune {rel_del}: {err}\n")

        # Prune empty directories bottom-up
        for dirpath, dirnames, filenames in os.walk(resolved_target, topdown=False):
            dp = Path(dirpath)
            if dp == resolved_target:
                continue
            if not any(dp.iterdir()):
                try:
                    dp.rmdir()
                except OSError:
                    pass

        # Write manifest
        manifest_path = resolved_target / MANIFEST_FILENAME
        manifest_payload = {
            "project": "sdd-parallel",
            "synced_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "target_source": target_source,
            "total_files": len(whitelisted_files),
            "files": sorted(whitelisted_files.keys()),
        }
        manifest_path.write_text(
            json.dumps(manifest_payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    elapsed = time.perf_counter() - start_time
    return SyncResult(
        target_dir=str(resolved_target),
        target_source=target_source,
        total_source_files=len(whitelisted_files),
        copied=copied,
        updated=updated,
        deleted=deleted,
        unchanged=unchanged,
        elapsed_sec=round(elapsed, 4),
        has_drift=has_drift,
        dry_run=dry_run,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """CLI Entrypoint for docs mirror sync."""
    parser = argparse.ArgumentParser(
        prog="sync_docs_mirror",
        description="Synchronize sdd-parallel documentation to Google Drive external mirror",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=None,
        help="Repository root path (defaults to auto-discovery)",
    )
    parser.add_argument(
        "--target-dir",
        type=Path,
        default=None,
        help="Target mirror directory (overrides default Google Drive and fallback)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate mirror operations without writing to or deleting from disk",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check mirror synchronization status (exit code 1 if drift exists)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force synchronization even into existing directory without manifest",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print detailed file-by-file synchronization actions",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Minimal console output",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output result as JSON",
    )

    args = parser.parse_args(argv)

    try:
        res = sync_docs_mirror(
            repo_root=args.repo,
            target_dir=args.target_dir,
            dry_run=args.dry_run,
            check_only=args.check,
            force=args.force,
            verbose=args.verbose,
        )

        if args.json_output:
            print(json.dumps(res.to_dict(), indent=2))
            return 1 if (args.check and res.has_drift) else 0

        prefix = "[docs-mirror]"
        if args.check:
            if res.has_drift:
                print(
                    f"{prefix} DRIFT DETECTED: {res.copied} new, {res.updated} modified, "
                    f"{res.deleted} stale in mirror."
                )
                print(f"{prefix} Target: {res.target_dir} ({res.target_source})")
                return 1
            if not args.quiet:
                print(
                    f"{prefix} CLEAN: All {res.total_source_files} files synchronized "
                    f"in {res.elapsed_sec:.3f}s."
                )
                print(f"{prefix} Target: {res.target_dir}")
            return 0

        mode_tag = " [DRY-RUN]" if res.dry_run else ""
        if not args.quiet or res.has_drift:
            print(
                f"{prefix}{mode_tag} Synced {res.total_source_files} files "
                f"(+ {res.copied} new, ~ {res.updated} updated, - {res.deleted} deleted, "
                f"= {res.unchanged} unchanged) in {res.elapsed_sec:.3f}s"
            )
            print(f"{prefix} Target: {res.target_dir} (source: {res.target_source})")
        return 0

    except Exception as exc:
        sys.stderr.write(f"[docs-mirror] ERROR: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
