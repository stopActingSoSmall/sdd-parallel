"""Deterministic Bulk-Synchronous Parallel (BSP) wave scheduler and DAG solver."""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence

from sdd_parallel.models import (
    ConflictEdge,
    ConflictType,
    FileRole,
    TaskNode,
    Wave,
    WavePlan,
    WaveTrack,
)


class CyclicDependencyError(ValueError):
    """Raised when directed dependencies among tasks contain a cycle."""


class CoverageInvariantError(RuntimeError):
    """Raised when scheduled waves do not cover 100% of input tasks."""


class BSPWaveScheduler:
    """Computes conflict edges, verifies acyclicity, and packs tasks into K-track waves."""

    def __init__(
        self,
        max_tracks: int = 3,
        file_roles: dict[str, FileRole] | None = None,
    ) -> None:
        if max_tracks < 1:
            raise ValueError("max_tracks must be >= 1")
        self.max_tracks = max_tracks
        self.file_roles: dict[str, FileRole] = dict(file_roles or {})

    def get_file_role(self, path: str) -> FileRole:
        normalized = path.replace("\\", "/").lstrip("./")
        parts = normalized.strip("/").split("/")
        filename = parts[-1] if parts else ""
        if "tests" in parts or filename.startswith("test_") or filename.endswith("_test.py"):
            return FileRole.STANDARD
        if normalized in self.file_roles:
            return self.file_roles[normalized]
        if filename in {"schema.py", "models.py"}:
            return FileRole.SPINE
        if filename in {"cli.py", "__init__.py"} or normalized.endswith("/mcp/server.py"):
            return FileRole.ADDITIVE_REGISTRY
        return FileRole.STANDARD

    @staticmethod
    def _can_reach(start: str, target: str, adj: dict[str, set[str]]) -> bool:
        visited: set[str] = set()
        queue = deque([start])
        while queue:
            curr = queue.popleft()
            if curr == target:
                return True
            for nxt in adj.get(curr, ()):
                if nxt not in visited:
                    visited.add(nxt)
                    queue.append(nxt)
        return False

    def build_edges(self, tasks: Sequence[TaskNode]) -> list[ConflictEdge]:
        """Construct directed dependencies and undirected collision edges across tasks."""
        edges: list[ConflictEdge] = []
        task_map = {t.id: t for t in tasks}
        explicit_adj: dict[str, set[str]] = {t.id: set() for t in tasks}

        # 1. Explicit dependencies
        for task in tasks:
            for dep_id in task.depends_on:
                if dep_id in task_map:
                    explicit_adj[dep_id].add(task.id)
                    edges.append(
                        ConflictEdge(
                            source=dep_id,
                            target=task.id,
                            conflict_type=ConflictType.EXPLICIT_DEP,
                            reason=f"{task.id} explicitly depends on {dep_id}",
                            directed=True,
                        )
                    )

        # 2. Pairwise file & semantic blast-radius analysis
        for i, task_a in enumerate(tasks):
            write_a = {f.replace("\\", "/") for f in task_a.write_files}
            impact_a = {f.replace("\\", "/") for f in task_a.impact_files}
            for task_b in tasks[i + 1 :]:
                write_b = {f.replace("\\", "/") for f in task_b.write_files}
                impact_b = {f.replace("\\", "/") for f in task_b.impact_files}

                shared_writes = sorted(write_a & write_b)
                spine_shared = [f for f in shared_writes if self.get_file_role(f) == FileRole.SPINE]
                standard_shared = [
                    f
                    for f in shared_writes
                    if self.get_file_role(f) == FileRole.STANDARD
                    and not self._has_isolated_function_scope(task_a, task_b, f)
                ]

                if spine_shared:
                    # Respect existing explicit dependency direction
                    if self._can_reach(task_b.id, task_a.id, explicit_adj):
                        src_id, dst_id = task_b.id, task_a.id
                    elif self._can_reach(task_a.id, task_b.id, explicit_adj):
                        src_id, dst_id = task_a.id, task_b.id
                    else:
                        src_id, dst_id = task_a.id, task_b.id
                    if not self._can_reach(dst_id, src_id, explicit_adj):
                        explicit_adj[src_id].add(dst_id)
                        edges.append(
                            ConflictEdge(
                                source=src_id,
                                target=dst_id,
                                conflict_type=ConflictType.SPINE_ORDER,
                                reason=f"Shared Spine Lock file(s): {', '.join(spine_shared)}",
                                shared_files=tuple(spine_shared),
                                directed=True,
                            )
                        )
                elif standard_shared:
                    edges.append(
                        ConflictEdge(
                            source=task_a.id,
                            target=task_b.id,
                            conflict_type=ConflictType.DIRECT_COLLISION,
                            reason=f"Direct write collision on {', '.join(standard_shared)}",
                            shared_files=tuple(standard_shared),
                            directed=False,
                        )
                    )
                else:
                    # Check Type-2 Semantic Collision: W(A) \cap I(B) or W(B) \cap I(A)
                    sem_overlap = sorted(
                        ((write_a & impact_b) | (write_b & impact_a))
                        - {
                            f
                            for f in (write_a | write_b)
                            if self.get_file_role(f) == FileRole.ADDITIVE_REGISTRY
                        }
                    )
                    if sem_overlap:
                        edges.append(
                            ConflictEdge(
                                source=task_a.id,
                                target=task_b.id,
                                conflict_type=ConflictType.SEMANTIC_COLLISION,
                                reason=(
                                    "Type-2 semantic impact radius overlap on "
                                    f"{', '.join(sem_overlap)}"
                                ),
                                shared_files=tuple(sem_overlap),
                                directed=False,
                            )
                        )

        return edges

    @staticmethod
    def _has_isolated_function_scope(task_a: TaskNode, task_b: TaskNode, file_path: str) -> bool:
        """Return True if both tasks declare distinct isolated function scopes for file_path."""
        scope_a = task_a.shared_function_notes.get(file_path)
        scope_b = task_b.shared_function_notes.get(file_path)
        return bool(scope_a and scope_b and scope_a != scope_b)

    def schedule(
        self,
        tasks: Sequence[TaskNode],
        sdd_kit: str = "bmad",
        wave_zero_blockers: Sequence[str] = (),
    ) -> WavePlan:
        """Schedule tasks into barrier-synchronized parallel waves with K tracks."""
        if not tasks:
            return WavePlan(
                sdd_kit=sdd_kit,
                waves=[],
                edges=[],
                wave_zero_blockers=list(wave_zero_blockers),
            )

        edges = self.build_edges(tasks)
        task_order = [t.id for t in tasks]
        task_map = {t.id: t for t in tasks}

        # Build directed graph and undirected conflict adjacency
        predecessors: dict[str, set[str]] = {t.id: set() for t in tasks}
        successors: dict[str, set[str]] = {t.id: set() for t in tasks}
        undirected_conflicts: dict[str, set[str]] = {t.id: set() for t in tasks}

        for edge in edges:
            if edge.directed:
                if edge.source in predecessors and edge.target in predecessors:
                    predecessors[edge.target].add(edge.source)
                    successors[edge.source].add(edge.target)
            else:
                undirected_conflicts[edge.source].add(edge.target)
                undirected_conflicts[edge.target].add(edge.source)

        # Verify acyclicity using Kahn's algorithm
        self._assert_acyclic(task_order, predecessors, successors)

        unscheduled = set(task_order)
        completed: set[str] = set()
        waves: list[Wave] = []
        wave_num = 1

        while unscheduled:
            # Candidate tasks whose directed predecessors are all completed
            ready = [
                tid
                for tid in task_order
                if tid in unscheduled and predecessors[tid].issubset(completed)
            ]
            if not ready:
                raise CyclicDependencyError(
                    f"Deadlock while scheduling remaining tasks: {sorted(unscheduled)}"
                )

            selected_ids: list[str] = []
            for tid in ready:
                if len(selected_ids) >= self.max_tracks:
                    break
                # Check undirected collision against already selected tasks in this wave
                if any(other in undirected_conflicts[tid] for other in selected_ids):
                    continue
                selected_ids.append(tid)

            tracks: list[WaveTrack] = []
            for idx, tid in enumerate(selected_ids):
                t_node = task_map[tid]
                track_label = f"track-{chr(ord('a') + idx)}"
                exclusive_files: list[str] = []
                additive_files: list[str] = []
                for f in t_node.write_files:
                    norm_f = f.replace("\\", "/")
                    if self.get_file_role(norm_f) == FileRole.ADDITIVE_REGISTRY:
                        additive_files.append(norm_f)
                    else:
                        exclusive_files.append(norm_f)

                tracks.append(
                    WaveTrack(
                        track_id=track_label,
                        task_ids=[tid],
                        effort=t_node.effort,
                        exclusive_write_files=exclusive_files,
                        additive_shared_files=additive_files,
                        function_scope_notes=dict(t_node.shared_function_notes),
                    )
                )
                unscheduled.remove(tid)
                completed.add(tid)

            waves.append(Wave(wave_number=wave_num, tracks=tracks))
            wave_num += 1

        spine_files = sorted(
            {
                f.replace("\\", "/")
                for t in tasks
                for f in t.write_files
                if self.get_file_role(f) == FileRole.SPINE
            }
        )
        additive_registries = sorted(
            {
                f.replace("\\", "/")
                for t in tasks
                for f in t.write_files
                if self.get_file_role(f) == FileRole.ADDITIVE_REGISTRY
            }
        )

        plan = WavePlan(
            sdd_kit=sdd_kit,
            waves=waves,
            edges=edges,
            wave_zero_blockers=list(wave_zero_blockers),
            spine_files=spine_files,
            additive_registries=additive_registries,
        )

        # Enforce 100% Task Coverage Invariant
        if set(plan.scheduled_task_ids) != set(task_order):
            missing = set(task_order) - set(plan.scheduled_task_ids)
            raise CoverageInvariantError(f"Tasks lost during scheduling: {sorted(missing)}")

        return plan

    @staticmethod
    def _assert_acyclic(
        task_order: list[str],
        predecessors: dict[str, set[str]],
        successors: dict[str, set[str]],
    ) -> None:
        in_degree = {tid: len(predecessors[tid]) for tid in task_order}
        queue = deque(tid for tid in task_order if in_degree[tid] == 0)
        visited = 0
        while queue:
            curr = queue.popleft()
            visited += 1
            for nxt in successors[curr]:
                in_degree[nxt] -= 1
                if in_degree[nxt] == 0:
                    queue.append(nxt)
        if visited != len(task_order):
            cyclic = [tid for tid, deg in in_degree.items() if deg > 0]
            raise CyclicDependencyError(f"Cycle detected in task dependencies: {cyclic}")

    @staticmethod
    def render_markdown_plan(
        plan: WavePlan,
        tasks: Sequence[TaskNode] | None = None,
    ) -> str:
        """Render human-readable Markdown execution plan with wave table and gates."""
        task_map = {t.id: t for t in (tasks or ())}
        lines: list[str] = [
            f"# Parallel Execution Plan (`{plan.sdd_kit}`)",
            "",
            f"- **Total Scheduled Tasks:** {len(plan.scheduled_task_ids)}",
            f"- **Total Waves:** {len(plan.waves)}",
            f"- **Detected Spine Locks:** `{', '.join(plan.spine_files) or 'none'}`",
            f"- **Additive Registries:** `{', '.join(plan.additive_registries) or 'none'}`",
            "",
        ]
        if plan.wave_zero_blockers:
            lines.append("## ⚠️ Wave 0 Blockers (Must Resolve Before Wave 1)")
            for blocker in plan.wave_zero_blockers:
                lines.append(f"- [ ] {blocker}")
            lines.append("")

        lines.extend(
            [
                "## Wave Schedule",
                "",
                "| Wave | Track | Story ID | Title | Effort | Exclusive Write Files | "
                "Shared Additive Files |",
                "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
            ]
        )
        for wave in plan.waves:
            for track in wave.tracks:
                for tid in track.task_ids:
                    t_node = task_map.get(tid)
                    title = t_node.title if t_node else tid
                    excl = ", ".join(f"`{f}`" for f in track.exclusive_write_files) or "—"
                    add = ", ".join(f"`{f}`" for f in track.additive_shared_files) or "—"
                    lines.append(
                        f"| **Wave {wave.wave_number}** | `{track.track_id}` | `{tid}` | "
                        f"{title} | **{track.effort.value}** | {excl} | {add} |"
                    )
        lines.append("")
        lines.append("## Wave Breakdown by Track")
        lines.append("")
        for wave in plan.waves:
            lines.append(f"* **Wave {wave.wave_number}**")
            for track in wave.tracks:
                for tid in track.task_ids:
                    t_node = task_map.get(tid)
                    title = t_node.title if t_node else tid
                    excl = ", ".join(f"`{f}`" for f in track.exclusive_write_files) or "none"
                    shared = (
                        f" | shared: {', '.join(f'`{f}`' for f in track.additive_shared_files)}"
                        if track.additive_shared_files
                        else ""
                    )
                    lines.append(
                        f"  - `{track.track_id}` — **{tid}** ({title}) "
                        f"[effort: `{track.effort.value}`]"
                    )
                    lines.append(f"    - writes: {excl}{shared}")
        lines.append("")
        return "\n".join(lines)
