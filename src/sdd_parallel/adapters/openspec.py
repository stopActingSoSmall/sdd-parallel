"""OpenSpec adapter (openspec/changes/<change-id>/tasks.md and delta specs)."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sdd_parallel.models import EffortTier, TaskNode, TaskStatus, Wave, WaveTrack

_CHECKLIST_RE = re.compile(r"^\s*[-*]\s*\[([ xX])\]\s*(.+)$")
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL)
_TITLE_RE = re.compile(r"^#+\s+(?:Proposal|Change|RFC)?[:\s]*(.+)$", re.IGNORECASE | re.MULTILINE)
_BACKTICK_PATH_RE = re.compile(
    r"`([A-Za-z0-9_./-]+\.(?:py|ts|tsx|js|jsx|go|rs|sql|json|yaml|yml|toml|html|css|md))`"
)
_PREFIX_PATH_RE = re.compile(
    r"(?:(?<=\s)|^)((?:src|tests|app|lib|pkg|internal|packages)/[A-Za-z0-9_./-]+\.(?:py|ts|tsx|js|jsx|go|rs|sql|json|yaml|yml|toml|html|css|md))\b"
)
_DEP_CHANGE_RE = re.compile(
    r"(?:depends\s+on|requires|prerequisite|blocked\s+by)[:\s]+`?([A-Za-z0-9_,-/ ]+)`?",
    re.IGNORECASE,
)
_INLINE_LIST_RE = re.compile(r"^([A-Za-z0-9_]+):\s*\[(.*?)\]\s*$")
_SCALAR_LINE_RE = re.compile(r"^([A-Za-z0-9_]+):\s*(.+?)\s*$")
_LIST_ITEM_RE = re.compile(r"^\s*-\s*[\"']?([^\"']+)[\"']?\s*$")


class OpenSpecAdapter:
    """Read-Only ACL for Fission-AI OpenSpec (openspec/changes/<change-id>/)."""

    kit_id: str = "openspec"

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root.resolve()
        self.openspec_dir = self.repo_root / "openspec"
        self.changes_dir = self.openspec_dir / "changes"

    def detect(self) -> bool:
        """Return True if OpenSpec directory structure exists."""
        return (
            self.changes_dir.is_dir()
            or (self.openspec_dir / "specs").is_dir()
            or (self.openspec_dir / "project.md").is_file()
            or (self.openspec_dir / "config.yaml").is_file()
        )

    @staticmethod
    def _parse_frontmatter(text: str) -> dict[str, Any]:
        m = _FRONTMATTER_RE.match(text)
        if not m:
            return {}
        block = m.group(1)
        data: dict[str, Any] = {}
        current_list_key: str | None = None

        for raw_line in block.splitlines():
            line = raw_line.rstrip()
            if not line.strip() or line.lstrip().startswith("#"):
                continue

            inline_m = _INLINE_LIST_RE.match(line.strip())
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

    @staticmethod
    def _parse_yaml_or_frontmatter(text: str) -> dict[str, Any]:
        """Parse YAML dictionary from raw YAML text or Markdown frontmatter."""
        stripped = text.lstrip("\ufeff").strip()
        m = _FRONTMATTER_RE.match(stripped)
        raw_yaml = m.group(1) if m else stripped
        try:
            from ruamel.yaml import YAML

            yaml = YAML(typ="safe")
            loaded = yaml.load(raw_yaml)
            if isinstance(loaded, dict):
                return {str(k).lower(): v for k, v in loaded.items()}
        except Exception:
            pass
        return OpenSpecAdapter._parse_frontmatter(text)

    def _is_clean_project_path(
        self,
        fp: str,
        assigned_worktrees: set[str] | None = None,
    ) -> bool:
        """Check whether path is a clean project source file and not a worktree or spec path."""
        norm = fp.replace("\\", "/").strip().lstrip("./")
        if not norm or ".." in norm:
            return False
        if norm.startswith(
            (
                ".worktrees/",
                "worktrees/",
                "openspec/",
                ".openspec/",
                ".openspec.",
                ".git/",
                ".tmp/",
                "tests/",
            )
        ):
            return False
        if norm in {
            "openspec",
            ".openspec",
            ".worktrees",
            "worktrees",
            ".openspec.yaml",
            "openspec.yaml",
        }:
            return False
        if assigned_worktrees:
            for wt in assigned_worktrees:
                clean_wt = wt.replace("\\", "/").strip().lstrip("./")
                if norm == clean_wt or norm.startswith(f"{clean_wt}/"):
                    return False
        return True

    def _extract_files_from_text(
        self,
        text: str,
        assigned_worktrees: set[str] | None = None,
    ) -> list[str]:
        write_files: list[str] = []
        for fm in _BACKTICK_PATH_RE.finditer(text):
            fp = fm.group(1).replace("\\", "/")
            if self._is_clean_project_path(fp, assigned_worktrees) and fp not in write_files:
                write_files.append(fp)
        for fm in _PREFIX_PATH_RE.finditer(text):
            fp = fm.group(1).replace("\\", "/")
            if self._is_clean_project_path(fp, assigned_worktrees) and fp not in write_files:
                write_files.append(fp)
        return write_files

    def get_change_worktree(self, change_id: str) -> Path | None:
        """Return Path to assigned worktree for change_id per WRK-01/WRK-02 or None."""
        change_dir = self.changes_dir / change_id
        if not change_dir.is_dir():
            return None

        # 1. Check .openspec.yaml or openspec.yaml
        for candidate_name in (".openspec.yaml", "openspec.yaml"):
            f = change_dir / candidate_name
            if f.is_file():
                try:
                    data = self._parse_yaml_or_frontmatter(f.read_text(encoding="utf-8-sig"))
                    wt_val = (
                        data.get("worktree")
                        or data.get("worktree_path")
                        or data.get("workspace")
                    )
                    if wt_val and isinstance(wt_val, str) and wt_val.strip():
                        p = Path(wt_val.strip())
                        return p if p.is_absolute() else (self.repo_root / p).resolve()
                except Exception:
                    pass

        # 2. Check proposal.md frontmatter
        prop_file = change_dir / "proposal.md"
        if prop_file.is_file():
            try:
                fm = self._parse_yaml_or_frontmatter(prop_file.read_text(encoding="utf-8-sig"))
                wt_val = (
                    fm.get("worktree")
                    or fm.get("worktree_path")
                    or fm.get("workspace")
                )
                if wt_val and isinstance(wt_val, str) and wt_val.strip():
                    p = Path(wt_val.strip())
                    return p if p.is_absolute() else (self.repo_root / p).resolve()
            except Exception:
                pass

        return None

    def get_assigned_worktrees(self) -> dict[str, Path]:
        """Return mapping of change_id -> assigned worktree Path."""
        if not self.changes_dir.is_dir():
            return {}
        res: dict[str, Path] = {}
        for d in sorted(self.changes_dir.iterdir()):
            if d.is_dir() and d.name not in {"archive", ".archive"} and "archive" not in d.parts:
                wt = self.get_change_worktree(d.name)
                if wt is not None:
                    res[d.name] = wt
        return res

    def load_tasks(
        self,
        include_done: bool = False,
        plan_source: str = "auto",
    ) -> list[TaskNode]:
        """Load active OpenSpec change folders as schedulable TaskNodes."""
        if not self.changes_dir.is_dir():
            return []

        project_file = self.openspec_dir / "project.md"
        change_order: dict[str, int] = {}
        change_epic: dict[str, str] = {}
        if project_file.is_file():
            cur_phase = "openspec"
            order_idx = 0
            for line in project_file.read_text(encoding="utf-8-sig").splitlines():
                sline = line.strip()
                if sline.startswith("## "):
                    cur_phase = sline[3:].strip()
                elif sline.startswith("- ") or sline.startswith("* "):
                    cid = sline[2:].strip().strip("`'\"")
                    if cid:
                        change_order[cid] = order_idx
                        change_epic[cid] = cur_phase
                        order_idx += 1

        tasks: list[TaskNode] = []
        change_dirs = sorted(
            [
                d
                for d in self.changes_dir.iterdir()
                if d.is_dir()
                and d.name not in {"archive", ".archive"}
                and "archive" not in d.parts
            ],
            key=lambda d: (change_order.get(d.name, 999), d.name),
        )

        assigned_wts_dict = self.get_assigned_worktrees()
        assigned_wt_strs = {wt.as_posix() for wt in assigned_wts_dict.values()}
        for wt in assigned_wts_dict.values():
            try:
                assigned_wt_strs.add(wt.relative_to(self.repo_root).as_posix())
            except ValueError:
                pass

        for change_dir in change_dirs:
            change_id = change_dir.name
            tasks_file = change_dir / "tasks.md"
            proposal_file = change_dir / "proposal.md"
            design_file = change_dir / "design.md"
            openspec_yaml_file = change_dir / ".openspec.yaml"
            if not openspec_yaml_file.is_file():
                openspec_yaml_file = change_dir / "openspec.yaml"

            combined_texts: list[str] = []
            prop_text = ""
            if proposal_file.is_file():
                prop_text = proposal_file.read_text(encoding="utf-8-sig")
                combined_texts.append(prop_text)
            if tasks_file.is_file():
                combined_texts.append(tasks_file.read_text(encoding="utf-8-sig"))
            if design_file.is_file():
                combined_texts.append(design_file.read_text(encoding="utf-8-sig"))
            for delta_spec in sorted(change_dir.rglob("specs/*/spec.md")):
                combined_texts.append(delta_spec.read_text(encoding="utf-8-sig"))

            yaml_data: dict[str, Any] = {}
            if openspec_yaml_file.is_file():
                yaml_data = self._parse_yaml_or_frontmatter(
                    openspec_yaml_file.read_text(encoding="utf-8-sig")
                )

            if not combined_texts and not yaml_data:
                continue

            all_text = "\n".join(combined_texts)
            fm = self._parse_yaml_or_frontmatter(prop_text) if prop_text else {}

            total_items = 0
            done_items = 0
            if tasks_file.is_file():
                for line in tasks_file.read_text(encoding="utf-8-sig").splitlines():
                    m = _CHECKLIST_RE.match(line)
                    if m:
                        total_items += 1
                        if m.group(1).lower() == "x":
                            done_items += 1

            fm_status = str(yaml_data.get("status") or fm.get("status", "")).lower()
            if fm_status in {"done", "merged", "completed"}:
                status = TaskStatus.DONE
            elif total_items > 0 and done_items == total_items:
                status = TaskStatus.DONE
            elif done_items > 0 or fm_status in {"in_progress", "in-progress", "active"}:
                status = TaskStatus.IN_PROGRESS
            else:
                status = TaskStatus.READY_FOR_DEV

            if status == TaskStatus.DONE and not include_done:
                continue

            # Extract write files (protect against worktree / openspec pollution)
            write_files = self._extract_files_from_text(all_text, assigned_wt_strs)
            raw_files = (
                yaml_data.get("affected_areas")
                or yaml_data.get("files")
                or yaml_data.get("files_modified")
                or fm.get("affected_areas")
                or fm.get("files")
                or fm.get("files_modified")
            )
            if isinstance(raw_files, list):
                for f in raw_files:
                    clean_f = str(f).replace("\\", "/").strip()
                    if (
                        self._is_clean_project_path(clean_f, assigned_wt_strs)
                        and clean_f not in write_files
                    ):
                        write_files.append(clean_f)

            # Extract dependencies
            depends_on: list[str] = []
            raw_deps = (
                yaml_data.get("depends_on")
                or yaml_data.get("dependencies")
                or yaml_data.get("requires")
                or fm.get("depends_on")
                or fm.get("dependencies")
                or fm.get("requires")
            )
            if isinstance(raw_deps, list):
                for dep in raw_deps:
                    d_str = str(dep).strip().strip("`'\"")
                    if d_str and d_str != change_id and d_str not in depends_on:
                        depends_on.append(d_str)
            elif isinstance(raw_deps, str):
                for part in re.split(r"[,; ]+", raw_deps):
                    d_str = part.strip().strip("`'\"")
                    if d_str and d_str != change_id and d_str not in depends_on:
                        depends_on.append(d_str)

            for dm in _DEP_CHANGE_RE.finditer(all_text):
                raw_deps = dm.group(1)
                for part in re.split(r"[,; ]+", raw_deps):
                    dep_id = part.strip().strip("`'\"")
                    if dep_id and dep_id != change_id and dep_id not in depends_on:
                        depends_on.append(dep_id)

            # Match depends on / requires in proposal without frontmatter
            for match in re.finditer(
                r"(?:depends\s+on|requires|after)\s*[:\-]?\s*([A-Za-z0-9_.\-,\s]+)",
                all_text,
                re.IGNORECASE,
            ):
                for part in re.split(r"[,; ]+", match.group(1)):
                    dep_id = part.strip().strip("`'\"")
                    if dep_id and dep_id != change_id and dep_id not in depends_on:
                        depends_on.append(dep_id)

            # Determine title
            title = yaml_data.get("title") or fm.get("title")
            if not title and prop_text:
                tm = _TITLE_RE.search(prop_text)
                if tm:
                    title = tm.group(1).strip()
            if not title:
                for line in prop_text.splitlines():
                    clean_l = line.strip()
                    if clean_l.startswith("# Proposal:") or clean_l.startswith("# Proposal -"):
                        title = clean_l.split(":", 1)[-1].strip()
                        break
                    if clean_l.startswith("# ") and not clean_l.lower().startswith("# why"):
                        title = clean_l[2:].strip()
                        break
            if not title:
                title = f"OpenSpec change: {change_id}"

            # Determine effort
            effort = EffortTier.MEDIUM
            fm_eff = yaml_data.get("effort") or fm.get("effort")
            if isinstance(fm_eff, str):
                eff_lower = fm_eff.lower()
                if eff_lower == "low":
                    effort = EffortTier.LOW
                elif eff_lower == "high":
                    effort = EffortTier.HIGH
            elif len(write_files) >= 5 or any("schema.py" in f for f in write_files):
                effort = EffortTier.HIGH
            elif len(write_files) == 1:
                effort = EffortTier.LOW

            tasks.append(
                TaskNode(
                    id=change_id,
                    title=str(title),
                    epic_id=change_epic.get(change_id, "openspec"),
                    status=status,
                    effort=effort,
                    write_files=write_files,
                    depends_on=depends_on,
                    description=all_text,
                )
            )

        return tasks

    def load_granular_tasks(self, change_id: str, include_done: bool = False) -> list[TaskNode]:
        """Decompose a single OpenSpec change folder into its individual task checklist items."""
        change_dir = self.changes_dir / change_id
        tasks_file = change_dir / "tasks.md"
        if not tasks_file.is_file():
            return []

        tasks: list[TaskNode] = []
        last_task_id: str | None = None
        task_idx = 1
        for line in tasks_file.read_text(encoding="utf-8-sig").splitlines():
            m = _CHECKLIST_RE.match(line)
            if not m:
                continue

            chk, desc = m.groups()
            sub_id = f"{change_id}-T{task_idx:02d}"
            task_idx += 1
            status = TaskStatus.DONE if chk.lower() == "x" else TaskStatus.READY_FOR_DEV
            if status == TaskStatus.DONE and not include_done:
                continue

            item_files = self._extract_files_from_text(desc)
            depends_on: list[str] = []
            if last_task_id is not None:
                depends_on.append(last_task_id)
            last_task_id = sub_id

            tasks.append(
                TaskNode(
                    id=sub_id,
                    title=desc.strip(),
                    epic_id=change_id,
                    status=status,
                    effort=EffortTier.MEDIUM if len(item_files) >= 3 else EffortTier.LOW,
                    write_files=item_files,
                    depends_on=depends_on,
                    description=desc.strip(),
                )
            )

        return tasks

    def reset_tasks_status(
        self,
        task_ids: Sequence[str],
        target_status: str = "ready-for-dev",
    ) -> None:
        """Reset checklist items for task_ids in openspec changes tasks.md."""
        target_set = set(task_ids)
        if not self.changes_dir.is_dir():
            return
        for tf in self.changes_dir.rglob("tasks.md"):
            lines = tf.read_text(encoding="utf-8-sig").splitlines()
            new_lines: list[str] = []
            for line in lines:
                if any(tid in line for tid in target_set):
                    line = re.sub(r"\[[xX]\]", "[ ]", line, count=1)
                new_lines.append(line)
            tf.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    def cleanup_wave_artifacts(self, wave_id: int, task_ids: Sequence[str]) -> None:
        """Reset tasks and clean wave artifacts."""
        self.reset_tasks_status(task_ids)

    def check_wave_zero_blockers(self, wave: Wave | None = None) -> list[str]:
        """Detect uncommitted changes in Git working tree, ignoring assigned worktrees."""
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
            ignored_prefixes: set[str] = {
                ".tmp",
                "_bmad-output/parallel-artifacts",
                ".parallel",
                ".worktrees",
                ".parallel-lock.json",
            }
            # Ignore registered worktrees from .openspec.yaml per WRK-01 / WRK-04
            for wt_path in self.get_assigned_worktrees().values():
                try:
                    rel = wt_path.relative_to(self.repo_root).as_posix()
                    ignored_prefixes.add(rel)
                except ValueError:
                    pass

            for raw_line in proc.stdout.splitlines():
                if len(raw_line) < 4:
                    continue
                path_part = raw_line[3:].strip().strip('"').replace("\\", "/").rstrip("/")
                if any(
                    path_part == pref or path_part.startswith(f"{pref}/")
                    for pref in ignored_prefixes
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
        """Return allowed write globs for OpenSpec change directories of this track."""
        return [f"openspec/changes/{sid}/**" for sid in track.task_ids]

    def get_forbidden_files(self, wave: Wave, track: WaveTrack) -> dict[str, str]:
        """Return immutable OpenSpec specs and sibling change folders."""
        forbidden: dict[str, str] = {}
        canonical_reason = "Canonical OpenSpec specs are updated only via openspec archive"
        forbidden["openspec/project.md"] = canonical_reason
        forbidden["openspec/config.yaml"] = canonical_reason
        forbidden[".openspec.yaml"] = canonical_reason

        specs_dir = self.openspec_dir / "specs"
        if specs_dir.is_dir():
            for p in sorted(specs_dir.rglob("*")):
                if p.is_file():
                    forbidden[p.relative_to(self.repo_root).as_posix()] = canonical_reason
        else:
            forbidden["openspec/specs/**"] = canonical_reason

        for sibling in wave.tracks:
            if sibling.track_id == track.track_id:
                continue
            for sib_task in sibling.task_ids:
                sib_dir = self.changes_dir / sib_task
                if sib_dir.is_dir():
                    for p in sorted(sib_dir.rglob("*")):
                        if p.is_file():
                            forbidden[p.relative_to(self.repo_root).as_posix()] = (
                                f"Locked by sibling track '{sibling.track_id}'"
                            )
                else:
                    forbidden[f"openspec/changes/{sib_task}/**"] = (
                        f"Locked by sibling track '{sibling.track_id}'"
                    )
                forbidden[f"openspec/changes/{sib_task}/.openspec.yaml"] = (
                    f"Locked by sibling track '{sibling.track_id}'"
                )
        return forbidden

    def get_track_env_vars(self, track: WaveTrack) -> dict[str, str]:
        return {}

    def check_preflight_context_gate(self, wave: Wave) -> list[str]:
        return []

    def prepare_worktree_overlay(self, worktree_path: Path, track: WaveTrack) -> list[str]:
        """Synchronize tasks and specs to worktree per WRK-03 (Plans stay in main checkout)."""
        injected: list[str] = []
        wt = worktree_path.resolve()
        if wt == self.repo_root:
            return injected

        for tid in track.task_ids:
            src_change_dir = self.changes_dir / tid
            if not src_change_dir.is_dir():
                continue
            dest_change_dir = wt / "openspec" / "changes" / tid
            dest_change_dir.mkdir(parents=True, exist_ok=True)
            for fname in (
                "tasks.md",
                "proposal.md",
                "design.md",
                ".openspec.yaml",
                "openspec.yaml",
            ):
                src_f = src_change_dir / fname
                if src_f.is_file():
                    dest_f = dest_change_dir / fname
                    if not dest_f.is_file() or dest_f.read_bytes() != src_f.read_bytes():
                        dest_f.write_bytes(src_f.read_bytes())
                        injected.append(dest_f.relative_to(wt).as_posix())
        return injected

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
        """Render operator worker command for OpenSpec track."""
        return f"`/opsx:apply {track.task_ids[0]}`" if track.task_ids else "`/opsx:apply`"

    def default_artifacts_dir(self) -> Path:
        """Return default artifacts directory for OpenSpec."""
        return self.repo_root / ".parallel" / "artifacts"

    def get_status_files(self) -> list[str]:
        """Return relative paths of OpenSpec tasks.md files."""
        if not self.changes_dir.is_dir():
            return []
        files: list[str] = []
        for p in sorted(self.changes_dir.rglob("tasks.md")):
            try:
                rel = p.relative_to(self.repo_root).as_posix()
                if rel not in files:
                    files.append(rel)
            except ValueError:
                pass
        return files
