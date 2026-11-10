"""BMAD Method adapter for parsing sprint-status.yaml, epics.md, and story specs."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sdd_parallel.models import EffortTier, TaskNode, TaskStatus, Wave, WaveTrack
from sdd_parallel.status_merger import (
    _DEV_STATUS_HEADER_RE,
    _KV_LINE_RE,
    _extract_status_map,
)

_STORY_KEY_RE = re.compile(r"^(\d+)-(\d+)-(.+)$")
_FILE_PATH_RE = re.compile(r"`((?:src|tests)/[A-Za-z0-9_./-]+\.py)`")
_SHORT_MOD_RE = re.compile(
    r"(?<![/.\w])`?([A-Za-z0-9_]+(?:/[A-Za-z0-9_]+)*\.py)(?:\([a-z0-9_-]+\))?`?(?![/\w])"
)
_CODE_MAP_SECTION_RE = re.compile(r"^##+\s+Code Map\b", re.IGNORECASE)
_NEGATIVE_LINE_RE = re.compile(
    r"^\s*[-*]\s*(?:\*\*)?(?:Без изменений|Не менять|Не трогать"
    r"|Unchanged|Do not modify|Never|Read-only)\b",
    re.IGNORECASE,
)
_EPIC_STORY_HEADER_RE = re.compile(
    r"^(?:###|[-*]\s+\*\*|[-*]\s+)\s*Story\s+(\d+)[.-](\d+)\s*:\s*(.+?)(?:\*\*)?$",
    re.IGNORECASE,
)
_PROPOSAL_FILES_LINE_RE = re.compile(
    r"^\s*[-*]?\s*\*\*(?:Файлы|Files)\s*:?\*\*:?\s*(.+)$",
    re.IGNORECASE,
)
_DEP_REF_RE = re.compile(
    r"(?:depends\s+on|after|requires|зависимость\s+от|после|требует|из|from)\s+(?:story\s+)?`?(\d+)[.-](\d+)`?",
    re.IGNORECASE,
)
_CHAIN_LINE_RE = re.compile(r"(\d+-\d+(?:\s*(?:→|->)\s*\d+-\d+)+)")
_EFFORT_ROW_RE = re.compile(
    r"^\|\s*(\d+-\d+)\b[^|]*\|\s*\*\*(low|medium|high)\*\*\s*\|",
    re.IGNORECASE,
)
_IGNORED_DIRTY_PREFIXES = (
    ".tmp",
    "_bmad-output/parallel-artifacts",
    ".parallel-lock.json",
    "_bmad-output/ultracode",
    "_bmad-output/ucg",
    ".ultracode",
    ".ucg",
    ".bmad-loop",
    "ultracode-verdicts",
    "_bmad-output/verdicts",
)


@dataclass(slots=True)
class _StoryHints:
    title: str | None = None
    files: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    effort: EffortTier | None = None


class BMADAdapter:
    """Read-Only Anti-Corruption Layer for BMAD Method v6/v7 artifacts."""

    kit_id: str = "bmad"

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root.resolve()
        self.impl_dir = self.repo_root / "_bmad-output" / "implementation-artifacts"
        self.plan_dir = self.repo_root / "_bmad-output" / "planning-artifacts"
        self.sprint_status_path = self.impl_dir / "sprint-status.yaml"
        self.epics_path = self.plan_dir / "epics.md"
        self._pkg_prefix = self._detect_src_package_prefix()

    def _detect_src_package_prefix(self) -> str:
        src_dir = self.repo_root / "src"
        if not src_dir.is_dir():
            return "src"
        pkgs = [
            d.name
            for d in sorted(src_dir.iterdir())
            if d.is_dir() and not d.name.startswith((".", "_")) and not d.name.endswith(".egg-info")
        ]
        if len(pkgs) == 1:
            return f"src/{pkgs[0]}"
        return "src"

    def _resolve_short_module(self, short_mod: str) -> str:
        clean = short_mod.replace("\\", "/").strip().lstrip("./")
        if not clean or ".." in clean or clean.startswith(("/", ".")):
            return ""
        if clean.startswith(("src/", "tests/")):
            return clean
        candidate = f"{self._pkg_prefix}/{clean}"
        if (self.repo_root / candidate).is_file():
            return candidate
        pkg_dir = self.repo_root / self._pkg_prefix
        if pkg_dir.is_dir():
            matches = [
                p for p in pkg_dir.rglob(clean) if p.is_file() and not p.name.startswith((".", "_"))
            ]
            if len(matches) == 1:
                return matches[0].relative_to(self.repo_root).as_posix()
        return candidate

    def _find_stories_yaml(self) -> Path | None:
        """Find stories.yaml in implementation-artifacts, planning-artifacts, or repo root."""
        candidates = [
            self.impl_dir / "stories.yaml",
            self.plan_dir / "stories.yaml",
            self.repo_root / "_bmad-output" / "stories.yaml",
            self.repo_root / "stories.yaml",
        ]
        for c in candidates:
            if c.is_file():
                return c
        return None

    def _has_bmad_loop_config(self) -> bool:
        """Check for existence of bmad-loop.toml or .bmad-loop.toml configuration."""
        return (
            (self.repo_root / "bmad-loop.toml").is_file()
            or (self.repo_root / ".bmad-loop.toml").is_file()
        )

    def _get_bmad_loop_config(self) -> dict[str, Any]:
        """Read and parse bmad-loop.toml or .bmad-loop.toml configuration."""
        for cfg_path in (self.repo_root / "bmad-loop.toml", self.repo_root / ".bmad-loop.toml"):
            if cfg_path.is_file():
                try:
                    import tomllib

                    return tomllib.loads(cfg_path.read_text(encoding="utf-8-sig"))
                except Exception:
                    pass
        return {}

    def _find_ticket_files(self) -> list[Path]:
        """Find markdown ticket/issue files in tickets/, stories/, or .scratch/."""
        candidates: list[Path] = []
        search_dirs = [
            self.repo_root / "tickets",
            self.repo_root / "stories",
            self.impl_dir / "stories",
            self.repo_root / ".scratch",
        ]
        for sdir in search_dirs:
            if sdir.is_dir():
                for p in sorted(sdir.rglob("*.md")):
                    if (
                        p.is_file()
                        and p.name.lower() not in {"readme.md", "index.md", "summary.md"}
                        and not p.name.startswith((".", "_"))
                    ):
                        candidates.append(p)
        return candidates

    def _has_ticket_files(self) -> bool:
        return len(self._find_ticket_files()) > 0

    def detect(self) -> bool:
        """Return True if the repository contains BMAD Method artifacts, stories, or tickets."""
        return (
            self.sprint_status_path.is_file()
            or self.epics_path.is_file()
            or (self.repo_root / "_bmad").is_dir()
            or self._find_stories_yaml() is not None
            or self._has_bmad_loop_config()
            or (self.repo_root / ".scratch").is_dir()
            or (self.repo_root / "tickets").is_dir()
        )

    def get_epics_for_wave(self, wave: Wave) -> set[str]:
        """Extract unique epic IDs touched by the tasks in the given wave."""
        epics: set[str] = set()
        for tid in wave.all_task_ids:
            clean = tid.removeprefix("epic-")
            if "-" in clean:
                epic_id = clean.split("-")[0]
                if epic_id:
                    epics.add(epic_id)
            elif clean:
                epics.add(clean)
        return epics

    def find_epic_context(self, epic_id: str) -> Path | None:
        """Find pre-compiled epic context in implementation or planning artifacts."""
        impl_ctx = self.impl_dir / f"epic-{epic_id}-context.md"
        if impl_ctx.is_file():
            return impl_ctx
        plan_ctx = self.plan_dir / f"epic-{epic_id}-context.md"
        if plan_ctx.is_file():
            return plan_ctx
        return None

    def check_epic_contexts(self, wave: Wave) -> list[str]:
        """Check existence of pre-compiled epic-<N>-context.md for epics touched by wave.

        Checks both implementation-artifacts and planning-artifacts directories.
        Returns a list of missing relative paths if not found in either location.
        """
        missing: list[str] = []
        epics = self.get_epics_for_wave(wave)
        for epic_id in sorted(epics):
            if self.find_epic_context(epic_id) is None:
                missing.append(f"_bmad-output/planning-artifacts/epic-{epic_id}-context.md")
        return missing

    def check_wave_zero_blockers(self, wave: Wave | None = None) -> list[str]:
        """Detect uncommitted changes, dirty in-progress stories, or missing epic contexts."""
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
                    for pref in _IGNORED_DIRTY_PREFIXES
                ):
                    continue
                if ("ultracode" in path_part or "ucg" in path_part) and path_part.endswith(".json"):
                    continue
                dirty_lines.append(raw_line.strip())

            if dirty_lines:
                blockers.append(
                    f"Dirty Git working tree ({len(dirty_lines)} modified/untracked files). "
                    "Commit or stash before spawning parallel git worktrees."
                )
        except OSError:
            pass

        if self.sprint_status_path.is_file():
            status_map = _extract_status_map(
                self.sprint_status_path.read_text(encoding="utf-8-sig")
            )
            for key, raw_status in status_map.items():
                if _STORY_KEY_RE.match(key) and raw_status == TaskStatus.IN_PROGRESS.value:
                    blockers.append(
                        f"Story '{key}' is currently 'in-progress'. "
                        "Complete and commit it in Wave 0."
                    )

        if wave is not None:
            missing_contexts = self.check_epic_contexts(wave)
            for ctx_path in missing_contexts:
                blockers.append(
                    f"Missing pre-compiled epic context: '{ctx_path}'. "
                    "Compile it on master before spawning parallel tracks "
                    "to prevent fatal add/add git merge conflicts."
                )

        return blockers

    def _get_spec_text(self, story_key: str, short_id: str) -> str:
        """Return raw spec markdown text if present in implementation artifacts."""
        if not self.impl_dir.is_dir():
            return ""
        candidates = list(self.impl_dir.glob(f"spec-{story_key}*.md"))
        if not candidates:
            candidates = list(self.impl_dir.glob(f"spec-{short_id}-*.md"))
        if candidates:
            try:
                return candidates[0].read_text(encoding="utf-8-sig")
            except Exception:
                pass
        return ""

    def _extract_spec_files_and_deps(
        self, story_key: str, short_id: str
    ) -> tuple[list[str], list[str], dict[str, str]]:
        """Extract Python file paths, explicit deps, and function scope notes from spec."""
        if not self.impl_dir.is_dir():
            return [], [], {}
        candidates = list(self.impl_dir.glob(f"spec-{story_key}*.md"))
        if not candidates:
            candidates = list(self.impl_dir.glob(f"spec-{short_id}-*.md"))
        if not candidates:
            return [], [], {}

        spec_text = candidates[0].read_text(encoding="utf-8-sig")
        in_code_map = False
        code_map_src: list[str] = []
        code_map_tests: list[str] = []
        all_src: list[str] = []
        all_tests: list[str] = []
        deps: list[str] = []
        notes: dict[str, str] = {}

        lines = spec_text.splitlines()
        for i, line in enumerate(lines):
            if _CODE_MAP_SECTION_RE.match(line):
                in_code_map = True
                continue
            if in_code_map and line.startswith("## "):
                in_code_map = False

            for dep_match in _DEP_REF_RE.finditer(line):
                dep_id = f"{dep_match.group(1)}-{dep_match.group(2)}"
                if dep_id != short_id and dep_id not in deps:
                    deps.append(dep_id)

            if _NEGATIVE_LINE_RE.match(line):
                continue

            for match in _FILE_PATH_RE.finditer(line):
                rel = match.group(1)
                is_test = rel.startswith("tests/")
                if is_test:
                    if rel not in all_tests:
                        all_tests.append(rel)
                    if in_code_map and rel not in code_map_tests:
                        code_map_tests.append(rel)
                else:
                    if rel not in all_src:
                        all_src.append(rel)
                    if in_code_map and rel not in code_map_src:
                        code_map_src.append(rel)
                    if in_code_map:
                        rest = line[match.end() :].strip()
                        cleaned_note = re.sub(r"^[\s—–:-]+", "", rest).strip()
                        if not cleaned_note:
                            sub_notes: list[str] = []
                            for next_line in lines[i + 1 :]:
                                if next_line.startswith(("  ", "\t")):
                                    sub = re.sub(r"^\s*[-*]\s*", "", next_line).strip()
                                    if sub:
                                        sub_notes.append(sub)
                                else:
                                    break
                            cleaned_note = "; ".join(sub_notes)
                        if cleaned_note and rel not in notes:
                            notes[rel] = cleaned_note

        if code_map_src:
            return code_map_src, deps, notes
        if all_src:
            return all_src, deps, notes
        return (code_map_tests if code_map_tests else all_tests), deps, notes

    def _parse_epics_and_planning_hints(self) -> dict[str, _StoryHints]:
        """Parse epics.md and planning-artifacts for titles, footprints, deps, and effort."""
        hints: dict[str, _StoryHints] = {}

        # 1. Parse epics.md
        if self.epics_path.is_file():
            epics_text = self.epics_path.read_text(encoding="utf-8-sig")
            current_id: str | None = None
            for line in epics_text.splitlines():
                stripped = line.strip()
                if stripped.startswith(("## ", "---")):
                    hdr = _EPIC_STORY_HEADER_RE.match(stripped)
                    if not hdr:
                        current_id = None
                        if stripped.startswith("## "):
                            continue

                hdr = _EPIC_STORY_HEADER_RE.match(stripped)
                if hdr:
                    e_num, s_num, raw_title = hdr.groups()
                    current_id = f"{e_num}-{s_num}"
                    clean_title = (
                        re.sub(r"\s*\(`[^`]+`\)\s*$", "", raw_title).strip().rstrip("*").strip()
                    )
                    info = hints.setdefault(current_id, _StoryHints())
                    info.title = clean_title
                    continue

                if current_id:
                    info = hints[current_id]
                    for m_full in _FILE_PATH_RE.finditer(line):
                        fp = m_full.group(1)
                        if fp.startswith("src/") and fp not in info.files:
                            info.files.append(fp)
                    for m_short in _SHORT_MOD_RE.finditer(line):
                        resolved = self._resolve_short_module(m_short.group(1))
                        if resolved.startswith("src/") and resolved not in info.files:
                            info.files.append(resolved)
                    for dep_m in _DEP_REF_RE.finditer(line):
                        dep_id = f"{dep_m.group(1)}-{dep_m.group(2)}"
                        if dep_id != current_id and dep_id not in info.depends_on:
                            info.depends_on.append(dep_id)

        # 2. Parse execution-plan-*.md if present in planning-artifacts
        if self.plan_dir.is_dir():
            for plan_file in sorted(self.plan_dir.glob("execution-plan-*.md")):
                text = plan_file.read_text(encoding="utf-8-sig")
                for line in text.splitlines():
                    # Check explicit chain arrows: 11-1 -> 11-4 -> 12-1 -> 12-2
                    for chain_m in _CHAIN_LINE_RE.finditer(line):
                        parts = [
                            p.strip() for p in re.split(r"→|->", chain_m.group(1)) if p.strip()
                        ]
                        for prev_id, nxt_id in zip(parts, parts[1:], strict=False):
                            info = hints.setdefault(nxt_id, _StoryHints())
                            if prev_id != nxt_id and prev_id not in info.depends_on:
                                info.depends_on.append(prev_id)

                    # Check Effort table rows: | 11-2 | **high** |
                    eff_m = _EFFORT_ROW_RE.match(line.strip())
                    if eff_m:
                        sid, raw_eff = eff_m.group(1), eff_m.group(2).lower()
                        info = hints.setdefault(sid, _StoryHints())
                        info.effort = EffortTier(raw_eff)

                    # Check inventory table rows: | 11-2 Title | ... | modules |
                    if line.strip().startswith("|"):
                        cells = [c.strip() for c in line.strip().strip("|").split("|")]
                        if len(cells) >= 3:
                            first_m = re.match(r"^(\d+-\d+)\b", cells[0])
                            if first_m:
                                sid = first_m.group(1)
                                info = hints.setdefault(sid, _StoryHints())
                                for dep_m in _DEP_REF_RE.finditer(line):
                                    dep_id = f"{dep_m.group(1)}-{dep_m.group(2)}"
                                    if dep_id != sid and dep_id not in info.depends_on:
                                        info.depends_on.append(dep_id)
                                for m_short in _SHORT_MOD_RE.finditer(cells[2]):
                                    resolved = self._resolve_short_module(m_short.group(1))
                                    if resolved.startswith("src/") and resolved not in info.files:
                                        info.files.append(resolved)

        # 3. Parse sprint-change-proposal-*.md if present in planning-artifacts
        if self.plan_dir.is_dir():
            for prop_file in sorted(self.plan_dir.glob("sprint-change-proposal-*.md")):
                text = prop_file.read_text(encoding="utf-8-sig")
                current_id = None
                for line in text.splitlines():
                    stripped = line.strip()
                    if stripped.startswith(("## ", "### Epic ", "---")):
                        hdr = _EPIC_STORY_HEADER_RE.match(stripped)
                        if not hdr:
                            current_id = None
                            if stripped.startswith("## ") or stripped.startswith("### Epic "):
                                continue

                    hdr = _EPIC_STORY_HEADER_RE.match(stripped)
                    if hdr:
                        e_num, s_num, raw_title = hdr.groups()
                        current_id = f"{e_num}-{s_num}"
                        clean_title = (
                            re.sub(r"\s*\(`[^`]+`\)\s*$", "", raw_title).strip().rstrip("*").strip()
                        )
                        info = hints.setdefault(current_id, _StoryHints())
                        if not info.title:
                            info.title = clean_title
                        continue

                    if current_id:
                        info = hints[current_id]
                        if _NEGATIVE_LINE_RE.match(line):
                            continue

                        files_m = _PROPOSAL_FILES_LINE_RE.match(stripped)
                        if files_m:
                            content = files_m.group(1)
                            for bt_m in re.finditer(r"`([^`]+)`", content):
                                raw_path = bt_m.group(1).strip()
                                if raw_path.startswith("src/"):
                                    if raw_path not in info.files:
                                        info.files.append(raw_path)
                                elif raw_path.endswith(".py"):
                                    res = self._resolve_short_module(raw_path)
                                    if res.startswith("src/") and res not in info.files:
                                        info.files.append(res)
                            for un_m in re.finditer(
                                r"(?:^|[\s,;])((?:src)/[A-Za-z0-9_./-]+(?:\.[A-Za-z0-9_-]+)?)",
                                content,
                            ):
                                un_path = un_m.group(1).rstrip(".,;")
                                if un_path.startswith("src/") and un_path not in info.files:
                                    info.files.append(un_path)

                        for m_full in _FILE_PATH_RE.finditer(line):
                            fp = m_full.group(1)
                            if fp.startswith("src/") and fp not in info.files:
                                info.files.append(fp)
                        for m_short in _SHORT_MOD_RE.finditer(line):
                            resolved = self._resolve_short_module(m_short.group(1))
                            if resolved.startswith("src/") and resolved not in info.files:
                                info.files.append(resolved)
                        for dep_m in _DEP_REF_RE.finditer(line):
                            dep_id = f"{dep_m.group(1)}-{dep_m.group(2)}"
                            if dep_id != current_id and dep_id not in info.depends_on:
                                info.depends_on.append(dep_id)

        return hints

    @staticmethod
    def _parse_frontmatter_dict(text: str) -> dict[str, Any]:
        """Extract YAML frontmatter dictionary from markdown text."""
        stripped = text.lstrip("\ufeff").strip()
        m = re.match(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", stripped, re.DOTALL)
        if not m:
            return {}
        raw_yaml = m.group(1)
        try:
            from ruamel.yaml import YAML

            yaml = YAML(typ="safe")
            loaded = yaml.load(raw_yaml)
            if isinstance(loaded, dict):
                return {str(k).lower(): v for k, v in loaded.items()}
        except Exception:
            pass
        data: dict[str, Any] = {}
        for line in raw_yaml.splitlines():
            sline = line.strip()
            if ":" in sline and not sline.startswith("#"):
                k, v = sline.split(":", 1)
                data[k.strip().lower()] = v.strip().strip("\"'")
        return data

    @staticmethod
    def _normalize_status(val: str) -> TaskStatus:
        """Normalize raw status strings with support for underscores and synonyms."""
        norm = val.strip().lower().replace("_", "-")
        if norm in {"in-progress", "active", "doing", "wip"}:
            return TaskStatus.IN_PROGRESS
        if norm in {"ready-for-dev", "ready"}:
            return TaskStatus.READY_FOR_DEV
        if norm in {"done", "completed", "complete", "merged"}:
            return TaskStatus.DONE
        return TaskStatus.from_raw(norm)

    def _load_ticket_nodes(self, include_done: bool = False) -> list[TaskNode]:
        """Load tasks from ticket files in tickets/, stories/, or .scratch/."""
        ticket_files = self._find_ticket_files()
        tasks: list[TaskNode] = []
        for tf in ticket_files:
            text = tf.read_text(encoding="utf-8-sig")
            fm = self._parse_frontmatter_dict(text)

            raw_id = fm.get("id") or tf.stem
            ticket_id = str(raw_id).strip()

            raw_title = fm.get("title")
            if not raw_title:
                for line in text.splitlines():
                    sline = line.strip()
                    if sline.startswith("# "):
                        raw_title = sline[2:].strip()
                        break
            if raw_title:
                title = str(raw_title).strip()
            else:
                title = ticket_id.replace("-", " ").capitalize()

            raw_status = fm.get("status")
            if raw_status:
                status = self._normalize_status(str(raw_status))
            else:
                total_items = 0
                done_items = 0
                for line in text.splitlines():
                    m = re.match(r"^\s*[-*]\s*\[([ xX])\]\s*(.+)$", line)
                    if m:
                        total_items += 1
                        if m.group(1).lower() == "x":
                            done_items += 1
                if total_items > 0 and done_items == total_items:
                    status = TaskStatus.DONE
                elif done_items > 0:
                    status = TaskStatus.IN_PROGRESS
                else:
                    status = TaskStatus.READY_FOR_DEV

            if status == TaskStatus.DONE and not include_done:
                continue

            write_files: list[str] = []
            fm_files = fm.get("files") or fm.get("write_files") or fm.get("affected_areas")
            if isinstance(fm_files, list):
                for f in fm_files:
                    clean_f = str(f).replace("\\", "/").strip()
                    resolved = self._resolve_short_module(clean_f)
                    target = resolved if resolved else clean_f
                    if target.startswith("src/") and target not in write_files:
                        write_files.append(target)
            elif isinstance(fm_files, str):
                for part in re.split(r"[,; ]+", fm_files):
                    clean_f = part.strip().strip("`'\"")
                    if clean_f:
                        resolved = self._resolve_short_module(clean_f)
                        target = resolved if resolved else clean_f
                        if target.startswith("src/") and target not in write_files:
                            write_files.append(target)

            for m_full in _FILE_PATH_RE.finditer(text):
                fp = m_full.group(1)
                if fp.startswith("src/") and fp not in write_files:
                    write_files.append(fp)
            for m_short in _SHORT_MOD_RE.finditer(text):
                resolved = self._resolve_short_module(m_short.group(1))
                if resolved.startswith("src/") and resolved not in write_files:
                    write_files.append(resolved)

            depends_on: list[str] = []
            fm_deps = fm.get("depends_on") or fm.get("dependencies") or fm.get("requires")
            if isinstance(fm_deps, list):
                for d in fm_deps:
                    d_str = str(d).strip().strip("`'\"")
                    if d_str and d_str != ticket_id and d_str not in depends_on:
                        depends_on.append(d_str)
            elif isinstance(fm_deps, str):
                for part in re.split(r"[,; ]+", fm_deps):
                    d_str = part.strip().strip("`'\"")
                    if d_str and d_str != ticket_id and d_str not in depends_on:
                        depends_on.append(d_str)

            for dep_m in _DEP_REF_RE.finditer(text):
                dep_id = f"{dep_m.group(1)}-{dep_m.group(2)}"
                if dep_id != ticket_id and dep_id not in depends_on:
                    depends_on.append(dep_id)

            raw_eff = fm.get("effort")
            if isinstance(raw_eff, str):
                eff_lower = raw_eff.lower()
                if eff_lower == "low":
                    effort = EffortTier.LOW
                elif eff_lower == "high":
                    effort = EffortTier.HIGH
                else:
                    effort = EffortTier.MEDIUM
            else:
                if len(write_files) >= 5 or (
                    len(write_files) >= 3 and any("schema" in f for f in write_files)
                ):
                    effort = EffortTier.HIGH
                elif len(write_files) <= 1:
                    effort = EffortTier.LOW
                else:
                    effort = EffortTier.MEDIUM

            epic_id = "epic-tickets"
            try:
                rel_parts = tf.relative_to(self.repo_root).parts
                if ".scratch" in rel_parts:
                    idx = rel_parts.index(".scratch")
                    if len(rel_parts) > idx + 1:
                        epic_id = f"epic-{rel_parts[idx + 1]}"
            except Exception:
                pass
            if epic_id == "epic-tickets" and "-" in ticket_id:
                prefix = ticket_id.split("-")[0]
                if prefix.isdigit():
                    epic_id = f"epic-{prefix}"

            tasks.append(
                TaskNode(
                    id=ticket_id,
                    title=title,
                    epic_id=epic_id,
                    status=status,
                    effort=effort,
                    write_files=write_files,
                    depends_on=depends_on,
                    description=text,
                )
            )
        return tasks

    def _load_from_stories_yaml(
        self,
        stories_yaml_path: Path,
        include_done: bool = False,
        plan_source: str = "auto",
    ) -> list[TaskNode]:
        """Load BMAD stories from stories.yaml (BMAD v7 / bmad-loop)."""
        text = stories_yaml_path.read_text(encoding="utf-8-sig")
        yaml_dict: dict[str, Any] = {}
        try:
            from ruamel.yaml import YAML

            yaml = YAML(typ="safe")
            loaded = yaml.load(text)
            if isinstance(loaded, dict):
                yaml_dict = loaded
        except Exception:
            yaml_dict = _extract_status_map(text)

        stories_entry = yaml_dict.get("stories") or yaml_dict.get("development_status")
        raw_items: dict[str, Any] = {}
        if isinstance(stories_entry, dict):
            raw_items = stories_entry
        elif isinstance(stories_entry, list):
            for it in stories_entry:
                if isinstance(it, dict) and "id" in it:
                    raw_items[str(it["id"])] = it
        else:
            raw_items = yaml_dict

        hints_map = self._parse_epics_and_planning_hints()
        tasks: list[TaskNode] = []

        for key, val in raw_items.items():
            if not isinstance(key, str) or key in {"last_updated", "version"}:
                continue

            clean_val: dict[str, Any] = {}
            if isinstance(val, dict):
                clean_val = val
                raw_status = str(clean_val.get("status", "ready-for-dev"))
            else:
                raw_status = str(val)

            match = _STORY_KEY_RE.match(key)
            if match:
                epic_num, story_num, slug = match.groups()
                short_id = f"{epic_num}-{story_num}"
                epic_id = f"epic-{epic_num}"
            else:
                short_id = key
                epic_id = "epic-1"
                slug = key

            status = self._normalize_status(raw_status)
            if status == TaskStatus.DONE and not include_done:
                continue

            hint = hints_map.get(short_id, _StoryHints())
            title = clean_val.get("title") or hint.title or slug.replace("-", " ").capitalize()

            write_files: list[str] = []
            val_files = clean_val.get("files") or clean_val.get("affected_areas")
            if isinstance(val_files, list):
                for f in val_files:
                    res = self._resolve_short_module(str(f))
                    tf = res if res else str(f)
                    if tf.startswith("src/") and tf not in write_files:
                        write_files.append(tf)

            spec_files, spec_deps, spec_notes = self._extract_spec_files_and_deps(key, short_id)
            if not write_files:
                write_files = spec_files if spec_files else list(hint.files)

            val_deps = clean_val.get("depends_on", [])
            dep_list = [str(d) for d in val_deps] if isinstance(val_deps, list) else []
            depends_on = list(dict.fromkeys([
                *dep_list,
                *hint.depends_on,
                *spec_deps,
            ]))

            effort = EffortTier.MEDIUM
            val_eff = clean_val.get("effort")
            if isinstance(val_eff, str):
                try:
                    effort = EffortTier(val_eff.lower())
                except ValueError:
                    pass
            elif hint.effort is not None:
                effort = hint.effort
            elif len(write_files) >= 5:
                effort = EffortTier.HIGH
            elif len(write_files) <= 1:
                effort = EffortTier.LOW

            spec_text = self._get_spec_text(key, short_id)
            tasks.append(
                TaskNode(
                    id=short_id,
                    title=str(title),
                    epic_id=epic_id,
                    status=status,
                    effort=effort,
                    write_files=write_files,
                    depends_on=depends_on,
                    shared_function_notes=spec_notes,
                    description=spec_text,
                )
            )

        return tasks

    def _load_from_sprint_status(
        self,
        include_done: bool = False,
        plan_source: str = "auto",
    ) -> list[TaskNode]:
        """Load BMAD stories from sprint-status.yaml and enrich from specs/epics."""
        status_map = _extract_status_map(self.sprint_status_path.read_text(encoding="utf-8-sig"))
        hints_map = self._parse_epics_and_planning_hints()
        tasks: list[TaskNode] = []

        for key, raw_status in status_map.items():
            match = _STORY_KEY_RE.match(key)
            if not match:
                continue
            epic_num, story_num, slug = match.groups()
            short_id = f"{epic_num}-{story_num}"
            spec_text = self._get_spec_text(key, short_id)
            fm = self._parse_frontmatter_dict(spec_text) if spec_text else {}

            fm_status = fm.get("status")
            if fm_status:
                status = self._normalize_status(str(fm_status))
            else:
                status = self._normalize_status(raw_status)
            if status == TaskStatus.DONE and not include_done:
                continue

            hint = hints_map.get(short_id, _StoryHints())
            title = fm.get("title") or hint.title or slug.replace("-", " ").capitalize()

            if plan_source == "plan" and hint.files:
                write_files = list(hint.files)
                depends_on = list(dict.fromkeys(hint.depends_on))
                shared_function_notes: dict[str, str] = {}
            else:
                spec_files, spec_deps, spec_notes = self._extract_spec_files_and_deps(key, short_id)
                if plan_source == "specs":
                    write_files = spec_files
                    depends_on = list(dict.fromkeys(spec_deps))
                    shared_function_notes = spec_notes
                else:  # "auto"
                    write_files = spec_files if spec_files else list(hint.files)
                    depends_on = list(dict.fromkeys([*hint.depends_on, *spec_deps]))
                    shared_function_notes = spec_notes

            fm_files = fm.get("files") or fm.get("write_files")
            if isinstance(fm_files, list):
                for f in fm_files:
                    clean_f = str(f).replace("\\", "/").strip()
                    res = self._resolve_short_module(clean_f)
                    tf = res if res else clean_f
                    if tf.startswith("src/") and tf not in write_files:
                        write_files.append(tf)

            fm_deps = fm.get("depends_on") or fm.get("dependencies")
            if isinstance(fm_deps, list):
                for d in fm_deps:
                    d_str = str(d).strip().strip("`'\"")
                    if d_str and d_str != short_id and d_str not in depends_on:
                        depends_on.append(d_str)

            if fm.get("effort"):
                try:
                    effort = EffortTier(str(fm["effort"]).lower())
                except ValueError:
                    effort = EffortTier.MEDIUM
            elif hint.effort is not None:
                effort = hint.effort
            else:
                effort = EffortTier.MEDIUM
                if len(write_files) >= 5 or (
                    len(write_files) >= 3 and any("schema.py" in f for f in write_files)
                ):
                    effort = EffortTier.HIGH
                elif len(write_files) == 1 and not any(
                    s in write_files[0] for s in ("schema.py", "models.py")
                ):
                    effort = EffortTier.LOW

            tasks.append(
                TaskNode(
                    id=short_id,
                    title=str(title),
                    epic_id=f"epic-{epic_num}",
                    status=status,
                    effort=effort,
                    write_files=write_files,
                    depends_on=depends_on,
                    shared_function_notes=shared_function_notes,
                    description=spec_text,
                )
            )

        return tasks

    def load_tasks(
        self,
        include_done: bool = False,
        plan_source: str = "auto",
    ) -> list[TaskNode]:
        """Load BMAD stories from sprint-status.yaml, stories.yaml, or ticket files."""
        # 1. Classical sprint-status.yaml
        if self.sprint_status_path.is_file():
            return self._load_from_sprint_status(include_done=include_done, plan_source=plan_source)

        # 2. bmad-loop.toml with [stories] source = "tickets" OR ticket files
        loop_cfg = self._get_bmad_loop_config()
        stories_cfg = loop_cfg.get("stories", {})
        source_cfg = stories_cfg.get("source") if isinstance(stories_cfg, dict) else None

        if source_cfg == "tickets" or self._has_ticket_files():
            ticket_tasks = self._load_ticket_nodes(include_done=include_done)
            if ticket_tasks:
                return ticket_tasks

        # 3. stories.yaml
        stories_yaml = self._find_stories_yaml()
        if stories_yaml is not None:
            return self._load_from_stories_yaml(
                stories_yaml,
                include_done=include_done,
                plan_source=plan_source,
            )

        # 4. Fallback: ticket files
        ticket_tasks = self._load_ticket_nodes(include_done=include_done)
        if ticket_tasks:
            return ticket_tasks

        return []

    def reset_tasks_status(
        self,
        task_ids: Sequence[str],
        target_status: str = "ready-for-dev",
    ) -> None:
        """Reset task statuses in sprint-status.yaml, stories.yaml, or ticket files."""
        target_set = set(task_ids)
        if self.sprint_status_path.is_file():
            lines = self.sprint_status_path.read_text(encoding="utf-8-sig").splitlines()
            new_lines: list[str] = []
            in_block = False
            for line in lines:
                if _DEV_STATUS_HEADER_RE.match(line):
                    in_block = True
                    new_lines.append(line)
                    continue
                if in_block:
                    if line.strip() and not line.startswith(" ") and not line.startswith("#"):
                        in_block = False
                        new_lines.append(line)
                        continue
                    match = _KV_LINE_RE.match(line)
                    if match:
                        indent, key, _val, comment = match.groups()
                        clean_id = key
                        if "-" in key:
                            parts = key.split("-")
                            if len(parts) >= 2:
                                clean_id = f"{parts[0]}-{parts[1]}"
                        if key in target_set or clean_id in target_set:
                            new_lines.append(f"{indent}{key}: {target_status}{comment}")
                            continue
                new_lines.append(line)
            self.sprint_status_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

        stories_yaml = self._find_stories_yaml()
        if stories_yaml is not None and stories_yaml.is_file():
            text = stories_yaml.read_text(encoding="utf-8-sig")
            new_lines = []
            for line in text.splitlines():
                modified_line = line
                for tid in target_set:
                    if tid in line and ":" in line:
                        modified_line = re.sub(
                            r":\s*[A-Za-z0-9_-]+",
                            f": {target_status}",
                            line,
                            count=1,
                        )
                new_lines.append(modified_line)
            stories_yaml.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

        for tf in self._find_ticket_files():
            if tf.stem in target_set or any(tid in tf.name for tid in target_set):
                lines = tf.read_text(encoding="utf-8-sig").splitlines()
                t_lines = []
                for line in lines:
                    if line.strip().startswith("status:"):
                        indent = line[: len(line) - len(line.lstrip())]
                        line = f"{indent}status: {target_status}"
                    line = re.sub(r"\[[xX]\]", "[ ]", line, count=1)
                    t_lines.append(line)
                tf.write_text("\n".join(t_lines) + "\n", encoding="utf-8")

        if self.impl_dir.is_dir():
            for tid in target_set:
                for p in list(self.impl_dir.glob(f"spec-{tid}*.md")):
                    p.unlink(missing_ok=True)

    def cleanup_wave_artifacts(self, wave_id: int, task_ids: Sequence[str]) -> None:
        """Clean up wave parallel artifacts and generated specs."""
        self.reset_tasks_status(task_ids)
        parallel_dir = self.repo_root / "_bmad-output" / "parallel-artifacts"
        if parallel_dir.is_dir():
            for f in list(parallel_dir.glob(f"*wave-{wave_id}*")):
                f.unlink(missing_ok=True)

    def get_allowed_write_globs(self, track: WaveTrack) -> list[str]:
        """Return allowed write globs for BMAD status files and story specs/tickets."""
        globs: list[str] = [
            "_bmad-output/implementation-artifacts/sprint-status.yaml",
            "stories.yaml",
            "_bmad-output/**/stories.yaml",
            *[f"_bmad-output/implementation-artifacts/spec-{sid}*.md" for sid in track.task_ids],
            *[f"_bmad-output/implementation-artifacts/*{sid}*.md" for sid in track.task_ids],
            *[f"tickets/*{sid}*.md" for sid in track.task_ids],
            *[f"stories/*{sid}*.md" for sid in track.task_ids],
            *[f".scratch/**/issues/*{sid}*.md" for sid in track.task_ids],
        ]
        stories_yaml = self._find_stories_yaml()
        if stories_yaml and stories_yaml.is_file():
            try:
                rel = stories_yaml.relative_to(self.repo_root).as_posix()
                if rel not in globs:
                    globs.append(rel)
            except ValueError:
                pass
        return globs

    def get_forbidden_files(self, wave: Wave, track: WaveTrack) -> dict[str, str]:
        """Return immutable epic context files during wave execution."""
        forbidden: dict[str, str] = {}
        epic_reason = (
            "Pre-compiled Epic Context is immutable during wave execution "
            "(Zero Cache Invalidation invariant)"
        )
        for search_dir in (self.plan_dir, self.impl_dir):
            if search_dir.is_dir():
                for ctx_path in sorted(search_dir.glob("epic-*-context.md")):
                    rel_ctx = ctx_path.relative_to(self.repo_root).as_posix()
                    forbidden.setdefault(rel_ctx, epic_reason)
        for epic_id in sorted(self.get_epics_for_wave(wave)):
            found_ctx = self.find_epic_context(epic_id)
            if found_ctx is not None:
                rel_ctx = found_ctx.relative_to(self.repo_root).as_posix()
                forbidden.setdefault(rel_ctx, epic_reason)
            else:
                rel_ctx = f"_bmad-output/planning-artifacts/epic-{epic_id}-context.md"
                forbidden.setdefault(rel_ctx, epic_reason)
        return forbidden

    def get_track_env_vars(self, track: WaveTrack) -> dict[str, str]:
        """BMAD does not require external environment variables."""
        return {}

    def check_preflight_context_gate(self, wave: Wave) -> list[str]:
        """Check for missing epic context files before wave execution."""
        return self.check_epic_contexts(wave)

    def prepare_worktree_overlay(self, worktree_path: Path, track: WaveTrack) -> list[str]:
        """Write BMAD configuration files and advance stories to ready-for-dev."""
        injected: list[str] = []
        bmad_custom = worktree_path / "_bmad" / "custom"
        bmad_custom.mkdir(parents=True, exist_ok=True)
        toml_content = (
            '[workflow]\npersistent_facts = ["file:{project-root}/.tmp/track-context.md"]\n'
        )
        for name in (
            "bmad-build.user.toml",
            "bmad-build-auto.user.toml",
            "bmad-build.toml",
            "bmad-build-auto.toml",
        ):
            dest = bmad_custom / name
            dest.write_text(toml_content, encoding="utf-8")
            injected.append(dest.relative_to(worktree_path).as_posix())

        sprint_yaml_path = (
            worktree_path / "_bmad-output" / "implementation-artifacts" / "sprint-status.yaml"
        )
        if sprint_yaml_path.is_file():
            try:
                content = sprint_yaml_path.read_text(encoding="utf-8")
                modified = False
                for t_id in track.task_ids:
                    pattern = re.compile(
                        rf"^(\s*{re.escape(t_id)}[\w-]*:\s*)backlog\s*$",
                        re.MULTILINE,
                    )
                    if pattern.search(content):
                        content = pattern.sub(r"\g<1>ready-for-dev", content)
                        modified = True
                if modified:
                    sprint_yaml_path.write_text(content, encoding="utf-8")
            except OSError:
                pass
        return injected

    def verify_track_spec_scope(
        self,
        worktree_path: Path,
        track: WaveTrack,
        forbidden_files: dict[str, str],
    ) -> list[str]:
        """Verify that BMAD story specs do not touch forbidden files."""
        violations: list[str] = []
        for sid in track.task_ids:
            spec_files, _, _ = self._extract_spec_files_and_deps(sid, sid)
            for fp in spec_files:
                norm_fp = fp.replace("\\", "/")
                if norm_fp in forbidden_files:
                    reason = forbidden_files[norm_fp]
                    violations.append(
                        f"Wave track {track.track_id} (story {sid}) "
                        f"spec Code Map touches forbidden file '{norm_fp}': {reason}"
                    )
        return violations

    def render_worker_command(self, track: WaveTrack) -> str:
        """Render operator worker command for BMAD or community ticket track."""
        if not track.task_ids:
            return "`/bmad-build`"
        first_task = track.task_ids[0]
        if (self.repo_root / ".scratch").is_dir() and not self.sprint_status_path.is_file():
            return f"`/implement-spec {first_task}`"
        return f"`/bmad-build {first_task}`"

    def default_artifacts_dir(self) -> Path:
        """Return default artifacts directory for BMAD."""
        return self.repo_root / "_bmad-output" / "parallel-artifacts"

    def get_status_files(self) -> list[str]:
        """Return relative paths of BMAD status files."""
        files: list[str] = []
        if self.sprint_status_path.is_file():
            try:
                files.append(self.sprint_status_path.relative_to(self.repo_root).as_posix())
            except ValueError:
                pass
        stories_yaml = self._find_stories_yaml()
        if stories_yaml and stories_yaml.is_file():
            try:
                rel = stories_yaml.relative_to(self.repo_root).as_posix()
                if rel not in files:
                    files.append(rel)
            except ValueError:
                pass
        for tf in self._find_ticket_files():
            try:
                rel = tf.relative_to(self.repo_root).as_posix()
                if rel not in files:
                    files.append(rel)
            except ValueError:
                pass
        if not files:
            files.append("_bmad-output/implementation-artifacts/sprint-status.yaml")
        return files

    def get_change_worktree(self, change_id: str) -> Path | None:
        """Return Path to assigned worktree if supported by framework or None."""
        return None

    def update_state_on_merge(
        self,
        wave_number: int,
        merged_task_ids: Sequence[str],
    ) -> Path | None:
        """Post-merge hook for BMAD state synchronization."""
        return None
