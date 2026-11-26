"""Pluggable Declarative Custom SDD Adapter (.parallel/custom-kit.yaml).

Enables custom and in-house SDD roadmap topologies, custom Markdown/YAML task
specifications, and configurable status rankings without hardcoding paths in the core engine.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from sdd_parallel.models import EffortTier, TaskNode, TaskStatus, Wave, WaveTrack

_BACKTICK_PATH_RE = re.compile(
    r"`([A-Za-z0-9_./-]+\.(?:py|ts|tsx|js|jsx|go|rs|sql|json|yaml|yml|toml|html|css|md))`"
)
_CHECKLIST_TASK_RE = re.compile(
    r"^\s*[-*]\s*\[([ xX])\]\s*`?([A-Za-z0-9_.-]+)`?\s*:?\s*(.+)$"
)
_FILES_LINE_RE = re.compile(
    r"^\s*[-*]\s*(?:Files?|Target\s+files?|Allowed\s+Files?|Write\s+Files?):\s*(.+)$",
    re.IGNORECASE,
)
_DEPS_LINE_RE = re.compile(
    r"^\s*[-*]\s*(?:\*{0,2}(?:Depends\s+on|Dependencies|Prerequisites|After):\*{0,2})\s*(.+)$",
    re.IGNORECASE,
)
_EFFORT_LINE_RE = re.compile(
    r"(?:[-*]\s*\*\*(?:Effort|Complexity):\*\*\s*([a-zA-Z_-]+)|\[Effort:\s*([a-zA-Z_-]+)\]|\b(?:effort|complexity):\s*(?:`?|\*{0,2})([a-zA-Z_-]+)|\*\*(low|medium|high)\*\*)",
    re.IGNORECASE,
)
_STATUS_LINE_RE = re.compile(
    r"^\s*[-*]\s*(?:\*{0,2}(?:Status|State):\*{0,2})\s*(?:[^\w\s]*\s*)?`?([A-Za-z0-9_.-]+)`?",
    re.IGNORECASE,
)
_FM_STATUS_RE = re.compile(
    r"^status:\s*['\"]?([A-Za-z0-9_.-]+)['\"]?\s*$",
    re.IGNORECASE | re.MULTILINE,
)


class CustomManifestAdapter:
    """Declarative custom SDD adapter driven by .parallel/custom-kit.yaml."""

    kit_id: str = "custom"

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root.resolve()
        self.config_path = self._find_config_file()
        self._config: dict[str, Any] = {}
        if self.config_path and self.config_path.is_file():
            self._load_config()

    def _find_config_file(self) -> Path | None:
        candidates = [
            self.repo_root / ".parallel" / "custom-kit.yaml",
            self.repo_root / ".parallel" / "custom-kit.yml",
            self.repo_root / ".parallel" / "custom-adapter.yaml",
            self.repo_root / ".parallel" / "custom-adapter.yml",
            self.repo_root / "custom-kit.yaml",
        ]
        for c in candidates:
            if c.is_file():
                return c
        return self.repo_root / ".parallel" / "custom-kit.yaml"

    def _load_config(self) -> None:
        if not self.config_path or not self.config_path.is_file():
            return
        yaml_parser = YAML(typ="safe")
        try:
            raw = yaml_parser.load(self.config_path.read_text(encoding="utf-8-sig"))
            if isinstance(raw, dict):
                self._config = raw
        except Exception:
            self._config = {}

    def detect(self) -> bool:
        """Return True if custom kit manifest exists."""
        return self.config_path is not None and self.config_path.is_file()

    def get_config(self) -> dict[str, Any]:
        """Return parsed custom kit configuration."""
        if not self._config and self.config_path and self.config_path.is_file():
            self._load_config()
        return self._config

    def _normalize_status(self, raw_status: str) -> TaskStatus:
        """Map raw status string to canonical TaskStatus using status_ranks configuration."""
        cfg = self.get_config()
        status_ranks: dict[str, Any] = cfg.get("status_ranks", {})
        norm = raw_status.strip().lower().replace("_", "-")

        # 1. User-configured status map
        if norm in status_ranks:
            target = str(status_ranks[norm]).strip().lower().replace("_", "-")
            for ts in TaskStatus:
                if ts.value == target:
                    return ts

        # 2. Canonical fallbacks
        if norm in {"done", "completed", "closed", "built"}:
            return TaskStatus.DONE
        if norm in {"review", "in-review", "testing", "evaluating"}:
            return TaskStatus.REVIEW
        if norm in {"in-progress", "active", "doing", "wip", "blocked"}:
            return TaskStatus.IN_PROGRESS
        if norm in {"ready-for-dev", "ready", "todo", "planned", "backlog", "draft"}:
            return TaskStatus.READY_FOR_DEV
        return TaskStatus.BACKLOG

    def _parse_task_file_markdown(self, file_path: Path) -> TaskNode | None:
        """Parse a standalone task specification file (e.g. phases/task_01.md)."""
        try:
            content = file_path.read_text(encoding="utf-8-sig")
        except OSError:
            return None

        # Resolve ID from filename or frontmatter
        task_id = file_path.stem
        m_id = re.search(r"^id:\s*['\"]?([A-Za-z0-9_.-]+)['\"]?", content, re.MULTILINE)
        if m_id:
            task_id = m_id.group(1)
        elif task_id.startswith("task_"):
            task_id = task_id.replace("task_", "")

        # Title
        title = task_id
        for line in content.splitlines():
            line_s = line.strip()
            if line_s.startswith("# "):
                title = line_s.lstrip("# 📋").strip()
                break

        # Status
        status = TaskStatus.READY_FOR_DEV
        fm_match = _FM_STATUS_RE.search(content)
        if fm_match:
            status = self._normalize_status(fm_match.group(1))
        else:
            for line in content.splitlines():
                sm = _STATUS_LINE_RE.search(line)
                if sm:
                    status = self._normalize_status(sm.group(1))
                    break

        # Effort
        effort = EffortTier.MEDIUM
        for line in content.splitlines():
            em = _EFFORT_LINE_RE.search(line)
            if em:
                eff_str = (
                    em.group(1) or em.group(2) or em.group(3) or em.group(4) or ""
                ).lower()
                if eff_str == "high":
                    effort = EffortTier.HIGH
                    break
                if eff_str == "low":
                    effort = EffortTier.LOW
                    break

        # Write files
        write_files: list[str] = []
        in_allowed_edit = False
        for line in content.splitlines():
            line_s = line.strip()
            is_edit_header = bool(
                re.search(
                    r"###?\s+(?:Files Allowed to Edit|Write Files|Target Files)",
                    line_s,
                    re.I,
                )
            )
            if is_edit_header:
                in_allowed_edit = True
                continue
            if in_allowed_edit:
                if line_s.startswith("#"):
                    in_allowed_edit = False
                    continue
                for match in _BACKTICK_PATH_RE.finditer(line_s):
                    p = match.group(1)
                    if p not in write_files and not p.startswith("tests/"):
                        write_files.append(p)
            else:
                fm = _FILES_LINE_RE.match(line_s)
                if fm:
                    for match in _BACKTICK_PATH_RE.finditer(fm.group(1)):
                        p = match.group(1)
                        if p not in write_files:
                            write_files.append(p)

        # Depends on
        depends_on: list[str] = []
        for line in content.splitlines():
            dm = _DEPS_LINE_RE.match(line.strip())
            if dm:
                raw_deps = dm.group(1)
                link_matches = re.findall(r"\[([^\]]+)\]\(([^)]+)\)", raw_deps)
                for label, target in link_matches:
                    for cand in (label, Path(target).stem):
                        for m in re.findall(r"(?:Task\s+|task_)?([A-Za-z0-9_.-]+)", cand, re.I):
                            c = m.strip("`[]() ")
                            if c and c.lower() not in {"and", "or", "the", "a", "md", "task"}:
                                if c not in depends_on and c != task_id:
                                    depends_on.append(c)
                for dep_id in re.findall(r"`([A-Za-z0-9_.-]+)`", raw_deps):
                    dep_clean = dep_id.strip("`[]() ")
                    if (
                        dep_clean
                        and dep_clean.lower() not in {"and", "or", "the", "a", "md", "task"}
                    ):
                        if dep_clean not in depends_on and dep_clean != task_id:
                            depends_on.append(dep_clean)

        return TaskNode(
            id=task_id,
            title=title,
            status=status,
            effort=effort,
            write_files=write_files,
            depends_on=depends_on,
            description=content[:500],
        )

    def _parse_checklist_markdown(self, file_path: Path) -> list[TaskNode]:
        """Parse tasks from a markdown checklist (e.g. tasks.md)."""
        tasks: list[TaskNode] = []
        try:
            content = file_path.read_text(encoding="utf-8-sig")
        except OSError:
            return tasks

        current_task: TaskNode | None = None
        for line in content.splitlines():
            cm = _CHECKLIST_TASK_RE.match(line)
            if cm:
                if current_task:
                    tasks.append(current_task)
                is_checked = cm.group(1).lower() == "x"
                t_id = cm.group(2)
                t_title = cm.group(3).strip()
                status = TaskStatus.DONE if is_checked else TaskStatus.READY_FOR_DEV
                current_task = TaskNode(
                    id=t_id,
                    title=t_title,
                    status=status,
                    write_files=[],
                    depends_on=[],
                )
                continue

            if current_task:
                fm = _FILES_LINE_RE.match(line)
                if fm:
                    for match in _BACKTICK_PATH_RE.finditer(fm.group(1)):
                        p = match.group(1)
                        if p not in current_task.write_files:
                            current_task.write_files.append(p)
                dm = _DEPS_LINE_RE.match(line)
                if dm:
                    for match in re.finditer(r"`?([A-Za-z0-9_.-]+)`?", dm.group(1)):
                        dep = match.group(1)
                        if dep and dep != current_task.id and dep not in current_task.depends_on:
                            current_task.depends_on.append(dep)
                eff = _EFFORT_LINE_RE.search(line)
                if eff:
                    val = (eff.group(1) or eff.group(2) or eff.group(3) or "").lower()
                    if val == "high":
                        current_task.effort = EffortTier.HIGH
                    elif val == "low":
                        current_task.effort = EffortTier.LOW

        if current_task:
            tasks.append(current_task)
        return tasks

    def _parse_yaml_tasks(self, file_path: Path) -> list[TaskNode]:
        """Parse tasks declared in YAML or JSON."""
        yaml_parser = YAML(typ="safe")
        try:
            data = yaml_parser.load(file_path.read_text(encoding="utf-8-sig"))
        except Exception:
            return []

        raw_list: list[dict[str, Any]] = []
        if isinstance(data, list):
            raw_list = [item for item in data if isinstance(item, dict)]
        elif isinstance(data, dict):
            for key in ("tasks", "stories", "roadmap", "items"):
                val = data.get(key)
                if isinstance(val, list):
                    raw_list = [item for item in val if isinstance(item, dict)]
                    break

        tasks: list[TaskNode] = []
        for item in raw_list:
            t_id = str(item.get("id") or item.get("name") or "")
            if not t_id:
                continue
            title = str(item.get("title") or t_id)
            raw_st = str(item.get("status") or "ready-for-dev")
            status = self._normalize_status(raw_st)
            write_files = [str(f) for f in item.get("write_files", []) or item.get("files", [])]
            depends_on = [str(d) for d in item.get("depends_on", []) or item.get("after", [])]
            eff_raw = str(item.get("effort") or "medium").lower()
            effort = EffortTier.MEDIUM
            if eff_raw == "high":
                effort = EffortTier.HIGH
            elif eff_raw == "low":
                effort = EffortTier.LOW

            tasks.append(
                TaskNode(
                    id=t_id,
                    title=title,
                    status=status,
                    effort=effort,
                    write_files=write_files,
                    depends_on=depends_on,
                    description=str(item.get("description") or ""),
                )
            )
        return tasks

    def load_tasks(
        self,
        include_done: bool = False,
        plan_source: str = "auto",
    ) -> list[TaskNode]:
        """Load and parse task nodes from configured roadmap sources."""
        cfg = self.get_config()
        sources = cfg.get("roadmap_sources", [])
        if not sources:
            # Fallback auto-discovery if manifest has no explicit sources
            sources = [
                {"path": "phases/task_*.md", "format": "markdown"},
                {"path": "specs/*/tasks.md", "format": "markdown"},
                {"path": "tasks/*.md", "format": "markdown"},
            ]

        all_tasks: list[TaskNode] = []
        seen_ids: set[str] = set()

        for src in sources:
            if isinstance(src, str):
                src_dict = {"path": src, "format": "markdown"}
            elif isinstance(src, dict):
                src_dict = src
            else:
                continue

            pattern = src_dict.get("path", "")
            fmt = str(src_dict.get("format", "auto")).lower()

            for matched_path in sorted(self.repo_root.glob(pattern)):
                if not matched_path.is_file():
                    continue

                extracted: list[TaskNode] = []
                if fmt in {"yaml", "json"} or matched_path.suffix in {".yaml", ".yml", ".json"}:
                    extracted = self._parse_yaml_tasks(matched_path)
                elif fmt == "checklist":
                    extracted = self._parse_checklist_markdown(matched_path)
                else:
                    # Try checklist first; if empty, try standalone task file
                    extracted = self._parse_checklist_markdown(matched_path)
                    if not extracted:
                        single = self._parse_task_file_markdown(matched_path)
                        if single:
                            extracted = [single]

                for t in extracted:
                    if t.id not in seen_ids:
                        seen_ids.add(t.id)
                        all_tasks.append(t)

        if not include_done:
            all_tasks = [t for t in all_tasks if t.status != TaskStatus.DONE]

        return all_tasks

    def reset_tasks_status(
        self,
        task_ids: Sequence[str],
        target_status: str = "ready-for-dev",
    ) -> None:
        """Reset status for given task IDs in roadmap files."""
        target_set = set(task_ids)
        for sf_rel in self.get_status_files():
            sf_path = self.repo_root / sf_rel
            if not sf_path.is_file():
                continue
            try:
                text = sf_path.read_text(encoding="utf-8-sig")
            except OSError:
                continue

            # If checklist: uncheck matching tasks
            lines = text.splitlines()
            modified = False
            new_lines: list[str] = []
            for line in lines:
                cm = _CHECKLIST_TASK_RE.match(line)
                if cm and cm.group(2) in target_set:
                    line = re.sub(r"\[[xX]\]", "[ ]", line, count=1)
                    modified = True
                new_lines.append(line)

            if modified:
                sf_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
                continue

            # If single-file task: update status line or frontmatter
            for tid in target_set:
                if sf_path.stem == tid or sf_path.stem == f"task_{tid}":
                    if _FM_STATUS_RE.search(text):
                        text = _FM_STATUS_RE.sub(f"status: {target_status}", text)
                        sf_path.write_text(text, encoding="utf-8")
                    elif re.search(r"(\*\*Status:\*\*|\bStatus:\s*).*", text):
                        text = re.sub(
                            r"(\*\*Status:\*\*|\bStatus:\s*).*",
                            rf"\g<1> {target_status}",
                            text,
                        )
                        sf_path.write_text(text, encoding="utf-8")

    def cleanup_wave_artifacts(self, wave_id: int, task_ids: Sequence[str]) -> None:
        """Reset task statuses upon wave cleanup/abort."""
        self.reset_tasks_status(task_ids)

    def check_wave_zero_blockers(self, wave: Wave | None = None) -> list[str]:
        """Verify git working tree cleanliness before wave execution."""
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

    def check_preflight_context_gate(self, wave: Wave) -> list[str]:
        """Pre-flight context gate verification."""
        return []

    def get_allowed_write_globs(self, track: WaveTrack) -> list[str]:
        """Return allowed write file patterns for the active track."""
        cfg = self.get_config()
        globs = list(cfg.get("allowed_write_globs", []))
        if not globs:
            globs = ["src/**", "tests/**", "specs/**", ".parallel/**"]
        globs.extend(self.get_status_files())
        return globs

    def get_forbidden_files(self, wave: Wave, track: WaveTrack) -> dict[str, str]:
        """Return dictionary of locked contract files and reasons."""
        forbidden: dict[str, str] = {}
        cfg = self.get_config()
        declared_forbidden = cfg.get("forbidden_files", [])
        reason = "Immutable Custom SDD architecture contract during parallel wave execution"

        for pat in declared_forbidden:
            norm_pat = str(pat).replace("\\", "/")
            matched = False
            for p in sorted(self.repo_root.glob(norm_pat)):
                if p.is_file():
                    rel = p.relative_to(self.repo_root).as_posix()
                    forbidden[rel] = reason
                    matched = True
            if not matched:
                forbidden[norm_pat] = reason

        return forbidden

    def get_track_env_vars(self, track: WaveTrack) -> dict[str, str]:
        """Return environment variables declared in custom kit config."""
        cfg = self.get_config()
        raw_env = cfg.get("env_vars", {})
        if isinstance(raw_env, dict):
            return {str(k): str(v) for k, v in raw_env.items()}
        return {}

    def prepare_worktree_overlay(self, worktree_path: Path, track: WaveTrack) -> list[str]:
        """Prepare worktree overlay files if needed."""
        return []

    def verify_track_spec_scope(
        self,
        worktree_path: Path,
        track: WaveTrack,
        forbidden_files: dict[str, str],
    ) -> list[str]:
        """Verify that track write files do not touch forbidden contracts."""
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
        """Render worker launch command template."""
        cfg = self.get_config()
        tpl = str(cfg.get("worker_command", "/custom-implement {task_id}"))
        task_arg = " ".join(track.task_ids) if track.task_ids else ""
        return f"`{tpl.format(task_id=task_arg).strip()}`"

    def default_artifacts_dir(self) -> Path:
        """Return default directory for custom SDD artifacts."""
        return self.repo_root / ".parallel" / "artifacts"

    def get_status_files(self) -> list[str]:
        """Return relative paths of all files holding task statuses."""
        cfg = self.get_config()
        sources = cfg.get("roadmap_sources", [])
        status_files: list[str] = []
        for src in sources:
            pat = src.get("path") if isinstance(src, dict) else str(src)
            if pat:
                for p in sorted(self.repo_root.glob(pat)):
                    if p.is_file():
                        rel = p.relative_to(self.repo_root).as_posix()
                        if rel not in status_files:
                            status_files.append(rel)
        return status_files

    def get_change_worktree(self, change_id: str) -> Path | None:
        """Return assigned worktree if applicable."""
        return None

    def update_state_on_merge(
        self,
        wave_number: int,
        merged_task_ids: Sequence[str],
    ) -> Path | None:
        """Post-merge hook for state synchronization."""
        return None
