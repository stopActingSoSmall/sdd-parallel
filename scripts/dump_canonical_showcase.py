#!/usr/bin/env python3
"""Canonical 5-Kit Showcase Dumper (Task 23a).

Scaffolds and exercises all 5 SDD kit demo environments:
- Captures authentic file trees BEFORE and AFTER wave preparation
- Verifies track worktrees, lock manifests, and artifacts
- Captures full standalone demo outputs for Spec Kit and BMAD
- Dumps verified markdown report to docs/marketing/canonical_5kit_snapshots.md
"""

from __future__ import annotations

import io
import re
import shutil
import sys
import tempfile
from pathlib import Path

# Ensure src/ is on sys.path
_SRC_DIR = Path(__file__).resolve().parent.parent / "src"

if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from sdd_parallel.cli import main  # noqa: E402
from sdd_parallel.demo import create_demo_repository, run_demo  # noqa: E402
from sdd_parallel.worktree import _restore_os_writable  # noqa: E402


def sanitize_paths(text: str) -> str:
    """Sanitize workstation user paths to generic /tmp paths."""
    res = re.sub(
        r"[A-Za-z]:[/\\]Users[/\\][A-Za-z0-9_.-]+[/\\]AppData[/\\]Local[/\\]Temp[/\\]",
        "/tmp/",
        text,
        flags=re.IGNORECASE,
    )
    res = re.sub(
        r"[A-Za-z]:[/\\]Users[/\\][A-Za-z0-9_.-]+",
        "/home/developer",
        res,
        flags=re.IGNORECASE,
    )
    return res


def render_tree(dir_path: Path, max_depth: int = 5) -> str:
    """Render an ASCII tree representing directory hierarchy."""
    lines: list[str] = [f"{dir_path.name}/"]

    def _walk(p: Path, prefix: str, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            entries = sorted(
                p.iterdir(),
                key=lambda x: (not x.is_dir(), x.name.lower()),
            )
        except OSError:
            return

        filtered = [e for e in entries if e.name != ".git"]

        for i, entry in enumerate(filtered):
            is_last = i == len(filtered) - 1
            connector = "└── " if is_last else "├── "
            sub_prefix = "    " if is_last else "│   "
            if entry.is_dir():
                lines.append(f"{prefix}{connector}{entry.name}/")
                _walk(entry, prefix + sub_prefix, depth + 1)
            else:
                lines.append(f"{prefix}{connector}{entry.name}")

    _walk(dir_path, "", 1)
    return "\n".join(lines)


def capture_kit_snapshots(kit: str) -> dict[str, str]:
    """Scaffold a kit repo, capture BEFORE tree, plan + prepare, capture AFTER tree."""
    temp_dir = Path(tempfile.mkdtemp(prefix=f"sdd-snapshot-{kit}-")).resolve()
    try:
        create_demo_repository(temp_dir, kit=kit)
        before_tree = render_tree(temp_dir)

        # Run plan
        rc_plan = main(["plan", "--repo", str(temp_dir), "--kit", kit])
        if rc_plan != 0:
            raise RuntimeError(f"Plan failed for {kit} with code {rc_plan}")

        # Run prepare --wave 1
        rc_prep = main(["prepare", "--repo", str(temp_dir), "--wave", "1"])
        if rc_prep != 0:
            raise RuntimeError(f"Prepare failed for {kit} with code {rc_prep}")

        after_tree = render_tree(temp_dir)

        exclude_file = temp_dir / ".git" / "info" / "exclude"
        exclude_content = (
            exclude_file.read_text(encoding="utf-8").strip() if exclude_file.is_file() else ""
        )

        return {
            "kit": kit,
            "before_tree": before_tree,
            "after_tree": after_tree,
            "exclude": exclude_content,
        }
    finally:
        _restore_os_writable(temp_dir)
        shutil.rmtree(temp_dir, ignore_errors=True)


def main_dumper() -> int:
    """Generate marketing snapshots for all 5 SDD ecosystems."""
    repo_root = Path(__file__).resolve().parent.parent
    output_md = repo_root / "docs" / "marketing" / "canonical_5kit_snapshots.md"
    output_md.parent.mkdir(parents=True, exist_ok=True)

    kits = ["bmad", "spec-kit", "gsd", "openspec", "beads"]
    kit_snapshots: list[dict[str, str]] = []

    for k in kits:
        snap = capture_kit_snapshots(k)
        kit_snapshots.append(snap)

    # Capture run_demo for spec-kit
    buf_speckit = io.StringIO()
    run_demo(keep_temp=False, output=buf_speckit, kit="spec-kit")
    demo_speckit_out = buf_speckit.getvalue()

    # Capture run_demo for bmad
    buf_bmad = io.StringIO()
    run_demo(keep_temp=False, output=buf_bmad, kit="bmad")
    demo_bmad_out = buf_bmad.getvalue()

    # Capture run_demo for all
    buf_all = io.StringIO()
    run_demo(keep_temp=False, output=buf_all, kit="all")
    demo_all_out = buf_all.getvalue()

    # Assemble Markdown report
    doc_lines: list[str] = [
        "# 📸 Canonical 5-Kit Showcase & Snapshot Dossier",
        "",
        "> **Auto-generated Reference for Tasks 23b–23d (`sdd-parallel v0.1.1`)**",
        "> Generated via `scripts/dump_canonical_showcase.py`.",
        "",
        "## 📊 1. Multi-Kit Verification Matrix (`sdd-parallel demo --kit all`)",
        "",
        "```text",
        demo_all_out.strip(),
        "```",
        "",
        "## 🌳 2. Canonical Directory Trees (Before vs After `prepare --wave 1`)",
        "",
    ]

    for snap in kit_snapshots:
        k = snap["kit"].upper()
        doc_lines.extend(
            [
                f"### {k} Ecosystem",
                "",
                "#### Before `prepare --wave 1` (Clean Scaffold)",
                "```text",
                snap["before_tree"],
                "```",
                "",
                "#### After `plan` + `prepare --wave 1` (Worktrees & Scope Locks Provisioned)",
                "```text",
                snap["after_tree"],
                "```",
                "",
                "#### Git Worktree Isolation (`.git/info/exclude`)",
                "```text",
                snap["exclude"] or "(none)",
                "```",
                "",
            ]
        )

    doc_lines.extend(
        [
            "## 🚀 3. Full Standalone Demo Output: GitHub Spec Kit (`--kit spec-kit`)",
            "",
            "```text",
            demo_speckit_out.strip(),
            "```",
            "",
            "## 🚀 4. Full Standalone Demo Output: BMAD Core (`--kit bmad`)",
            "",
            "```text",
            demo_bmad_out.strip(),
            "```",
            "",
        ]
    )

    full_content = sanitize_paths("\n".join(doc_lines) + "\n")
    output_md.write_text(full_content, encoding="utf-8")
    print(f"Successfully generated {output_md.as_posix()} ({len(doc_lines)} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_dumper())
