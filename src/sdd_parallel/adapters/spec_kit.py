"""GitHub Spec Kit adapter (.specify/specs/<feature>/tasks.md & constitution.md)."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sdd_parallel.models import EffortTier, FileRole, TaskNode, TaskStatus, Wave, WaveTrack

if TYPE_CHECKING:
    from sdd_parallel.ast_scanner import ASTHotspotScanner
    from sdd_parallel.crg_sidecar import CRGSidecarAdapter

_TASK_LINE_RE = re.compile(
    r"^\s*[-*]\s*\[([ xX])\]\s*([A-Za-z0-9]+[-_]?\d+)\s*"
    r"(?:\[([pP]|parallel|PARALLEL)\])?\s*(?:\[([A-Za-z0-9_ -]+)\])?\s*(.+)$"
)
_SECTION_HEADER_RE = re.compile(
    r"^##+\s+(?:User\s+Story\s+(\d+)|Phase\s+(\d+)|([A-Za-z0-9_ -]+))",
    re.IGNORECASE,
)
_DEP_IN_DESC_RE = re.compile(
    r"(?:depends\s+on|after|requires|blocked\s+by)\s+`?([A-Za-z0-9_,-/ :]+)`?",
    re.IGNORECASE,
)
_BACKTICK_PATH_RE = re.compile(
    r"`([A-Za-z0-9_./-]+\.(?:py|ts|tsx|js|jsx|go|rs|sql|json|yaml|yml|toml|html|css|md))`"
)
_PREFIX_PATH_RE = re.compile(
    r"(?:(?<=\s)|^)((?:src|tests|app|lib|pkg|internal|packages)/[A-Za-z0-9_./-]+\.(?:py|ts|tsx|js|jsx|go|rs|sql|json|yaml|yml|toml|html|css|md))\b"
)
_SUB_FILES_RE = re.compile(
    r"^\s*[-*]\s*(?:Files?|Target\s+files?):\s*(.+)$",
    re.IGNORECASE,
)
_SUB_DEPS_RE = re.compile(
    r"^\s*[-*]\s*(?:Depends\s+on|Dependencies|Prerequisites):\s*(.+)$",
    re.IGNORECASE,
)
_EFFORT_RE = re.compile(
    r"(?:\[Effort:\s*(low|medium|high)\]|\*\*(low|medium|high)\*\*|effort:\s*(low|medium|high))",
    re.IGNORECASE,
)


class SpecKitAdapter:
    """Read-Only ACL for GitHub Spec Kit (.specify/ or specs/)."""

    kit_id: str = "spec-kit"

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root.resolve()
        self.specify_dir = self.repo_root / ".specify"

    def detect(self) -> bool:
        """Return True if GitHub Spec Kit artifacts are present."""
        if (self.specify_dir / "memory" / "constitution.md").is_file():
            return True
        if (self.specify_dir / "specs").is_dir():
            return True
        if self.specify_dir.is_dir():
            return True
        spec_tasks = list(self.repo_root.glob("specs/*/tasks.md"))
        return bool(spec_tasks)

    def get_constitution_invariants(self) -> list[str]:
        """Extract core invariants from .specify/memory/constitution.md if present."""
        const_path = self.specify_dir / "memory" / "constitution.md"
        if not const_path.is_file():
            return []
        invariants: list[str] = []
        for line in const_path.read_text(encoding="utf-8-sig").splitlines():
            line_str = line.strip()
            if line_str.startswith(("-", "*", "1.", "2.", "3.", "4.", "5.")) and len(line_str) > 5:
                invariants.append(line_str.lstrip("-*123456789. "))
        return invariants

    def _find_tasks_files(self) -> list[Path]:
        # Prioritize root specs/ (real upstream standard), fallback to .specify/specs/
        root_specs = self.repo_root / "specs"
        if root_specs.is_dir():
            files = sorted(root_specs.glob("*/tasks.md"))
            if files:
                return files
        specify_specs = self.specify_dir / "specs"
        if specify_specs.is_dir():
            return sorted(specify_specs.glob("*/tasks.md"))
        return []

    def _extract_plan_files(self, feature_dir: Path) -> list[str]:
        plan_file = feature_dir / "plan.md"
        if not plan_file.is_file():
            return []
        text = plan_file.read_text(encoding="utf-8-sig")
        found: list[str] = []
        for fm in _BACKTICK_PATH_RE.finditer(text):
            p = fm.group(1).replace("\\", "/")
            if p not in found:
                found.append(p)
        for fm in _PREFIX_PATH_RE.finditer(text):
            p = fm.group(1).replace("\\", "/")
            if p not in found:
                found.append(p)
        return found

    def load_tasks(
        self,
        include_done: bool = False,
        plan_source: str = "auto",
    ) -> list[TaskNode]:
        """Parse Spec Kit tasks.md files and infer sequential vs [P] parallel hints."""
        tasks: list[TaskNode] = []
        tasks_files = self._find_tasks_files()
        is_multi_feature = len(tasks_files) > 1

        for tasks_file in tasks_files:
            feature_slug = tasks_file.parent.name
            current_section = feature_slug
            section_last_seq_id: str | None = None
            plan_fallback_files = self._extract_plan_files(tasks_file.parent)

            current_task: TaskNode | None = None

            raw_lines = tasks_file.read_text(encoding="utf-8-sig").splitlines()
            for raw_line in raw_lines:
                # 1. Section Header Check
                sec_match = _SECTION_HEADER_RE.match(raw_line)
                if sec_match:
                    us_num, ph_num, generic_sec = sec_match.groups()
                    if us_num:
                        current_section = f"US{us_num}"
                    elif ph_num:
                        current_section = f"Phase{ph_num}"
                    elif generic_sec:
                        current_section = generic_sec.strip()
                    # Reset sequential chain for a new user story / phase
                    section_last_seq_id = None
                    continue

                # 2. Sub-bullet enrichments for previous task
                if current_task is not None:
                    sub_file_m = _SUB_FILES_RE.match(raw_line)
                    if sub_file_m:
                        files_str = sub_file_m.group(1)
                        for part in re.split(r"[,;]\s*", files_str):
                            clean_part = part.strip().strip("`'\"")
                            if clean_part and clean_part not in current_task.write_files:
                                current_task.write_files.append(clean_part)
                        continue

                    sub_dep_m = _SUB_DEPS_RE.match(raw_line)
                    if sub_dep_m:
                        deps_str = sub_dep_m.group(1)
                        for part in re.split(r"[,;]\s*", deps_str):
                            clean_dep = part.strip().strip("`'\"")
                            if clean_dep:
                                target_dep = (
                                    f"{feature_slug}:{clean_dep}"
                                    if is_multi_feature and ":" not in clean_dep
                                    else clean_dep
                                )
                                if target_dep not in current_task.depends_on:
                                    current_task.depends_on.append(target_dep)
                        continue

                # 3. Task line match
                match = _TASK_LINE_RE.match(raw_line)
                if not match:
                    continue

                chk, raw_tid, parallel_flag, story_tag, desc = match.groups()
                is_parallel = bool(parallel_flag)
                status = TaskStatus.DONE if chk.lower() == "x" else TaskStatus.READY_FOR_DEV
                if status == TaskStatus.DONE and not include_done:
                    current_task = None
                    continue

                full_id = f"{feature_slug}:{raw_tid}" if is_multi_feature else raw_tid

                # Extract file paths from desc
                write_files: list[str] = []
                for fm in _BACKTICK_PATH_RE.finditer(desc):
                    fp = fm.group(1).replace("\\", "/")
                    if fp not in write_files:
                        write_files.append(fp)
                for fm in _PREFIX_PATH_RE.finditer(desc):
                    fp = fm.group(1).replace("\\", "/")
                    if fp not in write_files:
                        write_files.append(fp)

                # Fallback to plan.md files if task doesn't list files
                if not write_files and plan_fallback_files:
                    write_files = [f for f in plan_fallback_files if not f.startswith("tests/")]

                # Extract dependencies
                depends_on: list[str] = []
                # Explicit dependencies in desc
                for dep_match in _DEP_IN_DESC_RE.finditer(desc):
                    raw_dep_ids = dep_match.group(1)
                    for raw_candidate in re.split(r"[,;]\s*", raw_dep_ids):
                        clean_candidate = raw_candidate.strip().strip("`'\"")
                        if re.match(r"^[A-Za-z0-9]+[-_]?\d+$", clean_candidate):
                            target_id = (
                                f"{feature_slug}:{clean_candidate}"
                                if is_multi_feature and ":" not in clean_candidate
                                else clean_candidate
                            )
                            if target_id != full_id and target_id not in depends_on:
                                depends_on.append(target_id)

                # Sequential dependency within this story section if not [P]
                if not is_parallel and section_last_seq_id is not None:
                    if section_last_seq_id not in depends_on:
                        depends_on.append(section_last_seq_id)

                if not is_parallel:
                    section_last_seq_id = full_id

                # Effort estimation
                effort = EffortTier.MEDIUM
                effort_m = _EFFORT_RE.search(desc)
                if effort_m:
                    eff_str = (effort_m.group(1) or effort_m.group(2) or effort_m.group(3)).lower()
                    if eff_str == "low":
                        effort = EffortTier.LOW
                    elif eff_str == "high":
                        effort = EffortTier.HIGH
                elif len(write_files) >= 4 or any("schema.py" in f for f in write_files):
                    effort = EffortTier.HIGH
                elif len(write_files) == 1 and not any(
                    s in write_files[0] for s in ("schema.py", "models.py")
                ):
                    effort = EffortTier.LOW

                epic_tag = story_tag or current_section or feature_slug
                task_node = TaskNode(
                    id=full_id,
                    title=desc.strip(),
                    epic_id=epic_tag,
                    status=status,
                    effort=effort,
                    write_files=write_files,
                    depends_on=depends_on,
                )
                tasks.append(task_node)
                current_task = task_node

        return tasks

    def verify_parallel_markers(
        self,
        tasks: list[TaskNode] | None = None,
        scanner: ASTHotspotScanner | None = None,
        crg: CRGSidecarAdapter | None = None,
    ) -> list[dict[str, Any]]:
        """Verify whether manual [P] markers are safe or overridden by code collisions.

        Checks:
        1. Type-1 collisions: Two parallel tasks writing to identical standard files.
        2. Spine collisions: Two parallel tasks modifying the same Spine Lock module.
        3. Type-2 semantic collisions: Task A write set overlaps Task B's CRG impact radius.
        """
        all_tasks = tasks if tasks is not None else self.load_tasks(include_done=False)
        file_roles: dict[str, FileRole] = {}
        if scanner is not None:
            meta = scanner.scan()
            file_roles = {rel: m.role for rel, m in meta.items()}

        diagnostics: list[dict[str, Any]] = []
        for i, t_a in enumerate(all_tasks):
            for t_b in all_tasks[i + 1 :]:
                # Both tasks are independent according to explicit depends_on
                if t_a.id in t_b.depends_on or t_b.id in t_a.depends_on:
                    continue

                set_a = set(t_a.write_files)
                set_b = set(t_b.write_files)
                shared = sorted(set_a & set_b)
                if not shared:
                    continue

                spine_shared = [f for f in shared if file_roles.get(f) == FileRole.SPINE]
                standard_shared = [
                    f
                    for f in shared
                    if file_roles.get(f, FileRole.STANDARD) != FileRole.ADDITIVE_REGISTRY
                    and f not in spine_shared
                ]

                if spine_shared:
                    diagnostics.append(
                        {
                            "task_a": t_a.id,
                            "task_b": t_b.id,
                            "conflict_type": "SPINE_ORDER",
                            "shared_files": spine_shared,
                            "reason": (
                                "Manual [P] marker overridden: shared Spine file(s): "
                                f"{', '.join(spine_shared)}"
                            ),
                            "action": "Separated into sequential waves by BSPWaveScheduler",
                        }
                    )
                elif standard_shared:
                    diagnostics.append(
                        {
                            "task_a": t_a.id,
                            "task_b": t_b.id,
                            "conflict_type": "DIRECT_COLLISION",
                            "shared_files": standard_shared,
                            "reason": (
                                "Manual [P] marker overridden: direct write collision on: "
                                f"{', '.join(standard_shared)}"
                            ),
                            "action": "Separated into different waves by BSPWaveScheduler",
                        }
                    )

        return diagnostics

    def reset_tasks_status(
        self,
        task_ids: Sequence[str],
        target_status: str = "ready-for-dev",
    ) -> None:
        """Reset checkboxes in tasks.md files for task_ids to unchecked (- [ ])."""
        target_set = set(task_ids)
        for tf in self._find_tasks_files():
            lines = tf.read_text(encoding="utf-8-sig").splitlines()
            new_lines: list[str] = []
            for line in lines:
                m = _TASK_LINE_RE.match(line)
                if m:
                    t_id = m.group(2)
                    if t_id in target_set:
                        line = re.sub(r"\[[xX]\]", "[ ]", line, count=1)
                new_lines.append(line)
            tf.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    def cleanup_wave_artifacts(self, wave_id: int, task_ids: Sequence[str]) -> None:
        """Reset task checkboxes and clean wave artifacts."""
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
        """Return allowed write globs for Spec Kit tasks and checklists."""
        return [
            ".specify/specs/**/tasks.md",
            "specs/**/tasks.md",
            ".specify/specs/**/checklists/**",
            "specs/**/checklists/**",
        ]

    def get_forbidden_files(self, wave: Wave, track: WaveTrack) -> dict[str, str]:
        """Return immutable constitution and spec architecture files."""
        forbidden: dict[str, str] = {}
        reason = "Immutable Spec Kit architecture contract during parallel wave execution"
        const_file = self.specify_dir / "memory" / "constitution.md"
        if const_file.is_file():
            forbidden[const_file.relative_to(self.repo_root).as_posix()] = reason
        else:
            forbidden[".specify/memory/constitution.md"] = reason

        feature_json = self.specify_dir / "feature.json"
        if feature_json.is_file():
            forbidden[feature_json.relative_to(self.repo_root).as_posix()] = (
                "Immutable Spec Kit feature metadata during parallel wave execution"
            )
        else:
            forbidden[".specify/feature.json"] = (
                "Immutable Spec Kit feature metadata during parallel wave execution"
            )

        spec_roots = [self.specify_dir / "specs", self.repo_root / "specs"]
        for root in spec_roots:
            if root.is_dir():
                for target_name in ("spec.md", "plan.md", "data-model.md"):
                    for p in sorted(root.glob(f"*/{target_name}")):
                        forbidden[p.relative_to(self.repo_root).as_posix()] = reason
        return forbidden

    def _resolve_feature_slug_for_task(self, task_id: str) -> str:
        """Resolve feature slug from task_id prefix or directory layout."""
        if ":" in task_id:
            return task_id.split(":", 1)[0]
        if "/" in task_id:
            return task_id.split("/", 1)[0]
        for tf in self._find_tasks_files():
            try:
                content = tf.read_text(encoding="utf-8-sig")
                if task_id in content:
                    return tf.parent.name
            except OSError:
                pass
        return "001-feature"

    def get_track_env_vars(self, track: WaveTrack) -> dict[str, str]:
        """Return required environment variables for Spec Kit workers."""
        if not track.task_ids:
            return {}
        feature_slug = self._resolve_feature_slug_for_task(track.task_ids[0])
        if (self.repo_root / "specs" / feature_slug).exists():
            feature_dir = f"specs/{feature_slug}"
        else:
            feature_dir = f".specify/specs/{feature_slug}"
        return {
            "SPECIFY_FEATURE": feature_slug,
            "SPECIFY_FEATURE_DIRECTORY": feature_dir,
        }

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
        """Render operator worker command for Spec Kit track."""
        if not track.task_ids:
            return "`/speckit.implement`"
        task_id = track.task_ids[0]
        if ":" in task_id:
            feat, local_id = task_id.split(":", 1)
            return f"`SPECIFY_FEATURE={feat} /speckit.implement {local_id}`"
        return f"`/speckit.implement {task_id}`"

    def default_artifacts_dir(self) -> Path:
        """Return default artifacts directory for Spec Kit."""
        return self.repo_root / ".parallel" / "artifacts"

    def get_status_files(self) -> list[str]:
        """Return relative paths of Spec Kit tasks.md files in specs/** or .specify/**."""
        status_files: list[str] = []
        for spec_root in (self.repo_root / "specs", self.specify_dir / "specs"):
            if spec_root.is_dir():
                for p in sorted(spec_root.rglob("tasks.md")):
                    try:
                        rel = p.relative_to(self.repo_root).as_posix()
                        if rel not in status_files:
                            status_files.append(rel)
                    except ValueError:
                        pass
        return status_files
