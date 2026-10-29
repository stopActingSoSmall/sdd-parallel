"""GSD (Get Shit Done) Artifact Hijack adapter (.planning/phases/*/*-PLAN.md)."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from sdd_parallel.models import EffortTier, TaskNode, TaskStatus, Wave, WaveTrack

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL)
_LIST_INLINE_RE = re.compile(r"^([A-Za-z0-9_]+):\s*\[(.*?)\]\s*$")
_SCALAR_LINE_RE = re.compile(r"^([A-Za-z0-9_]+):\s*(.+?)\s*$")
_LIST_ITEM_RE = re.compile(r"^\s*-\s*[\"']?([^\"']+)[\"']?\s*$")
_TITLE_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
_ROADMAP_PHASE_RE = re.compile(
    r"^##+\s+Phase\s+(\d+)\s*[-:]\s*(.+?)(?:\s*\((?:depends\s+on|after)\s+Phase\s+(\d+)\))?$",
    re.IGNORECASE | re.MULTILINE,
)
_CHECKLIST_DONE_RE = re.compile(r"^\s*-\s*\[[xX]\]\s*(.+)$")


class GSDHijackAdapter:
    """Artifact Hijack adapter for gsd-build/get-shit-done (.planning/).

    Reads `{phase}-{NN}-PLAN.md` files across all phases, lifting GSD's intra-phase
    lock-in while locking `.planning/STATE.md` and `.planning/ROADMAP.md` inside
    worker worktrees so parallel workers never conflict on global state files.
    """

    kit_id: str = "gsd"

    FORBIDDEN_STATE_FILES: dict[str, str] = {
        ".planning/STATE.md": "Locked by GSD Artifact Hijack (updated centrally on merge)",
        ".planning/ROADMAP.md": "Locked by GSD Artifact Hijack (updated centrally on merge)",
    }

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root.resolve()
        self.planning_dir = self.repo_root / ".planning"
        self.phases_dir = self.planning_dir / "phases"
        self.state_file = self.planning_dir / "STATE.md"
        self.roadmap_file = self.planning_dir / "ROADMAP.md"

    def detect(self) -> bool:
        """Return True if GSD .planning/ directory exists."""
        return self.roadmap_file.is_file() or self.state_file.is_file() or self.phases_dir.is_dir()

    @staticmethod
    def _parse_frontmatter(text: str) -> dict[str, str | list[str]]:
        m = _FRONTMATTER_RE.match(text)
        if not m:
            return {}
        block = m.group(1)
        data: dict[str, str | list[str]] = {}
        current_list_key: str | None = None

        for raw_line in block.splitlines():
            line = raw_line.rstrip()
            if not line.strip() or line.lstrip().startswith("#"):
                continue

            inline_m = _LIST_INLINE_RE.match(line.strip())
            if inline_m:
                key, inner = inline_m.groups()
                items = [
                    item.strip().strip("\"'")
                    for item in inner.split(",")
                    if item.strip().strip("\"'")
                ]
                data[key.lower()] = items
                current_list_key = None
                continue

            if line.strip().endswith(":") and not line.startswith(" "):
                current_list_key = line.strip()[:-1].strip().lower()
                data[current_list_key] = []
                continue

            item_m = _LIST_ITEM_RE.match(line)
            if item_m and current_list_key is not None:
                cur_val = data.get(current_list_key)
                if isinstance(cur_val, list):
                    cur_val.append(item_m.group(1).strip().strip("\"'"))
                continue

            scalar_m = _SCALAR_LINE_RE.match(line.strip())
            if scalar_m:
                key, val = scalar_m.groups()
                data[key.lower()] = val.strip().strip("\"'")
                current_list_key = None

        return data

    def _get_done_tasks_from_state(self) -> set[str]:
        """Extract plan IDs marked as completed [x] in .planning/STATE.md."""
        done_ids: set[str] = set()
        if not self.state_file.is_file():
            return done_ids
        for line in self.state_file.read_text(encoding="utf-8-sig").splitlines():
            m = _CHECKLIST_DONE_RE.match(line)
            if m:
                content = m.group(1)
                for token in re.split(r"[\s,:;()]+", content):
                    clean = token.strip().strip("`'\"")
                    if re.match(r"^\d{1,3}-\d{1,3}$", clean):
                        done_ids.add(clean)
        return done_ids

    def _parse_roadmap_phase_dependencies(self) -> dict[str, list[str]]:
        """Parse inter-phase dependencies from .planning/ROADMAP.md if present."""
        phase_deps: dict[str, list[str]] = {}
        if not self.roadmap_file.is_file():
            return phase_deps
        text = self.roadmap_file.read_text(encoding="utf-8-sig")
        for m in _ROADMAP_PHASE_RE.finditer(text):
            ph_num, _ph_title, dep_ph = m.groups()
            key = f"phase-{ph_num}"
            if dep_ph:
                phase_deps[key] = [f"phase-{dep_ph}"]
        return phase_deps

    def load_tasks(
        self,
        include_done: bool = False,
        plan_source: str = "auto",
    ) -> list[TaskNode]:
        """Load GSD plan files across all phases for cross-phase wave scheduling."""
        if not self.phases_dir.is_dir():
            return []

        done_in_state = self._get_done_tasks_from_state()
        tasks: list[TaskNode] = []
        plan_files = sorted(self.phases_dir.rglob("*-PLAN.md"))

        for plan_file in plan_files:
            stem = plan_file.name[: -len("-PLAN.md")]
            summary_file = plan_file.with_name(f"{stem}-SUMMARY.md")
            status = (
                TaskStatus.DONE
                if (summary_file.is_file() or stem in done_in_state)
                else TaskStatus.READY_FOR_DEV
            )
            if status == TaskStatus.DONE and not include_done:
                continue

            text = plan_file.read_text(encoding="utf-8-sig")
            fm = self._parse_frontmatter(text)

            raw_files = (
                fm.get("files_modified") or fm.get("files") or fm.get("modified_files") or []
            )
            write_files: list[str] = (
                [f.replace("\\", "/").strip() for f in raw_files if isinstance(f, str)]
                if isinstance(raw_files, list)
                else []
            )

            # XML-tag parsing: <task type="auto">...<files>src/a.py, src/b.py</files>...</task>
            xml_files_blocks = re.findall(
                r"<files>(.*?)</files>", text, flags=re.DOTALL | re.IGNORECASE
            )
            for block in xml_files_blocks:
                for raw_token in re.split(r"[,\n;]+", block):
                    cleaned = raw_token.strip().strip("`'\"")
                    if cleaned:
                        norm_f = cleaned.replace("\\", "/")
                        if norm_f not in write_files:
                            write_files.append(norm_f)

            raw_deps = fm.get("depends_on") or fm.get("dependencies") or fm.get("deps") or []
            depends_on: list[str] = []
            if isinstance(raw_deps, list):
                for dep in raw_deps:
                    if isinstance(dep, str):
                        clean_dep = dep.strip()
                        if clean_dep.endswith("-PLAN.md"):
                            clean_dep = clean_dep[: -len("-PLAN.md")]
                        elif clean_dep.endswith(".md"):
                            clean_dep = clean_dep[: -len(".md")]
                        if clean_dep and clean_dep != stem and clean_dep not in depends_on:
                            depends_on.append(clean_dep)

            # XML-tag parsing: <depends_on>...</depends_on>
            xml_dep_blocks = re.findall(
                r"<depends_on>(.*?)</depends_on>", text, flags=re.DOTALL | re.IGNORECASE
            )
            for block in xml_dep_blocks:
                for raw_token in re.split(r"[,\n;]+", block):
                    cleaned = raw_token.strip().strip("`'\"")
                    if cleaned.endswith("-PLAN.md"):
                        cleaned = cleaned[: -len("-PLAN.md")]
                    elif cleaned.endswith(".md"):
                        cleaned = cleaned[: -len(".md")]
                    if cleaned and cleaned != stem and cleaned not in depends_on:
                        depends_on.append(cleaned)

            title_m = _TITLE_RE.search(text)
            title = title_m.group(1).strip() if title_m else f"GSD Plan {stem}"
            phase_name = plan_file.parent.name

            # Check explicit effort in frontmatter
            fm_effort = fm.get("effort")
            effort = EffortTier.MEDIUM
            if isinstance(fm_effort, str):
                eff_lower = fm_effort.lower()
                if eff_lower == "low":
                    effort = EffortTier.LOW
                elif eff_lower == "high":
                    effort = EffortTier.HIGH
            elif len(write_files) >= 5 or any("schema.py" in f for f in write_files):
                effort = EffortTier.HIGH
            elif len(write_files) == 1 and not any(
                s in write_files[0] for s in ("schema.py", "models.py")
            ):
                effort = EffortTier.LOW

            tasks.append(
                TaskNode(
                    id=stem,
                    title=title,
                    epic_id=phase_name,
                    status=status,
                    effort=effort,
                    write_files=write_files,
                    depends_on=depends_on,
                )
            )

        return tasks

    def update_state_on_merge(
        self,
        wave_number: int,
        merged_task_ids: list[str],
    ) -> Path | None:
        """Centrally update .planning/STATE.md upon Wave K barrier merge checkpoint.

        Marks merged plans as completed [x] and records wave checkpoint history
        without causing worktree git merge conflicts.
        """
        if not self.planning_dir.is_dir():
            self.planning_dir.mkdir(parents=True, exist_ok=True)

        now_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        lines: list[str] = []
        if self.state_file.is_file():
            existing = self.state_file.read_text(encoding="utf-8-sig").splitlines()
            for line in existing:
                updated_line = line
                # Check off task items if present in checklist form
                for tid in merged_task_ids:
                    if f"{tid}" in line and "- [ ]" in line:
                        updated_line = line.replace("- [ ]", "- [x]")
                        break
                lines.append(updated_line)
        else:
            lines = [
                "# Project State (GSD Artifact Hijack Mode)",
                "",
                "## Status",
                f"- **Last Updated:** {now_str}",
                "- **Active Mode:** Parallel Wave Execution via sdd-parallel",
                "",
                "## Completed Plans",
            ]

        # Append wave merge checkpoint record
        tasks_str = ", ".join(merged_task_ids)
        lines.extend(
            [
                "",
                f"### Wave {wave_number} Merge Checkpoint (`{now_str}`)",
                f"- **Merged Tasks:** `{tasks_str}`",
                "- **Status:** Barrier Merge Verified & Quality Gates Passed",
            ]
        )

        for tid in merged_task_ids:
            task_entry = f"- [x] {tid} (Completed in Wave {wave_number})"
            if task_entry not in lines:
                lines.append(task_entry)

        content = "\n".join(lines).strip() + "\n"
        self.state_file.write_text(content, encoding="utf-8")
        return self.state_file

    def reset_tasks_status(
        self,
        task_ids: Sequence[str],
        target_status: str = "ready-for-dev",
    ) -> None:
        """Reset completed tasks in .planning/STATE.md and remove *-SUMMARY.md."""
        target_set = set(task_ids)
        if self.state_file.is_file():
            lines = self.state_file.read_text(encoding="utf-8-sig").splitlines()
            new_lines: list[str] = []
            for line in lines:
                m = _CHECKLIST_DONE_RE.match(line)
                if m:
                    content = m.group(1)
                    if any(tid in content for tid in target_set):
                        line = re.sub(r"\[[xX]\]", "[ ]", line, count=1)
                new_lines.append(line)
            self.state_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

        if self.phases_dir.is_dir():
            for tid in target_set:
                for sf in list(self.phases_dir.rglob(f"*{tid}*-SUMMARY.md")):
                    sf.unlink(missing_ok=True)

    def cleanup_wave_artifacts(self, wave_id: int, task_ids: Sequence[str]) -> None:
        """Reset task states and clean wave summaries."""
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
        """Return allowed write globs for GSD phases."""
        return [".planning/phases/**"]

    def get_forbidden_files(self, wave: Wave, track: WaveTrack) -> dict[str, str]:
        """Return immutable GSD state and roadmap files."""
        reason = "Locked by GSD Artifact Hijack (updated centrally on merge)"
        return {
            ".planning/STATE.md": reason,
            ".planning/ROADMAP.md": reason,
            ".planning/PROJECT.md": reason,
            ".planning/REQUIREMENTS.md": reason,
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
        """Render operator worker command for GSD track."""
        return (
            f"`/gsd-execute-phase {track.task_ids[0]}`"
            if track.task_ids
            else "`/gsd-execute-phase`"
        )
