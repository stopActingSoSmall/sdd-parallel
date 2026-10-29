"""Canonical data models for SDD-Parallel DAG, conflicts, waves, and worktree locks."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class TaskStatus(StrEnum):
    """Lifecycle status of an SDD story or task."""

    BACKLOG = "backlog"
    READY_FOR_DEV = "ready-for-dev"
    BLOCKED = "blocked"
    IN_PROGRESS = "in-progress"
    REVIEW = "review"
    DONE = "done"

    def rank(self) -> int:
        """Return monotonic progress rank for 3-way status merging."""
        ranks: dict[TaskStatus, int] = {
            TaskStatus.BACKLOG: 0,
            TaskStatus.BLOCKED: 0,
            TaskStatus.READY_FOR_DEV: 1,
            TaskStatus.IN_PROGRESS: 2,
            TaskStatus.REVIEW: 3,
            TaskStatus.DONE: 4,
        }
        return ranks[self]

    @classmethod
    def from_raw(cls, value: str) -> TaskStatus:
        """Parse raw string status from sprint-status.yaml or checklists."""
        normalized = value.strip().lower()
        for member in cls:
            if member.value == normalized:
                return member
        if normalized in {"completed", "complete", "x"}:
            return cls.DONE
        if normalized in {"blocked"}:
            return cls.BLOCKED
        return cls.BACKLOG


class EffortTier(StrEnum):
    """Recommended LLM reasoning budget tier for a task."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class FileRole(StrEnum):
    """Architectural role of a file for blast-radius conflict resolution."""

    STANDARD = "standard"
    SPINE = "spine"
    ADDITIVE_REGISTRY = "additive_registry"


class ConflictType(StrEnum):
    """Type of dependency or collision edge between two tasks."""

    EXPLICIT_DEP = "explicit_dep"
    SPINE_ORDER = "spine_order"
    DIRECT_COLLISION = "direct_collision"
    SEMANTIC_COLLISION = "semantic_collision"


@dataclass(slots=True)
class TaskNode:
    """Canonical representation of an SDD story or task."""

    id: str
    title: str
    epic_id: str = ""
    status: TaskStatus = TaskStatus.BACKLOG
    effort: EffortTier = EffortTier.MEDIUM
    write_files: list[str] = field(default_factory=list)
    read_files: list[str] = field(default_factory=list)
    impact_files: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    shared_function_notes: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "epic_id": self.epic_id,
            "status": self.status.value,
            "effort": self.effort.value,
            "write_files": list(self.write_files),
            "read_files": list(self.read_files),
            "impact_files": list(self.impact_files),
            "depends_on": list(self.depends_on),
            "shared_function_notes": dict(self.shared_function_notes),
        }


@dataclass(slots=True, frozen=True)
class ConflictEdge:
    """Directed dependency or undirected collision edge between two tasks."""

    source: str
    target: str
    conflict_type: ConflictType
    reason: str
    shared_files: tuple[str, ...] = ()
    directed: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "conflict_type": self.conflict_type.value,
            "reason": self.reason,
            "shared_files": list(self.shared_files),
            "directed": self.directed,
        }


@dataclass(slots=True)
class WaveTrack:
    """A single isolated git worktree execution track within a parallel wave."""

    track_id: str
    task_ids: list[str]
    effort: EffortTier
    exclusive_write_files: list[str] = field(default_factory=list)
    additive_shared_files: list[str] = field(default_factory=list)
    function_scope_notes: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "track_id": self.track_id,
            "task_ids": list(self.task_ids),
            "effort": self.effort.value,
            "exclusive_write_files": list(self.exclusive_write_files),
            "additive_shared_files": list(self.additive_shared_files),
            "function_scope_notes": dict(self.function_scope_notes),
        }


@dataclass(slots=True)
class Wave:
    """Barrier-synchronized wave containing up to K parallel tracks."""

    wave_number: int
    tracks: list[WaveTrack]
    checkpoint_gates: list[str] = field(default_factory=list)

    @property
    def all_task_ids(self) -> list[str]:
        return [tid for track in self.tracks for tid in track.task_ids]

    def to_dict(self) -> dict[str, Any]:
        return {
            "wave_number": self.wave_number,
            "tracks": [t.to_dict() for t in self.tracks],
            "checkpoint_gates": list(self.checkpoint_gates),
        }


@dataclass(slots=True)
class WavePlan:
    """Complete multi-wave execution plan produced by the scheduler."""

    sdd_kit: str
    waves: list[Wave]
    edges: list[ConflictEdge]
    wave_zero_blockers: list[str] = field(default_factory=list)
    spine_files: list[str] = field(default_factory=list)
    additive_registries: list[str] = field(default_factory=list)

    @property
    def scheduled_task_ids(self) -> list[str]:
        return [tid for wave in self.waves for tid in wave.all_task_ids]

    def to_dict(self) -> dict[str, Any]:
        return {
            "sdd_kit": self.sdd_kit,
            "wave_zero_blockers": list(self.wave_zero_blockers),
            "spine_files": list(self.spine_files),
            "additive_registries": list(self.additive_registries),
            "waves": [w.to_dict() for w in self.waves],
            "edges": [e.to_dict() for e in self.edges],
        }


@dataclass(slots=True)
class LockManifest:
    """Blast-radius lock file (.parallel-lock.json) enforced by PreToolUse hook."""

    wave_id: int
    track_id: str
    stories: list[str]
    repo_root: str
    worktree_root: str
    allowed_write_globs: list[str]
    additive_shared_files: list[str]
    forbidden_files: dict[str, str]
    env_vars: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "wave_id": self.wave_id,
            "track_id": self.track_id,
            "stories": list(self.stories),
            "repo_root": self.repo_root,
            "worktree_root": self.worktree_root,
            "allowed_write_globs": list(self.allowed_write_globs),
            "additive_shared_files": list(self.additive_shared_files),
            "forbidden_files": dict(self.forbidden_files),
            "env_vars": dict(self.env_vars),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LockManifest:
        return cls(
            wave_id=int(data["wave_id"]),
            track_id=str(data["track_id"]),
            stories=[str(s) for s in data.get("stories", [])],
            repo_root=str(data.get("repo_root", "")),
            worktree_root=str(data.get("worktree_root", "")),
            allowed_write_globs=[str(g) for g in data.get("allowed_write_globs", [])],
            additive_shared_files=[str(f) for f in data.get("additive_shared_files", [])],
            forbidden_files={str(k): str(v) for k, v in data.get("forbidden_files", {}).items()},
            env_vars={str(k): str(v) for k, v in data.get("env_vars", {}).items()},
        )
