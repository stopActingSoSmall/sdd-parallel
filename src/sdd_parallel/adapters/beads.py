"""Beads DAG tracker adapter (.beads/issues.jsonl)."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sdd_parallel.models import EffortTier, TaskNode, TaskStatus, Wave, WaveTrack

_BACKTICK_PATH_RE = re.compile(
    r"`([A-Za-z0-9_./-]+\.(?:py|ts|tsx|js|jsx|go|rs|sql|json|yaml|yml|toml|html|css|md))`"
)
_PREFIX_PATH_RE = re.compile(
    r"(?:(?<=\s)|^)((?:src|tests|app|lib|pkg|internal|packages)/[A-Za-z0-9_./-]+\.(?:py|ts|tsx|js|jsx|go|rs|sql|json|yaml|yml|toml|html|css|md))\b"
)


class BeadsAdapter:
    """Read-Only ACL for Steve Yegge's Beads DAG issue tracker (.beads/issues.jsonl)."""

    kit_id: str = "beads"

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root.resolve()
        self.beads_dir = self.repo_root / ".beads"
        if (self.beads_dir / "beads.jsonl").is_file():
            self.issues_path = self.beads_dir / "beads.jsonl"
        elif (self.beads_dir / "issues.jsonl").is_file():
            self.issues_path = self.beads_dir / "issues.jsonl"
        else:
            self.issues_path = self.beads_dir / "beads.jsonl"

    def get_existing_jsonl_paths(self) -> list[Path]:
        """Return list of existing beads jsonl files (.beads/beads.jsonl, .beads/issues.jsonl)."""
        paths: list[Path] = []
        for name in ("beads.jsonl", "issues.jsonl"):
            p = self.beads_dir / name
            if p.is_file():
                paths.append(p)
        return paths

    def detect(self) -> bool:
        """Return True if .beads/beads.jsonl, .beads/issues.jsonl or .beads/ config exists."""
        return (
            (self.beads_dir / "beads.jsonl").is_file()
            or (self.beads_dir / "issues.jsonl").is_file()
            or (self.beads_dir / "config.yaml").is_file()
        )

    def _extract_files(self, record: dict[str, Any]) -> list[str]:
        write_files: list[str] = []
        raw_files = (
            record.get("files") or record.get("write_files") or record.get("target_files") or []
        )
        if isinstance(raw_files, list):
            for rf in raw_files:
                if isinstance(rf, str):
                    clean_rf = rf.replace("\\", "/").strip()
                    if clean_rf and clean_rf not in write_files:
                        write_files.append(clean_rf)

        text_block = "\n".join(
            [
                str(record.get("title", "")),
                str(record.get("description", "")),
                str(record.get("notes", "")),
            ]
        )
        for fm in _BACKTICK_PATH_RE.finditer(text_block):
            fp = fm.group(1).replace("\\", "/")
            if fp not in write_files:
                write_files.append(fp)
        for fm in _PREFIX_PATH_RE.finditer(text_block):
            fp = fm.group(1).replace("\\", "/")
            if fp not in write_files:
                write_files.append(fp)

        return write_files

    def _extract_dependencies(self, record: dict[str, Any], current_id: str) -> list[str]:
        depends_on: list[str] = []
        raw_deps = (
            record.get("depends_on") or record.get("dependencies") or record.get("blocked_by") or []
        )
        if isinstance(raw_deps, str):
            for part in re.split(r"[,; ]+", raw_deps):
                clean_dep = part.strip().strip("`'\"")
                if clean_dep and clean_dep != current_id and clean_dep not in depends_on:
                    depends_on.append(clean_dep)
        elif isinstance(raw_deps, list):
            for dep in raw_deps:
                if isinstance(dep, str):
                    clean_dep = dep.strip().strip("`'\"")
                    if clean_dep and clean_dep != current_id and clean_dep not in depends_on:
                        depends_on.append(clean_dep)
                elif isinstance(dep, dict):
                    dep_type = str(dep.get("type", "blocks")).lower().strip()
                    if dep_type not in {"blocks", "parent-child", "depends_on", ""}:
                        continue
                    dep_id = (
                        dep.get("target")
                        or dep.get("depends_on_id")
                        or dep.get("id")
                        or dep.get("issue_id")
                    )
                    if isinstance(dep_id, str):
                        clean_dep = dep_id.strip().strip("`'\"")
                        if clean_dep and clean_dep != current_id and clean_dep not in depends_on:
                            depends_on.append(clean_dep)
        return depends_on

    def _determine_effort(self, record: dict[str, Any], write_files: list[str]) -> EffortTier:
        priority = record.get("priority")
        if isinstance(priority, (int, float)):
            if priority == 0:
                return EffortTier.HIGH
            if priority in {1, 2}:
                return EffortTier.MEDIUM
            if priority >= 3:
                return EffortTier.LOW

        raw_effort = record.get("effort") or record.get("story_points") or record.get("points")
        if isinstance(raw_effort, str):
            eff_lower = raw_effort.lower().strip()
            if eff_lower == "low":
                return EffortTier.LOW
            if eff_lower == "high":
                return EffortTier.HIGH
            if eff_lower == "medium":
                return EffortTier.MEDIUM
        elif isinstance(raw_effort, (int, float)):
            if raw_effort <= 2:
                return EffortTier.LOW
            if raw_effort >= 8:
                return EffortTier.HIGH
            return EffortTier.MEDIUM

        if len(write_files) >= 5 or any("schema.py" in f for f in write_files):
            return EffortTier.HIGH
        if len(write_files) == 1 and not any(
            s in write_files[0] for s in ("schema.py", "models.py")
        ):
            return EffortTier.LOW
        return EffortTier.MEDIUM

    def load_tasks(
        self,
        include_done: bool = False,
        plan_source: str = "auto",
    ) -> list[TaskNode]:
        """Load issues from .beads/issues.jsonl and enrich with file footprints."""
        if not self.issues_path.is_file():
            return []

        tasks: list[TaskNode] = []
        for raw_line in self.issues_path.read_text(encoding="utf-8-sig").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                record: dict[str, Any] = json.loads(line)
            except json.JSONDecodeError:
                continue

            tid = str(record.get("id") or "").strip()
            if not tid:
                continue

            raw_status = str(record.get("status") or "backlog").lower()
            if raw_status in {"closed", "done", "resolved", "completed"}:
                status = TaskStatus.DONE
            elif raw_status in {"in_progress", "in-progress", "started", "active", "wip"}:
                status = TaskStatus.IN_PROGRESS
            elif raw_status in {"blocked"}:
                status = TaskStatus.BLOCKED
            else:
                status = TaskStatus.READY_FOR_DEV

            if status == TaskStatus.DONE and not include_done:
                continue

            title = str(record.get("title") or tid)
            write_files = self._extract_files(record)
            depends_on = self._extract_dependencies(record, tid)
            effort = self._determine_effort(record, write_files)

            epic_val = (
                record.get("epic")
                or record.get("epic_id")
                or record.get("parent")
                or record.get("parent_id")
                or "beads"
            )

            tasks.append(
                TaskNode(
                    id=tid,
                    title=title,
                    epic_id=str(epic_val),
                    status=status,
                    effort=effort,
                    write_files=write_files,
                    depends_on=depends_on,
                )
            )

        return tasks

    def reset_tasks_status(
        self,
        task_ids: Sequence[str],
        target_status: str = "ready-for-dev",
    ) -> None:
        """Reset status of issues in .beads/issues.jsonl to open."""
        target_set = set(task_ids)
        if not self.issues_path.is_file():
            return
        lines = self.issues_path.read_text(encoding="utf-8-sig").splitlines()
        new_lines: list[str] = []
        for line in lines:
            if not line.strip():
                continue
            try:
                rec: dict[str, Any] = json.loads(line)
            except json.JSONDecodeError:
                new_lines.append(line)
                continue
            issue_id = str(rec.get("id", ""))
            if issue_id in target_set:
                rec["status"] = "open"
                rec["closed_at"] = None
            new_lines.append(json.dumps(rec, ensure_ascii=False))
        self.issues_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    def cleanup_wave_artifacts(self, wave_id: int, task_ids: Sequence[str]) -> None:
        """Reset tasks and clean wave artifacts."""
        self.reset_tasks_status(task_ids)

    def check_wave_zero_blockers(self, wave: Wave | None = None) -> list[str]:
        """Detect uncommitted changes in Git working tree."""
        blockers: list[str] = []
        try:
            proc = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
            dirty_lines: list[str] = []
            for raw_line in proc.stdout.splitlines():
                if len(raw_line) < 4:
                    continue
                path_part = raw_line[3:].strip().strip('"').replace("\\", "/").rstrip("/")
                if any(
                    path_part == pref or path_part.startswith(f"{pref}/")
                    for pref in (
                        ".tmp",
                        "_bmad-output/parallel-artifacts",
                        ".parallel",
                        ".worktrees",
                        ".parallel-lock.json",
                    )
                ):
                    continue
                dirty_lines.append(raw_line.strip())

            if dirty_lines:
                blockers.append(
                    f"Dirty Git working tree ({len(dirty_lines)} modified/untracked files). "
                    "Commit or stash before spawning parallel git worktrees."
                )
        except OSError:
            pass
        return blockers

    def get_allowed_write_globs(self, track: WaveTrack) -> list[str]:
        """Return allowed write globs for Beads issue database."""
        return [".beads/issues.jsonl", ".beads/beads.jsonl"]

    def get_forbidden_files(self, wave: Wave, track: WaveTrack) -> dict[str, str]:
        """Return immutable Beads configuration file."""
        return {
            ".beads/config.yaml": "Beads tracker configuration is locked during wave execution",
        }

    def get_track_env_vars(self, track: WaveTrack) -> dict[str, str]:
        return {}

    def check_preflight_context_gate(self, wave: Wave) -> list[str]:
        return []

    def prepare_worktree_overlay(self, worktree_path: Path, track: WaveTrack) -> list[str]:
        return []

    def verify_track_spec_scope(
        self,
        worktree_path: Path,
        track: WaveTrack,
        forbidden_files: dict[str, str],
    ) -> list[str]:
        violations: list[str] = []
        for sid in track.task_ids:
            for fp in track.exclusive_write_files:
                norm_fp = fp.replace("\\", "/")
                if norm_fp in forbidden_files:
                    reason = forbidden_files[norm_fp]
                    violations.append(
                        f"Wave track {track.track_id} (task {sid}) "
                        f"touches forbidden file '{norm_fp}': {reason}"
                    )
        return violations

    def render_worker_command(self, track: WaveTrack) -> str:
        """Render operator worker command for Beads track."""
        return (
            f"`bd update {track.task_ids[0]} --status in-progress`"
            if track.task_ids
            else "`bd update`"
        )
