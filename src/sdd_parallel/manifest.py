"""Execution & correlation manifest, structural signals, and routing contracts (ADR-0009).

Exports deterministic wave execution facts, prevented conflict edges,
and routing override contracts without LLM calls, USD calculations, or external telemetry bloat.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sdd_parallel.models import (
    ConflictType,
    EffortTier,
    RoutingOverride,
    TaskNode,
    WavePlan,
)


@dataclass(frozen=True)
class PreventedConflictEntry:
    """A deterministic conflict edge prevented by wave separation."""

    task_a: str
    task_b: str
    conflict_type: str
    files: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_a": self.task_a,
            "task_b": self.task_b,
            "conflict_type": self.conflict_type,
            "files": list(self.files),
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PreventedConflictEntry:
        return cls(
            task_a=str(data.get("task_a", "")),
            task_b=str(data.get("task_b", "")),
            conflict_type=str(data.get("conflict_type", "")),
            files=[str(f) for f in data.get("files", [])],
            reason=str(data.get("reason", "")),
        )


@dataclass
class TrackManifestEntry:
    """Execution facts and runner correlation metadata for a single track slot."""

    track_id: str
    task_ids: list[str]
    worktree_path: str
    branch: str
    commit_sha: str | None = None
    changed_files: list[str] = field(default_factory=list)
    recommended_effort: str = "medium"
    selected_model: str | None = None
    execution_mode: str = "NORMAL"
    harness: str | None = None
    session_id: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    duration_seconds: float | None = None
    status: str = "ready"

    def to_dict(self) -> dict[str, Any]:
        return {
            "track_id": self.track_id,
            "task_ids": list(self.task_ids),
            "worktree_path": self.worktree_path,
            "branch": self.branch,
            "commit_sha": self.commit_sha,
            "changed_files": list(self.changed_files),
            "recommended_effort": self.recommended_effort,
            "selected_model": self.selected_model,
            "execution_mode": self.execution_mode,
            "harness": self.harness,
            "session_id": self.session_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": self.duration_seconds,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TrackManifestEntry:
        dur = data.get("duration_seconds")
        return cls(
            track_id=str(data.get("track_id", "")),
            task_ids=[str(t) for t in data.get("task_ids", [])],
            worktree_path=str(data.get("worktree_path", "")),
            branch=str(data.get("branch", "")),
            commit_sha=str(data["commit_sha"]) if data.get("commit_sha") else None,
            changed_files=[str(f) for f in data.get("changed_files", [])],
            recommended_effort=str(data.get("recommended_effort", "medium")),
            selected_model=str(data["selected_model"]) if data.get("selected_model") else None,
            execution_mode=str(data.get("execution_mode", "NORMAL")),
            harness=str(data["harness"]) if data.get("harness") else None,
            session_id=str(data["session_id"]) if data.get("session_id") else None,
            started_at=str(data["started_at"]) if data.get("started_at") else None,
            finished_at=str(data["finished_at"]) if data.get("finished_at") else None,
            duration_seconds=float(dur) if dur is not None else None,
            status=str(data.get("status", "ready")),
        )


@dataclass
class WaveRunManifest:
    """Deterministic barrier manifest recorded before slot resetting."""

    schema_version: str = "1.0"
    wave_id: int = 1
    sdd_kit: str = "bmad"
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    tracks: list[TrackManifestEntry] = field(default_factory=list)
    prevented_conflicts: list[PreventedConflictEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "wave_id": self.wave_id,
            "sdd_kit": self.sdd_kit,
            "generated_at": self.generated_at,
            "tracks": [t.to_dict() for t in self.tracks],
            "prevented_conflicts": [c.to_dict() for c in self.prevented_conflicts],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> WaveRunManifest:
        return cls(
            schema_version=str(data.get("schema_version", "1.0")),
            wave_id=int(data.get("wave_id", 1)),
            sdd_kit=str(data.get("sdd_kit", "bmad")),
            generated_at=str(data.get("generated_at", "")),
            tracks=[TrackManifestEntry.from_dict(t) for t in data.get("tracks", [])],
            prevented_conflicts=[
                PreventedConflictEntry.from_dict(c) for c in data.get("prevented_conflicts", [])
            ],
        )


def extract_prevented_conflicts(plan: WavePlan) -> list[PreventedConflictEntry]:
    """Extract deterministic collision and spine order edges prevented by scheduling."""
    target_types = {
        ConflictType.DIRECT_COLLISION,
        ConflictType.SEMANTIC_COLLISION,
        ConflictType.SPINE_ORDER,
    }
    entries: list[PreventedConflictEntry] = []
    for edge in plan.edges:
        if edge.conflict_type in target_types:
            entries.append(
                PreventedConflictEntry(
                    task_a=edge.source,
                    task_b=edge.target,
                    conflict_type=edge.conflict_type.value,
                    files=list(edge.shared_files),
                    reason=edge.reason,
                )
            )
    return entries


def load_routing_overrides(path: Path) -> dict[str, RoutingOverride]:
    """Load and strictly validate external Cognitive Router overrides from JSON file."""
    if not path.is_file():
        raise ValueError(f"Routing override file does not exist: {path.as_posix()}")

    try:
        raw_text = path.read_text(encoding="utf-8-sig")
        data = json.loads(raw_text)
    except Exception as exc:
        raise ValueError(f"Failed to parse routing override file as JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("Invalid routing override format: root must be a JSON object")

    overrides_map = data.get("overrides")
    if not isinstance(overrides_map, dict):
        raise ValueError(
            "Invalid routing override format: missing or non-dict 'overrides' field"
        )

    result: dict[str, RoutingOverride] = {}
    for task_id, item in overrides_map.items():
        if not isinstance(item, dict):
            raise ValueError(
                f"Invalid override entry for task '{task_id}': expected JSON object"
            )
        try:
            override = RoutingOverride.from_dict(str(task_id), item)
        except Exception as exc:
            raise ValueError(
                f"Invalid override configuration for task '{task_id}': {exc}"
            ) from exc
        result[str(task_id)] = override

    return result


def _tier_rank(tier: EffortTier) -> int:
    ranks = {
        EffortTier.LOW: 1,
        EffortTier.MEDIUM: 2,
        EffortTier.HIGH: 3,
    }
    return ranks.get(tier, 2)


def apply_routing_overrides(
    plan: WavePlan,
    overrides: Mapping[str, RoutingOverride],
) -> WavePlan:
    """Apply external Cognitive Router decisions onto a WavePlan."""
    if not overrides:
        return plan

    # 1. Update matching TaskNodes in plan.tasks
    task_map: dict[str, TaskNode] = {t.id: t for t in plan.tasks}
    for task_id, override in overrides.items():
        t_node = task_map.get(task_id)
        if t_node is not None:
            t_node.effort = override.effort
            t_node.model_override = override.model
            t_node.execution_mode = override.execution_mode

    # 2. Update tracks across all waves
    for wave in plan.waves:
        for track in wave.tracks:
            track_overrides = [
                overrides[tid] for tid in track.task_ids if tid in overrides
            ]
            if not track_overrides:
                continue

            # Determine track effort as max effort across all tasks in track
            all_efforts = [
                task_map[tid].effort
                for tid in track.task_ids
                if tid in task_map
            ]
            if not all_efforts:
                all_efforts = [ov.effort for ov in track_overrides]
            max_effort = max(all_efforts, key=_tier_rank)
            track.effort = max_effort

            # Priority for model_override: highest effort task with an explicit model
            sorted_ovs = sorted(
                track_overrides,
                key=lambda o: _tier_rank(o.effort),
                reverse=True,
            )
            for ov in sorted_ovs:
                if ov.model:
                    track.model_override = ov.model
                    break

            # Propagate execution_mode (prefer non-NORMAL from highest effort)
            for ov in sorted_ovs:
                if ov.execution_mode and ov.execution_mode != "NORMAL":
                    track.execution_mode = ov.execution_mode
                    break
            else:
                track.execution_mode = sorted_ovs[0].execution_mode

    return plan


def harvest_wave_manifest(
    repo_root: Path,
    plan: WavePlan,
    wave_id: int,
    sdd_kit: str,
    fixed_slots: bool = True,
    worktrees_dir: Path | None = None,
) -> WaveRunManifest:
    """Collect Git facts and runner session metadata before track slot resetting."""
    target_wave = next((w for w in plan.waves if w.wave_number == wave_id), None)
    if target_wave is None:
        raise ValueError(f"Wave {wave_id} not found in plan")

    wt_base = worktrees_dir or (repo_root / ".worktrees")
    prevented = extract_prevented_conflicts(plan)
    tracks: list[TrackManifestEntry] = []

    for track in target_wave.tracks:
        if fixed_slots:
            wt_path = wt_base / track.track_id
        else:
            wt_path = wt_base / f"wave-{wave_id}-{track.track_id}"

        # Sanitize worktree path
        try:
            rel_to_repo = wt_path.resolve().relative_to(repo_root.resolve()).as_posix()
            sanitized_path = rel_to_repo
        except Exception:
            try:
                rel_to_home = wt_path.resolve().relative_to(Path.home().resolve()).as_posix()
                sanitized_path = f"~/{rel_to_home}"
            except Exception:
                sanitized_path = f".worktrees/{track.track_id}"

        # Branch name
        branch_name = f"parallel/wave-{wave_id}-{track.track_id}"
        chk_branch = subprocess.run(
            ["git", "rev-parse", "--verify", branch_name],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if chk_branch.returncode != 0:
            dyn_branch = f"parallel/wave-{wave_id}-{track.track_id}"
            branch_name = dyn_branch

        # Commit SHA
        commit_sha: str | None = None
        rev_proc = subprocess.run(
            ["git", "rev-parse", branch_name],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if rev_proc.returncode == 0 and rev_proc.stdout.strip():
            commit_sha = rev_proc.stdout.strip()

        # Changed files relative to merge-base or HEAD
        changed_files: list[str] = []
        if commit_sha:
            mb_proc = subprocess.run(
                ["git", "merge-base", "HEAD", branch_name],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
            mb_sha = mb_proc.stdout.strip() if mb_proc.returncode == 0 else ""
            diff_args = (
                ["git", "diff", "--name-only", f"{mb_sha}...{branch_name}"]
                if mb_sha
                else ["git", "diff", "--name-only", f"HEAD...{branch_name}"]
            )
            diff_proc = subprocess.run(
                diff_args,
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
            if diff_proc.returncode == 0 and diff_proc.stdout.strip():
                changed_files = [
                    line.replace("\\", "/")
                    for line in diff_proc.stdout.strip().splitlines()
                    if line.strip()
                ]

        # Runner session reading from .tmp/runner-session.json
        session_file = wt_path / ".tmp" / "runner-session.json"
        session_id: str | None = None
        harness: str | None = None
        started_at: str | None = None
        finished_at: str | None = None
        duration_seconds: float | None = None
        status = "ready"

        if session_file.is_file():
            try:
                s_data = json.loads(session_file.read_text(encoding="utf-8-sig"))
                session_id = (
                    str(s_data.get("session_id"))
                    if s_data.get("session_id")
                    else (
                        str(s_data.get("conversation_id"))
                        if s_data.get("conversation_id")
                        else None
                    )
                )
                harness = str(s_data.get("harness")) if s_data.get("harness") else None
                started_at = str(s_data.get("started_at")) if s_data.get("started_at") else None
                finished_at = str(s_data.get("finished_at")) if s_data.get("finished_at") else None
                if s_data.get("duration_seconds") is not None:
                    duration_seconds = float(s_data["duration_seconds"])
                elif started_at and finished_at:
                    try:
                        t0 = datetime.fromisoformat(started_at)
                        t1 = datetime.fromisoformat(finished_at)
                        duration_seconds = round((t1 - t0).total_seconds(), 2)
                    except Exception:
                        pass
                exit_code = s_data.get("exit_code")
                if exit_code is not None:
                    status = "completed" if int(exit_code) == 0 else "failed"
                elif session_id:
                    status = "in_progress"
            except Exception:
                pass

        tracks.append(
            TrackManifestEntry(
                track_id=track.track_id,
                task_ids=list(track.task_ids),
                worktree_path=sanitized_path,
                branch=branch_name,
                commit_sha=commit_sha,
                changed_files=changed_files,
                recommended_effort=track.effort.value,
                selected_model=track.model_override,
                execution_mode=track.execution_mode,
                harness=harness,
                session_id=session_id,
                started_at=started_at,
                finished_at=finished_at,
                duration_seconds=duration_seconds,
                status=status,
            )
        )

    return WaveRunManifest(
        schema_version="1.0",
        wave_id=wave_id,
        sdd_kit=sdd_kit,
        generated_at=datetime.now(UTC).isoformat(),
        tracks=tracks,
        prevented_conflicts=prevented,
    )


def save_wave_manifest(repo_root: Path, manifest: WaveRunManifest) -> Path:
    """Atomically persist wave manifest and update summary run-manifest.json."""
    parallel_dir = repo_root / ".parallel"
    parallel_dir.mkdir(parents=True, exist_ok=True)

    # 1. Save wave-<K>-manifest.json atomically
    wave_manifest_path = parallel_dir / f"wave-{manifest.wave_id}-manifest.json"
    temp_wave_manifest = parallel_dir / f".tmp-wave-{manifest.wave_id}-manifest.json"
    manifest_dict = manifest.to_dict()
    temp_wave_manifest.write_text(
        json.dumps(manifest_dict, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temp_wave_manifest.replace(wave_manifest_path)

    # 2. Update summary run-manifest.json atomically
    run_manifest_path = parallel_dir / "run-manifest.json"
    temp_run_manifest = parallel_dir / ".tmp-run-manifest.json"
    summary_data: dict[str, Any] = {
        "schema_version": "1.0",
        "waves": {},
    }
    if run_manifest_path.is_file():
        try:
            existing = json.loads(run_manifest_path.read_text(encoding="utf-8-sig"))
            if isinstance(existing, dict):
                summary_data.update(existing)
                if not isinstance(summary_data.get("waves"), dict):
                    summary_data["waves"] = {}
        except Exception:
            pass

    summary_data["waves"][str(manifest.wave_id)] = manifest_dict
    temp_run_manifest.write_text(
        json.dumps(summary_data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temp_run_manifest.replace(run_manifest_path)

    return wave_manifest_path
