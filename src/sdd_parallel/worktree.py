"""Git worktree provisioner, non-invasive BMAD context boundary injector, and Merge Gate."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sdd_parallel.adapters import SDDAdapterProtocol, get_adapter
from sdd_parallel.hooks.blast_radius_guard import _matches_any_glob
from sdd_parallel.models import LockManifest, Wave, WavePlan, WaveTrack
from sdd_parallel.status_merger import get_status_merger

_EXCLUDE_ENTRIES = (
    "_bmad/custom/bmad-build.user.toml",
    "_bmad/custom/bmad-build-auto.user.toml",
    "_bmad/custom/bmad-build.toml",
    "_bmad/custom/bmad-build-auto.toml",
    ".parallel-lock.json",
    ".tmp/",
    "ACTIVE_TRACK.md",
)


def default_antigravity_worktrees_dir(project_name: str) -> Path:
    """Return default root directory for Antigravity worktrees:
    ~/.gemini/antigravity/worktrees/<ProjectName>.
    """
    return Path.home() / ".gemini" / "antigravity" / "worktrees" / project_name


def resolve_checkpoint_gates(repo_root: Path) -> list[str]:
    """Resolve stack-agnostic checkpoint quality gates for repo_root."""
    # Priority 1: .parallel-config.json
    cfg_file = repo_root / ".parallel-config.json"
    if cfg_file.is_file():
        try:
            data = json.loads(cfg_file.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict) and "checkpoint_gates" in data:
                gates = data["checkpoint_gates"]
                if isinstance(gates, list):
                    return [str(g) for g in gates]
        except Exception:
            pass

    # Priority 2: [tool.sdd-parallel] in pyproject.toml
    pyproject_file = repo_root / "pyproject.toml"
    pyproject_data: dict[str, Any] = {}
    if pyproject_file.is_file():
        try:
            pyproject_data = tomllib.loads(pyproject_file.read_text(encoding="utf-8-sig"))
            sdd_conf = pyproject_data.get("tool", {}).get("sdd-parallel", {})
            if isinstance(sdd_conf, dict) and "checkpoint_gates" in sdd_conf:
                gates = sdd_conf["checkpoint_gates"]
                if isinstance(gates, list):
                    return [str(g) for g in gates]
        except Exception:
            pass

    # Priority 3: Polymorphic autodetect by stack markers
    # Python (pyproject.toml or pytest.ini)
    pytest_ini = repo_root / "pytest.ini"
    if pyproject_file.is_file() or pytest_ini.is_file():
        tests_dir = repo_root / "tests"
        has_tests = False
        if tests_dir.is_dir() and any(tests_dir.rglob("*.py")):
            has_tests = True
        else:
            for _ in repo_root.glob("test_*.py"):
                has_tests = True
                break
            if not has_tests:
                for _ in repo_root.glob("*_test.py"):
                    has_tests = True
                    break

        if has_tests:
            py_gates: list[str] = ["uv run pytest"]
            tool_dict = pyproject_data.get("tool", {}) if isinstance(pyproject_data, dict) else {}
            if isinstance(tool_dict, dict):
                if "ruff" in tool_dict:
                    py_gates.append("uv run ruff check src/ tests/")
                if "mypy" in tool_dict:
                    py_gates.append("uv run mypy --strict src")
            return py_gates

    # Node.js / TypeScript (package.json)
    package_json = repo_root / "package.json"
    if package_json.is_file():
        try:
            pkg_data = json.loads(package_json.read_text(encoding="utf-8-sig"))
            test_script = str(pkg_data.get("scripts", {}).get("test", ""))
            if test_script and 'echo "Error: no test specified" && exit 1' not in test_script:
                return ["npm test"]
        except Exception:
            pass

    # Rust (Cargo.toml)
    if (repo_root / "Cargo.toml").is_file():
        return ["cargo test"]

    # Go (go.mod)
    if (repo_root / "go.mod").is_file():
        return ["go test ./..."]

    # Fallback for Python / greenfield sandboxes
    return ["uv run pytest"]


def _is_no_tests_collected(gate_cmd: str, returncode: int, stdout: str, stderr: str) -> bool:
    """Return True if a test runner exited non-zero solely because no test files exist yet."""
    combined = f"{stdout}\n{stderr}".lower()
    if "pytest" in gate_cmd and returncode == 5:
        return True
    no_test_markers = (
        "collected 0 items",
        "no tests ran",
        "no test files found",
        "no tests found",
        "no specs found",
    )
    return any(marker in combined for marker in no_test_markers)


def create_directory_link(target: Path, link: Path) -> None:
    """Create a directory junction on Windows or a directory symlink on POSIX."""
    target = target.resolve()
    link = link.resolve()
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.exists() or (hasattr(link, "is_junction") and link.is_junction()) or link.is_symlink():
        remove_directory_link(link)

    if sys.platform == "win32":
        import _winapi

        # CreateJunction is only present in _winapi on Windows
        getattr(_winapi, "CreateJunction")(str(target), str(link))
    else:
        os.symlink(str(target), str(link), target_is_directory=True)


def remove_directory_link(link: Path) -> None:
    """Safely remove a directory junction on Windows or symlink on POSIX.

    Does nothing if link is a regular directory or does not exist.
    """
    is_junc = hasattr(link, "is_junction") and link.is_junction()
    is_sym = link.is_symlink()
    if not (is_junc or is_sym):
        return

    if os.name == "nt":
        if is_junc:
            try:
                os.rmdir(link)
                return
            except OSError:
                pass
        if is_sym:
            link.unlink()
    else:
        if is_sym:
            link.unlink()


class LIFORollbackViolationError(RuntimeError):
    """Raised when an attempt is made to rollback a wave violating LIFO order."""


def read_parallel_state(repo_root: Path) -> dict[str, Any]:
    """Read .parallel/state.json containing monotonic wave history."""
    state_file = repo_root.resolve() / ".parallel" / "state.json"
    if state_file.is_file():
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError):
            pass
    return {"last_merged_wave_id": None, "run_counter": 0, "history": []}


def write_parallel_state(repo_root: Path, state: dict[str, Any]) -> None:
    """Save state dict to .parallel/state.json."""
    state_file = repo_root.resolve() / ".parallel" / "state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _configure_worktree_filemode(worktree_path: Path) -> None:
    """Configure core.fileMode false in worktree so chmod a-w does not pollute git status."""
    subprocess.run(
        ["git", "config", "core.fileMode", "false"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
    )
    subprocess.run(
        ["git", "config", "--worktree", "core.fileMode", "false"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
    )


def _apply_os_readonly_locks(worktree_path: Path, lock: LockManifest) -> list[str]:
    """Apply OS read-only file attribute (chmod a-w) to forbidden files inside the worktree."""
    locked: list[str] = []
    wt = worktree_path.resolve()
    for rel_path in lock.forbidden_files:
        norm = rel_path.replace("\\", "/")
        target = (wt / norm).resolve()
        if target.is_file():
            try:
                mode = os.stat(target).st_mode
                os.chmod(target, mode & ~stat.S_IWRITE & ~stat.S_IWGRP & ~stat.S_IWOTH)
                locked.append(norm)
            except OSError:
                pass
    return locked


def _restore_os_writable(worktree_path: Path, rel_paths: Iterable[str] | None = None) -> None:
    """Restore OS write permissions (chmod u+w) for specified files or all files in worktree."""
    wt = worktree_path.resolve()
    if rel_paths is not None:
        for rel_path in rel_paths:
            norm = rel_path.replace("\\", "/")
            target = (wt / norm).resolve()
            if target.is_file():
                try:
                    mode = os.stat(target).st_mode
                    os.chmod(target, mode | stat.S_IWRITE)
                except OSError:
                    pass
    else:
        for root, _, files in os.walk(wt):
            for f in files:
                p = Path(root) / f
                try:
                    mode = os.stat(p).st_mode
                    if not (mode & stat.S_IWRITE):
                        os.chmod(p, mode | stat.S_IWRITE)
                except OSError:
                    pass


class WorktreeManager:
    """Manages isolated git worktrees, context boundary files, and barrier checkpoint merges."""

    def __init__(
        self,
        repo_root: Path,
        layout: str = "local",
        worktrees_dir: Path | None = None,
        fixed_slots: bool = True,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.layout = layout
        self.fixed_slots = fixed_slots
        if worktrees_dir is not None:
            self.worktrees_dir = worktrees_dir.resolve()
        elif layout == "antigravity":
            self.worktrees_dir = default_antigravity_worktrees_dir(self.repo_root.name).resolve()
        else:
            self.worktrees_dir = (self.repo_root / ".worktrees").resolve()

    def worktree_path_for_track(self, wave_number: int, track_id: str) -> Path:
        """Resolve filesystem path for track worktree according to fixed_slots mode."""
        if self.fixed_slots:
            return self.worktrees_dir / track_id
        return self.worktrees_dir / f"wave-{wave_number}-{track_id}"

    def branch_name_for_track(self, wave_number: int, track_id: str) -> str:
        """Resolve git branch name according to fixed_slots mode."""
        if self.fixed_slots:
            return f"parallel/{track_id}"
        return f"parallel/wave-{wave_number}-{track_id}"

    def local_link_for_track(self, wave_number: int, track_id: str) -> Path:
        """Resolve junction/symlink path under repo_root / .worktrees."""
        local_dir = self.repo_root / ".worktrees"
        if self.fixed_slots:
            return local_dir / track_id
        return local_dir / f"wave-{wave_number}-{track_id}"

    def build_lock_manifest(
        self,
        wave: Wave,
        track: WaveTrack,
        worktree_path: Path | None = None,
        all_spine_files: Iterable[str] = (),
        extra_forbidden_files: Mapping[str, str] | None = None,
        adapter: SDDAdapterProtocol | None = None,
    ) -> LockManifest:
        """Build .parallel-lock.json manifest for a specific track in a wave."""
        wt_resolved = (
            worktree_path.resolve()
            if worktree_path is not None
            else self.worktree_path_for_track(wave.wave_number, track.track_id).resolve()
        )
        forbidden: dict[str, str] = {}

        # Lock files owned exclusively by sibling tracks in the same wave
        for sibling in wave.tracks:
            if sibling.track_id == track.track_id:
                continue
            sibling_stories = ", ".join(sibling.task_ids)
            for excl_file in sibling.exclusive_write_files:
                if excl_file not in track.exclusive_write_files:
                    forbidden[excl_file] = (
                        f"Locked by sibling {sibling.track_id} (story {sibling_stories})"
                    )

        # Lock Spine files not assigned to this track
        for spine_file in all_spine_files:
            norm_spine = spine_file.replace("\\", "/")
            if norm_spine not in track.exclusive_write_files and norm_spine not in forbidden:
                forbidden[norm_spine] = "Locked by Spine Chain invariant"

        if adapter is None:
            _, adapter = get_adapter(self.repo_root)

        # Lock kit-specific files via polymorphic adapter
        for f_path, reason in adapter.get_forbidden_files(wave, track).items():
            norm_f = f_path.replace("\\", "/")
            if norm_f not in forbidden:
                forbidden[norm_f] = reason

        # Lock kit-specific global state files passed explicitly
        if extra_forbidden_files:
            for extra_file, reason in extra_forbidden_files.items():
                norm_extra = extra_file.replace("\\", "/")
                if norm_extra not in forbidden:
                    forbidden[norm_extra] = reason

        allowed_globs = [
            *track.exclusive_write_files,
            *track.additive_shared_files,
            "tests/**",
            ".tmp/**",
            *adapter.get_allowed_write_globs(track),
        ]

        env_vars = adapter.get_track_env_vars(track)

        return LockManifest(
            wave_id=wave.wave_number,
            track_id=track.track_id,
            stories=list(track.task_ids),
            repo_root=self.repo_root.as_posix(),
            worktree_root=wt_resolved.as_posix(),
            allowed_write_globs=allowed_globs,
            additive_shared_files=list(track.additive_shared_files),
            forbidden_files=forbidden,
            env_vars=env_vars,
        )

    def render_track_context_md(
        self,
        wave: Wave,
        track: WaveTrack,
        lock: LockManifest,
        headless: bool = False,
    ) -> str:
        """Render .tmp/track-context.md injected into bmad-build / bmad-build-auto."""
        stories_str = ", ".join(track.task_ids)
        lines: list[str] = [
            "# PARALLEL TRACK CONTEXT BOUNDARY (ENFORCED)",
            f"- **Wave:** {wave.wave_number} | **Track:** `{track.track_id}` | "
            f"**Assigned Stories:** `{stories_str}`",
            f"- **Recommended Model Effort:** `{track.effort.value}`",
            "",
            "## 1. Allowed Exclusive Write Scope",
        ]
        if track.exclusive_write_files:
            for f in track.exclusive_write_files:
                note = track.function_scope_notes.get(f)
                suffix = f" *(Scope: {note})*" if note else ""
                lines.append(f"- `{f}`{suffix}")
        else:
            lines.append("- *(New module files and story spec files)*")

        lines.extend(["", "## 2. Shared Additive Registries (Append-Only)"])
        if track.additive_shared_files:
            for f in track.additive_shared_files:
                note = track.function_scope_notes.get(f, "Additive registration only")
                lines.append(f"- `{f}` — {note}")
        else:
            lines.append("- *(None)*")

        lines.extend(["", "## 3. Forbidden Files (Locked by Sibling Tracks / Spine)"])
        if lock.forbidden_files:
            for f, reason in sorted(lock.forbidden_files.items()):
                lines.append(f"- `{f}` — **{reason}**")
        else:
            lines.append("- *(None)*")

        if lock.env_vars:
            lines.extend(["", "## Required SDD Environment Variables"])
            for k, v in sorted(lock.env_vars.items()):
                lines.append(f"- `export {k}={v}` (Bash) / `$env:{k}=\"{v}\"` (PowerShell)")

        if headless:
            lines.extend(
                [
                    "",
                    "## 4. HEADLESS WORKTREE MODE (Silent File-State Handoff)",
                    "- Do NOT output verbose human walkthroughs in chat.",
                    "- Write full implementation & review records into the story spec file "
                    "(`## Auto Run Result`).",
                    "- End your response with a single compact JSON status line: "
                    '`{"story": "<id>", "status": "done|blocked", "commit": "<sha>"}`.',
                ]
            )

        return "\n".join(lines) + "\n"

    def inject_track_boundary(
        self,
        worktree_path: Path,
        wave: Wave,
        track: WaveTrack,
        lock: LockManifest,
        headless: bool = False,
    ) -> None:
        """Write .tmp/track-context.md, .parallel-lock.json, BMAD overrides, and git exclude."""
        wt = worktree_path.resolve()
        tmp_dir = wt / ".tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)

        # 1. Write .tmp/track-context.md
        context_md = self.render_track_context_md(wave, track, lock, headless=headless)
        (tmp_dir / "track-context.md").write_text(context_md, encoding="utf-8")

        # 2. Write .parallel-lock.json
        (wt / ".parallel-lock.json").write_text(
            json.dumps(lock.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        # 3. Polymorphic adapter overlay
        _, adapter = get_adapter(self.repo_root)
        adapter.prepare_worktree_overlay(wt, track)

        # 4. Write ACTIVE_TRACK.md HUD for visible operator guidance in IDE
        assigned_desc = ", ".join(f"`{t_id}`" for t_id in track.task_ids)
        excl_desc = ", ".join(f"`{f}`" for f in track.exclusive_write_files) or "*(None)*"
        shared_desc = ", ".join(f"`{f}`" for f in track.additive_shared_files) or "*(None)*"
        forbid_lines = [f"- `{f}` — {reason}" for f, reason in lock.forbidden_files.items()]
        forbid_desc = "\n".join(forbid_lines) or "- *(None)*"
        worker_cmd = adapter.render_worker_command(track).strip("`")
        hud_lines = [
            f"# 🎯 ACTIVE WORKTREE: Wave {wave.wave_number} — Track {track.track_id.upper()}",
            f"> **Назначенные задачи:** {assigned_desc}",
            f"> **Разрешённые файлы (Exclusive):** {excl_desc}",
            f"> **Разрешённые файлы (Shared Additive):** {shared_desc}",
            "",
            "## 🛑 Запрещённые файлы (Spine / Чужие треки):",
            forbid_desc,
            "",
            "## 🚀 Команда запуска:",
            "Просто отправьте в чат:",
            "```text",
            worker_cmd,
            "```",
            f"({adapter.kit_id.upper()} автоматически выберет назначенную задачу).",
            "",
        ]
        if lock.env_vars:
            hud_lines.extend(
                [
                    "## 🔑 Required Environment Variables:",
                    *[f"- `{k}={v}`" for k, v in sorted(lock.env_vars.items())],
                    "",
                ]
            )
        (wt / "ACTIVE_TRACK.md").write_text("\n".join(hud_lines), encoding="utf-8")

        # 6. Register injected files in git info/exclude so `git status --porcelain` stays empty
        self._update_git_exclude(wt)

    def _update_git_exclude(self, worktree_path: Path) -> None:
        """Append injected filenames to the worktree's git info/exclude."""
        try:
            proc = subprocess.run(
                ["git", "rev-parse", "--git-path", "info/exclude"],
                cwd=worktree_path,
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                raw_path = Path(proc.stdout.strip())
                exclude_path = raw_path if raw_path.is_absolute() else (worktree_path / raw_path)
                exclude_path.parent.mkdir(parents=True, exist_ok=True)
                existing = (
                    exclude_path.read_text(encoding="utf-8") if exclude_path.is_file() else ""
                )
                lines = set(existing.splitlines())
                appended = [entry for entry in _EXCLUDE_ENTRIES if entry not in lines]
                if appended:
                    with exclude_path.open("a", encoding="utf-8") as fh:
                        if existing and not existing.endswith("\n"):
                            fh.write("\n")
                        for entry in appended:
                            fh.write(f"{entry}\n")
        except OSError:
            pass

    def prepare_wave(
        self,
        plan: WavePlan,
        wave_number: int,
        base_branch: str = "HEAD",
        headless: bool = False,
        dry_run: bool = False,
        extra_forbidden_files: Mapping[str, str] | None = None,
    ) -> list[Path]:
        """Provision git worktrees and inject boundary contracts for all tracks in `wave_number`."""
        target_wave = next((w for w in plan.waves if w.wave_number == wave_number), None)
        if target_wave is None:
            raise ValueError(f"Wave {wave_number} not found in plan")

        _, adapter = get_adapter(self.repo_root, kit=plan.sdd_kit)
        extra_forbid = dict(extra_forbidden_files or {})

        created_paths: list[Path] = []
        for track in target_wave.tracks:
            branch_name = self.branch_name_for_track(wave_number, track.track_id)
            wt_path = self.worktree_path_for_track(wave_number, track.track_id)
            created_paths.append(wt_path)
            if dry_run:
                continue

            wt_path.parent.mkdir(parents=True, exist_ok=True)
            if self.fixed_slots and wt_path.exists() and (wt_path / ".git").exists():
                # Step 1: Unlock previous wave locks before reset
                _restore_os_writable(wt_path)
                # Step 2: In-place branch reset
                _configure_worktree_filemode(wt_path)
                subprocess.run(
                    ["git", "checkout", "-B", branch_name, base_branch],
                    cwd=wt_path,
                    check=True,
                    capture_output=True,
                )
                subprocess.run(
                    ["git", "clean", "-fd"],
                    cwd=wt_path,
                    check=True,
                    capture_output=True,
                )
            else:
                if not wt_path.exists():
                    subprocess.run(
                        ["git", "worktree", "add", "-B", branch_name, str(wt_path), base_branch],
                        cwd=self.repo_root,
                        capture_output=True,
                        check=True,
                    )
                _configure_worktree_filemode(wt_path)

            if self.layout == "antigravity":
                local_link = self.local_link_for_track(wave_number, track.track_id)
                if local_link.resolve() != wt_path.resolve():
                    create_directory_link(target=wt_path, link=local_link)

            lock = self.build_lock_manifest(
                wave=target_wave,
                track=track,
                worktree_path=wt_path,
                all_spine_files=plan.spine_files,
                extra_forbidden_files=extra_forbid,
                adapter=adapter,
            )
            self.inject_track_boundary(
                worktree_path=wt_path,
                wave=target_wave,
                track=track,
                lock=lock,
                headless=headless,
            )
            _apply_os_readonly_locks(wt_path, lock)
        return created_paths

    def cleanup_wave(
        self,
        plan_or_wave: WavePlan | int,
        wave_number: int | None = None,
        force: bool = True,
        remove_directories: bool | None = None,
    ) -> list[Path]:
        """Clean up worktrees, directory links, and temporary state for a specific wave."""
        if isinstance(plan_or_wave, WavePlan):
            plan = plan_or_wave
            wave_num = wave_number if wave_number is not None else 1
            target_wave = next((w for w in plan.waves if w.wave_number == wave_num), None)
            track_ids = [t.track_id for t in target_wave.tracks] if target_wave else []
        else:
            wave_num = plan_or_wave
            track_ids = []

        should_remove = (
            remove_directories if remove_directories is not None else (not self.fixed_slots)
        )

        cleaned: list[Path] = []
        candidate_paths: list[Path] = []
        if track_ids:
            for tid in track_ids:
                if self.fixed_slots:
                    candidate_paths.append(self.worktrees_dir / tid)
                    candidate_paths.append(self.repo_root / ".worktrees" / tid)
                candidate_paths.append(self.worktrees_dir / f"wave-{wave_num}-{tid}")
                candidate_paths.append(self.repo_root / ".worktrees" / f"wave-{wave_num}-{tid}")
        else:
            prefix = f"wave-{wave_num}-"
            for base_dir in (self.worktrees_dir, self.repo_root / ".worktrees"):
                if base_dir.is_dir():
                    for entry in base_dir.iterdir():
                        if entry.name.startswith(prefix) or (
                            self.fixed_slots and entry.name.startswith("track-")
                        ):
                            candidate_paths.append(entry)

        seen: set[str] = set()
        for p in candidate_paths:
            is_junc = hasattr(p, "is_junction") and p.is_junction()
            if is_junc or p.is_symlink():
                remove_directory_link(p)
                cleaned.append(p)
                continue

            if not p.exists():
                continue

            resolved_str = str(p.resolve())
            if resolved_str in seen:
                continue
            seen.add(resolved_str)

            is_fixed_slot_entry = p.name.startswith("track-") and self.fixed_slots
            if not should_remove and is_fixed_slot_entry and (p / ".git").exists():
                _restore_os_writable(p)
                b_name = f"parallel/{p.name}"
                subprocess.run(
                    ["git", "checkout", "-B", b_name, "HEAD"],
                    cwd=p,
                    capture_output=True,
                    check=False,
                )
                subprocess.run(
                    ["git", "clean", "-fd"],
                    cwd=p,
                    capture_output=True,
                    check=False,
                )
                for f_tmp in (".parallel-lock.json", ".tmp/track-context.md", "ACTIVE_TRACK.md"):
                    p_tmp = p / f_tmp
                    if p_tmp.is_file():
                        try:
                            p_tmp.unlink()
                        except OSError:
                            pass
                cleaned.append(p)
            else:
                _restore_os_writable(p)
                cmd = ["git", "worktree", "remove"]
                if force:
                    cmd.append("--force")
                cmd.append(str(p))
                subprocess.run(cmd, cwd=self.repo_root, capture_output=True, text=True, check=False)

                if p.exists() and p.is_dir() and p != self.repo_root:
                    try:
                        shutil.rmtree(p)
                    except OSError:
                        pass

                cleaned.append(p)

        subprocess.run(
            ["git", "worktree", "prune"], cwd=self.repo_root, capture_output=True, check=False
        )
        return cleaned

    def claim_track(
        self,
        plan: WavePlan,
        wave_number: int,
        track_id: str,
        worktree_path: Path,
        headless: bool = False,
    ) -> LockManifest:
        """Attach boundary locks to an already created worktree (e.g. Antigravity UI worktree)."""
        target_wave = next((w for w in plan.waves if w.wave_number == wave_number), None)
        if target_wave is None:
            raise ValueError(f"Wave {wave_number} not found in plan")
        target_track = next((t for t in target_wave.tracks if t.track_id == track_id), None)
        if target_track is None:
            raise ValueError(f"Track '{track_id}' not found in Wave {wave_number}")

        _, adapter = get_adapter(self.repo_root, kit=plan.sdd_kit)
        lock = self.build_lock_manifest(
            wave=target_wave,
            track=target_track,
            worktree_path=worktree_path,
            all_spine_files=plan.spine_files,
            adapter=adapter,
        )
        self.inject_track_boundary(
            worktree_path=worktree_path,
            wave=target_wave,
            track=target_track,
            lock=lock,
            headless=headless,
        )
        return lock

    def verify_wave(self, plan: WavePlan, wave_number: int) -> list[str]:
        """Verify that generated story specs and worktree files do not violate locks."""
        target_wave = next((w for w in plan.waves if w.wave_number == wave_number), None)
        if target_wave is None:
            raise ValueError(f"Wave {wave_number} not found in plan")

        violations: list[str] = []
        for track in target_wave.tracks:
            candidate_path = self.worktree_path_for_track(wave_number, track.track_id)
            if not candidate_path.is_dir():
                dyn_path = self.worktrees_dir / f"wave-{wave_number}-{track.track_id}"
                candidate_path = dyn_path if dyn_path.is_dir() else self.repo_root
            probe_root = candidate_path

            _, adapter = get_adapter(self.repo_root, kit=plan.sdd_kit)
            lock = self.build_lock_manifest(
                wave=target_wave,
                track=track,
                worktree_path=probe_root,
                all_spine_files=plan.spine_files,
                adapter=adapter,
            )
            # 1. Spec code-map / task check
            violations.extend(
                adapter.verify_track_spec_scope(
                    worktree_path=probe_root,
                    track=track,
                    forbidden_files=lock.forbidden_files,
                )
            )

            # 2. Defense-in-Depth Scope Guard: Working tree audit (git status --porcelain)
            if probe_root.is_dir() and (probe_root / ".git").exists():
                st_proc = subprocess.run(
                    ["git", "status", "--porcelain"],
                    cwd=probe_root,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if st_proc.returncode == 0 and st_proc.stdout.strip():
                    for line in st_proc.stdout.splitlines():
                        raw_file = line[3:].strip()
                        if " -> " in raw_file:
                            raw_file = raw_file.split(" -> ")[1].strip()
                        norm_f = raw_file.replace("\\", "/")
                        ignored_prefixes = (
                            ".tmp/",
                            "_bmad/custom/",
                            ".pytest_cache/",
                            "__pycache__/",
                        )
                        if norm_f.startswith(ignored_prefixes):
                            continue
                        if norm_f in {".parallel-lock.json", "ACTIVE_TRACK.md"}:
                            continue
                        if norm_f in lock.forbidden_files:
                            violations.append(
                                f"Wave {wave_number} / {track.track_id}: working tree has modified "
                                f"forbidden file '{norm_f}': {lock.forbidden_files[norm_f]}"
                            )
                        elif (
                            lock.allowed_write_globs
                            and not _matches_any_glob(norm_f, lock.allowed_write_globs)
                            and norm_f not in lock.additive_shared_files
                        ):
                            violations.append(
                                f"Wave {wave_number} / {track.track_id}: working tree "
                                f"file '{norm_f}' is outside declared Allowed Write Scope"
                            )

                # 3. Defense-in-Depth Scope Guard: Committed diff audit against merge-base
                base_rev_proc = subprocess.run(
                    ["git", "rev-parse", "--verify", "HEAD"],
                    cwd=self.repo_root,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                diff_range = "HEAD~1...HEAD"
                if base_rev_proc.returncode == 0 and base_rev_proc.stdout.strip():
                    main_head_sha = base_rev_proc.stdout.strip()
                    mb_proc = subprocess.run(
                        ["git", "merge-base", main_head_sha, "HEAD"],
                        cwd=probe_root,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    if mb_proc.returncode == 0 and mb_proc.stdout.strip():
                        diff_range = f"{mb_proc.stdout.strip()}...HEAD"

                diff_proc = subprocess.run(
                    ["git", "diff", "--name-only", diff_range],
                    cwd=probe_root,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if diff_proc.returncode == 0 and diff_proc.stdout.strip():
                    for raw_diff in diff_proc.stdout.splitlines():
                        norm_d = raw_diff.replace("\\", "/").strip()
                        if not norm_d:
                            continue
                        diff_ignored = (
                            ".tmp/",
                            "_bmad/custom/",
                            ".pytest_cache/",
                            "__pycache__/",
                        )
                        if norm_d.startswith(diff_ignored):
                            continue
                        if norm_d in {".parallel-lock.json", "ACTIVE_TRACK.md"}:
                            continue
                        if norm_d in lock.forbidden_files:
                            violations.append(
                                f"Wave {wave_number} / {track.track_id}: committed changes "
                                f"in forbidden file '{norm_d}': {lock.forbidden_files[norm_d]}"
                            )
                        elif (
                            lock.allowed_write_globs
                            and not _matches_any_glob(norm_d, lock.allowed_write_globs)
                            and norm_d not in lock.additive_shared_files
                        ):
                            violations.append(
                                f"Wave {wave_number} / {track.track_id}: committed file '{norm_d}' "
                                "is outside declared Allowed Write Scope"
                            )
        return violations

    def merge_wave(
        self,
        plan: WavePlan,
        wave_number: int,
        run_quality_gates: bool = True,
        cleanup_worktrees: bool = False,
        dry_run: bool = False,
        strict_gates: bool = False,
        adapter: SDDAdapterProtocol | None = None,
    ) -> list[str]:
        """Merge all track branches in `wave_number`, resolve status files, and run gates."""
        if adapter is None:
            kit_name = getattr(plan, "sdd_kit", "auto") or "auto"
            try:
                adapter = get_adapter(self.repo_root, kit=kit_name)[1]
            except Exception:
                adapter = get_adapter(self.repo_root)[1]

        target_wave = next((w for w in plan.waves if w.wave_number == wave_number), None)
        if target_wave is None:
            raise ValueError(f"Wave {wave_number} not found in plan")

        branch_names: list[str] = []
        for track in target_wave.tracks:
            candidate = self.branch_name_for_track(wave_number, track.track_id)
            chk = subprocess.run(
                ["git", "rev-parse", "--verify", candidate],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
            if chk.returncode != 0:
                dyn_candidate = f"parallel/wave-{wave_number}-{track.track_id}"
                chk_dyn = subprocess.run(
                    ["git", "rev-parse", "--verify", dyn_candidate],
                    cwd=self.repo_root,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if chk_dyn.returncode == 0:
                    candidate = dyn_candidate
            branch_names.append(candidate)

        if dry_run:
            return branch_names

        pre_merge_head = ""
        head_rev = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if head_rev.returncode == 0:
            pre_merge_head = head_rev.stdout.strip()

        merged_branches: list[str] = []

        for track, branch_name in zip(target_wave.tracks, branch_names, strict=True):
            # Check if branch exists in git
            chk = subprocess.run(
                ["git", "rev-parse", "--verify", branch_name],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
            if chk.returncode != 0:
                continue

            # Determine merge-base
            mb_proc = subprocess.run(
                ["git", "merge-base", "HEAD", branch_name],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
            mb_sha = mb_proc.stdout.strip() if mb_proc.returncode == 0 else ""

            # Detect modified status files in branch_name
            diff_args = (
                ["git", "diff", "--name-only", f"{mb_sha}...{branch_name}"]
                if mb_sha
                else ["git", "diff", "--name-only", f"HEAD...{branch_name}"]
            )
            diff_proc = subprocess.run(
                diff_args,
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
            branch_changed = (
                [
                    line.strip().replace("\\", "/")
                    for line in diff_proc.stdout.splitlines()
                    if line.strip()
                ]
                if diff_proc.returncode == 0
                else []
            )

            candidate_status_files = set(branch_changed)
            for st_rel in adapter.get_status_files():
                if (self.repo_root / st_rel).is_file():
                    candidate_status_files.add(st_rel)

            resolved_status_files: dict[str, str] = {}
            for rel in sorted(candidate_status_files):
                merger_fn = get_status_merger(rel)

                if merger_fn is not None:
                    main_file = self.repo_root / rel
                    if main_file.is_file():
                        ours_text = main_file.read_text(encoding="utf-8")
                        theirs_proc = subprocess.run(
                            ["git", "show", f"{branch_name}:{rel}"],
                            cwd=self.repo_root,
                            capture_output=True,
                            text=True,
                            check=False,
                        )
                        if theirs_proc.returncode == 0 and theirs_proc.stdout:
                            theirs_text = theirs_proc.stdout
                            base_text = ours_text
                            if mb_sha:
                                b_proc = subprocess.run(
                                    ["git", "show", f"{mb_sha}:{rel}"],
                                    cwd=self.repo_root,
                                    capture_output=True,
                                    text=True,
                                    check=False,
                                )
                                if b_proc.returncode == 0 and b_proc.stdout:
                                    base_text = b_proc.stdout
                            resolved_status_files[rel] = merger_fn(
                                base_text, ours_text, theirs_text
                            )

            # Perform merge (--no-ff --no-commit so we can cleanly resolve status files)
            merge_proc = subprocess.run(
                ["git", "merge", "--no-ff", "--no-commit", branch_name],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
            for rel, resolved_text in resolved_status_files.items():
                p = self.repo_root / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(resolved_text, encoding="utf-8")
                subprocess.run(
                    ["git", "add", rel],
                    cwd=self.repo_root,
                    capture_output=True,
                    check=True,
                )

            # Check if any unmerged paths remain
            unmerged_proc = subprocess.run(
                ["git", "diff", "--name-only", "--diff-filter=U"],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
            if unmerged_proc.stdout.strip():
                conflicted = unmerged_proc.stdout.strip().splitlines()
                subprocess.run(
                    ["git", "merge", "--abort"],
                    cwd=self.repo_root,
                    capture_output=True,
                    check=False,
                )
                raise RuntimeError(f"Merge conflict while merging {branch_name}: {conflicted}")

            # Commit if merge_head exists or staged changes exist
            merge_head = self.repo_root / ".git" / "MERGE_HEAD"
            status_chk = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
            if merge_head.exists() or status_chk.stdout.strip():
                msg = (
                    f"merge(wave-{wave_number}): integrate {track.track_id} "
                    f"({', '.join(track.task_ids)})"
                )
                subprocess.run(
                    ["git", "commit", "--no-edit", "-m", msg],
                    cwd=self.repo_root,
                    capture_output=True,
                    check=True,
                )
            elif merge_proc.returncode != 0:
                raise RuntimeError(
                    f"git merge failed for {branch_name}: {merge_proc.stderr.strip()}"
                )

            merged_branches.append(branch_name)

            if cleanup_worktrees:
                wt_path = self.worktree_path_for_track(wave_number, track.track_id)
                if not wt_path.exists():
                    wt_path = self.worktrees_dir / f"wave-{wave_number}-{track.track_id}"

                if self.fixed_slots and wt_path.exists() and (wt_path / ".git").exists():
                    # Fixed slot hygiene: reset branch to HEAD and unlock, preserving directory
                    _restore_os_writable(wt_path)
                    subprocess.run(
                        ["git", "checkout", "-B", branch_name, "HEAD"],
                        cwd=wt_path,
                        check=False,
                        capture_output=True,
                    )
                    subprocess.run(
                        ["git", "clean", "-fd"],
                        cwd=wt_path,
                        check=False,
                        capture_output=True,
                    )
                    for f_tmp in (
                        ".parallel-lock.json",
                        ".tmp/track-context.md",
                        "ACTIVE_TRACK.md",
                    ):
                        p_tmp = wt_path / f_tmp
                        if p_tmp.is_file():
                            try:
                                p_tmp.unlink()
                            except OSError:
                                pass
                elif not self.fixed_slots:
                    if self.layout == "antigravity":
                        local_link = (
                            self.repo_root / ".worktrees" / f"wave-{wave_number}-{track.track_id}"
                        )
                        remove_directory_link(local_link)
                    if wt_path.exists():
                        subprocess.run(
                            ["git", "worktree", "remove", "--force", str(wt_path)],
                            cwd=self.repo_root,
                            check=False,
                        )

        if plan.sdd_kit == "gsd" and merged_branches:
            from sdd_parallel.adapters.gsd_hijack import GSDHijackAdapter

            gsd_adapter = GSDHijackAdapter(self.repo_root)
            updated_state = gsd_adapter.update_state_on_merge(
                wave_number=wave_number,
                merged_task_ids=target_wave.all_task_ids,
            )
            if updated_state and updated_state.is_file():
                subprocess.run(
                    ["git", "add", updated_state.relative_to(self.repo_root).as_posix()],
                    cwd=self.repo_root,
                    check=False,
                )
                subprocess.run(
                    [
                        "git",
                        "commit",
                        "-m",
                        f"chore(gsd): update STATE.md after Wave {wave_number} checkpoint merge",
                    ],
                    cwd=self.repo_root,
                    check=False,
                )

        if run_quality_gates and merged_branches:
            gates = (
                target_wave.checkpoint_gates
                if target_wave.checkpoint_gates
                else resolve_checkpoint_gates(self.repo_root)
            )
            for gate_cmd in gates:
                cmd_parts = shlex.split(gate_cmd.replace("\\", "/"))
                gate_proc = subprocess.run(
                    cmd_parts,
                    cwd=self.repo_root,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if gate_proc.returncode != 0:
                    if not strict_gates and _is_no_tests_collected(
                        gate_cmd, gate_proc.returncode, gate_proc.stdout, gate_proc.stderr
                    ):
                        continue
                    if pre_merge_head:
                        subprocess.run(
                            ["git", "reset", "--hard", pre_merge_head],
                            cwd=self.repo_root,
                            capture_output=True,
                            check=False,
                        )
                    rb_msg = (
                        f"Checkpoint quality gate '{gate_cmd}' failed after merging Wave "
                        f"{wave_number} (transactional auto-rollback to {pre_merge_head[:7]} "
                        f"completed):\n{gate_proc.stdout}\n{gate_proc.stderr}"
                    )
                    raise RuntimeError(rb_msg)

        if merged_branches:
            post_merge_head = ""
            post_rev = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
            if post_rev.returncode == 0:
                post_merge_head = post_rev.stdout.strip()

            state = read_parallel_state(self.repo_root)
            state["last_merged_wave_id"] = wave_number
            history: list[dict[str, Any]] = state.get("history", [])
            history.append(
                {
                    "wave_number": wave_number,
                    "pre_merge_commit": pre_merge_head,
                    "post_merge_commit": post_merge_head,
                    "task_ids": target_wave.all_task_ids,
                    "merged_branches": list(merged_branches),
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            )
            state["history"] = history
            write_parallel_state(self.repo_root, state)

        return merged_branches

    def abort_wave(self, wave_id: int) -> list[str]:
        """Abort wave before merge: remove worktrees, prune, delete parallel branches."""
        return WorktreeRollbackManager(
            self.repo_root,
            fixed_slots=self.fixed_slots,
        ).abort_wave(wave_id)

    def rollback_wave(
        self,
        wave_id: int,
        save_backup: bool = True,
        to_commit: str | None = None,
        kit: str = "auto",
        task_ids: Sequence[str] | None = None,
    ) -> str:
        """Rollback latest merged wave in LIFO order with safety backup and SDD reset."""
        return WorktreeRollbackManager(
            self.repo_root,
            fixed_slots=self.fixed_slots,
        ).rollback_wave(
            wave_id=wave_id,
            save_backup=save_backup,
            to_commit=to_commit,
            kit=kit,
            task_ids=task_ids,
        )


class WorktreeRollbackManager:
    """Manages transactional wave rollback, LIFO invariants, and safety backups."""

    def __init__(self, repo_root: Path, fixed_slots: bool = True) -> None:
        self.repo_root = repo_root.resolve()
        self.fixed_slots = fixed_slots
        self.worktree_mgr = WorktreeManager(self.repo_root, fixed_slots=fixed_slots)

    def abort_wave(self, wave_id: int) -> list[str]:
        """Abort wave before merge: remove worktrees, prune, delete parallel branches."""
        # 1. Clean worktrees and junction links
        self.worktree_mgr.cleanup_wave(wave_id, force=True)
        # 2. Prune git worktrees
        subprocess.run(["git", "worktree", "prune"], cwd=self.repo_root, check=False)
        # 3. Find and delete parallel/wave-{wave_id}-* branches
        deleted_branches: list[str] = []
        branch_proc = subprocess.run(
            ["git", "branch", "--list", f"parallel/wave-{wave_id}-*"],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        for raw_b in branch_proc.stdout.splitlines():
            b_name = raw_b.strip().lstrip("*+ ").strip()
            if b_name:
                subprocess.run(
                    ["git", "branch", "-D", b_name],
                    cwd=self.repo_root,
                    capture_output=True,
                    check=False,
                )
                deleted_branches.append(b_name)
        return deleted_branches

    def rollback_wave(
        self,
        wave_id: int,
        save_backup: bool = True,
        to_commit: str | None = None,
        kit: str = "auto",
        task_ids: Sequence[str] | None = None,
    ) -> str:
        """Rollback latest merged wave in LIFO order with safety backup and SDD reset."""
        state = read_parallel_state(self.repo_root)
        last_merged = state.get("last_merged_wave_id")

        if last_merged is not None and last_merged != wave_id:
            raise LIFORollbackViolationError(
                f"LIFO rollback order violation: cannot rollback Wave {wave_id} "
                f"because latest merged wave is Wave {last_merged}."
            )

        history: list[dict[str, Any]] = state.get("history", [])
        entry: dict[str, Any] | None = None
        for h in reversed(history):
            if h.get("wave_number") == wave_id:
                entry = h
                break

        target_commit = to_commit
        if not target_commit and entry:
            target_commit = str(entry.get("pre_merge_commit", ""))

        if not target_commit:
            raise ValueError(
                f"Cannot determine target commit to rollback Wave {wave_id}. "
                "Specify target commit via --to-commit."
            )

        # Invariant: Safety backup branch
        backup_branch = ""
        if save_backup:
            ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            backup_branch = f"backup/wave-{wave_id}-before-rollback-{ts}"
            subprocess.run(
                ["git", "branch", backup_branch, "HEAD"],
                cwd=self.repo_root,
                check=True,
            )

        # Reset HEAD to target commit
        subprocess.run(["git", "reset", "--hard", target_commit], cwd=self.repo_root, check=True)

        # Polymorphic status reset
        resolved_task_ids: list[str] = list(task_ids or [])
        if not resolved_task_ids and entry:
            raw_ids = entry.get("task_ids", [])
            if isinstance(raw_ids, list):
                resolved_task_ids = [str(x) for x in raw_ids]

        _, adapter = get_adapter(self.repo_root, kit=kit)
        if resolved_task_ids:
            adapter.reset_tasks_status(resolved_task_ids, target_status="ready-for-dev")
            adapter.cleanup_wave_artifacts(wave_id, resolved_task_ids)

        # Clean leftover worktrees and parallel branches
        self.abort_wave(wave_id)

        # Update state.json
        if entry and entry in history:
            history.remove(entry)
        state["history"] = history
        state["last_merged_wave_id"] = history[-1]["wave_number"] if history else None
        write_parallel_state(self.repo_root, state)

        return backup_branch
