"""Deterministic Bulk-Synchronous Parallel (BSP) wave scheduler and DAG solver."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from sdd_parallel.models import (
    DEFAULT_CONTRACT_GLOBS,
    DEFAULT_SPINE_BUCKET_GLOBS,
    ConflictEdge,
    ConflictType,
    FileRole,
    TaskNode,
    TaskStructuralSignals,
    Wave,
    WavePlan,
    WaveTrack,
    extract_spine_bucket,
    is_contract_file,
    normalize_rel_path,
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
        ast_scanner: Any | None = None,
        contract_globs: Sequence[str] | None = None,
        repo_root: Path | None = None,
        spine_bucket_globs: Sequence[str] | None = None,
    ) -> None:
        if max_tracks < 1:
            raise ValueError("max_tracks must be >= 1")
        self.max_tracks = max_tracks
        self.file_roles: dict[str, FileRole] = {
            normalize_rel_path(k): v
            for k, v in (file_roles or {}).items()
            if normalize_rel_path(k)
        }
        self.ast_scanner = ast_scanner
        self.contract_globs = (
            tuple(contract_globs) if contract_globs is not None else DEFAULT_CONTRACT_GLOBS
        )
        self.spine_bucket_globs = (
            tuple(spine_bucket_globs)
            if spine_bucket_globs is not None
            else DEFAULT_SPINE_BUCKET_GLOBS
        )
        self.repo_root = (
            repo_root
            if repo_root is not None
            else (getattr(ast_scanner, "repo_root", None) if ast_scanner is not None else None)
        )

    def get_file_role(self, path: str) -> FileRole:
        normalized = normalize_rel_path(path)
        parts = normalized.split("/") if normalized else []
        filename = parts[-1] if parts else ""
        if "tests" in parts or filename.startswith("test_") or filename.endswith("_test.py"):
            return FileRole.STANDARD
        if normalized in self.file_roles:
            return self.file_roles[normalized]
        if is_contract_file(normalized, self.contract_globs):
            return FileRole.SPINE
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

    def build_edges(
        self,
        tasks: Sequence[TaskNode],
        completed_wave_of: Mapping[str, int] | None = None,
    ) -> list[ConflictEdge]:
        """Construct directed dependencies and undirected collision edges across tasks."""
        if self.ast_scanner is not None:
            linked_map = self.ast_scanner.link_tasks(tasks)
            if isinstance(linked_map, dict) and linked_map:
                for task in tasks:
                    if linked_map.get(task.id):
                        importers: set[str] = {
                            norm for f in task.impact_files if (norm := normalize_rel_path(f))
                        }
                        write_set = {
                            norm for wf in task.write_files if (norm := normalize_rel_path(wf))
                        }
                        for wf in write_set:
                            importers.update(
                                norm
                                for imp in self.ast_scanner.get_importers(wf)
                                if (norm := normalize_rel_path(imp))
                            )
                        importers.difference_update(write_set)
                        task.impact_files = sorted(importers)

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
            write_a = {norm for f in task_a.write_files if (norm := normalize_rel_path(f))}
            impact_a = {norm for f in task_a.impact_files if (norm := normalize_rel_path(f))}
            for task_b in tasks[i + 1 :]:
                write_b = {norm for f in task_b.write_files if (norm := normalize_rel_path(f))}
                impact_b = {norm for f in task_b.impact_files if (norm := normalize_rel_path(f))}

                shared_writes = sorted(write_a & write_b)
                spine_shared = [f for f in shared_writes if self.get_file_role(f) == FileRole.SPINE]
                buckets_a = {
                    b
                    for f in write_a
                    if (b := extract_spine_bucket(f, self.spine_bucket_globs)) is not None
                }
                buckets_b = {
                    b
                    for f in write_b
                    if (b := extract_spine_bucket(f, self.spine_bucket_globs)) is not None
                }
                shared_buckets = sorted(buckets_a & buckets_b)
                standard_shared = [
                    f
                    for f in shared_writes
                    if self.get_file_role(f) == FileRole.STANDARD
                    and not self._has_isolated_function_scope(task_a, task_b, f)
                ]

                if spine_shared or shared_buckets:
                    # Respect existing explicit dependency direction and completed-wave order
                    if self._can_reach(task_b.id, task_a.id, explicit_adj):
                        src_id, dst_id = task_b.id, task_a.id
                    elif self._can_reach(task_a.id, task_b.id, explicit_adj):
                        src_id, dst_id = task_a.id, task_b.id
                    elif completed_wave_of is not None:
                        w_a = completed_wave_of.get(task_a.id)
                        w_b = completed_wave_of.get(task_b.id)
                        if w_a is not None and w_b is None:
                            src_id, dst_id = task_a.id, task_b.id
                        elif w_b is not None and w_a is None:
                            src_id, dst_id = task_b.id, task_a.id
                        elif w_a is not None and w_b is not None and w_a != w_b:
                            src_id, dst_id = (
                                (task_a.id, task_b.id) if w_a < w_b else (task_b.id, task_a.id)
                            )
                        else:
                            src_id, dst_id = task_a.id, task_b.id
                    else:
                        src_id, dst_id = task_a.id, task_b.id
                    if not self._can_reach(dst_id, src_id, explicit_adj):
                        explicit_adj[src_id].add(dst_id)
                        combined_shared = sorted(set(spine_shared) | set(shared_buckets))
                        reason_parts: list[str] = []
                        if spine_shared:
                            reason_parts.append(
                                f"Shared Spine Lock file(s): {', '.join(spine_shared)}"
                            )
                        if shared_buckets:
                            reason_parts.append(
                                f"Shared Spine bucket(s): {', '.join(shared_buckets)}"
                            )
                        edges.append(
                            ConflictEdge(
                                source=src_id,
                                target=dst_id,
                                conflict_type=ConflictType.SPINE_ORDER,
                                reason="; ".join(reason_parts),
                                shared_files=tuple(combined_shared),
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

                    ns_shared = sorted(
                        set(task_a.namespace_tokens) & set(task_b.namespace_tokens)
                    )
                    if ns_shared:
                        edges.append(
                            ConflictEdge(
                                source=task_a.id,
                                target=task_b.id,
                                conflict_type=ConflictType.NAMESPACE_COLLISION,
                                reason=f"Shared namespace token(s): {', '.join(ns_shared)}",
                                shared_files=tuple(ns_shared),
                                directed=False,
                            )
                        )

        return edges

    @staticmethod
    def _lookup_function_scope(notes: Mapping[str, str], norm_path: str) -> str:
        if norm_path in notes:
            return notes[norm_path].strip()
        for raw_key, value in notes.items():
            if normalize_rel_path(raw_key) == norm_path:
                return value.strip()
        return ""

    def _has_isolated_function_scope(
        self,
        task_a: TaskNode,
        task_b: TaskNode,
        file_path: str,
    ) -> bool:
        """Return True if tasks touch distinct, AST-verified non-interacting functions."""
        norm_path = normalize_rel_path(file_path)
        scope_a = self._lookup_function_scope(task_a.shared_function_notes, norm_path)
        scope_b = self._lookup_function_scope(task_b.shared_function_notes, norm_path)
        if not scope_a or not scope_b or scope_a == scope_b:
            return False
        if self.ast_scanner is not None:
            return bool(self.ast_scanner.are_functions_isolated(norm_path, scope_a, scope_b))
        return True

    def compute_structural_signals(
        self,
        task: TaskNode,
        edges: Sequence[ConflictEdge],
    ) -> TaskStructuralSignals:
        """Compute deterministic AST & conflict-graph structural metrics for task."""
        norm_writes = [norm for f in task.write_files if (norm := normalize_rel_path(f))]
        files_count = len(task.write_files)
        max_fan_in = 0
        if self.ast_scanner is not None and norm_writes:
            max_fan_in = max(
                (len(self.ast_scanner.get_importers(f)) for f in norm_writes),
                default=0,
            )
        touches_spine = any(
            self.get_file_role(f) == FileRole.SPINE for f in norm_writes
        )

        base_dir: Path | None = self.repo_root
        if base_dir is None and self.ast_scanner is not None:
            base_dir = getattr(self.ast_scanner, "repo_root", None)

        ast_loc = 0
        for f in task.write_files:
            f_path = Path(f)
            if base_dir is not None and not f_path.is_absolute():
                p = (base_dir / normalize_rel_path(f)).resolve()
            else:
                p = f_path.resolve()
            if p.is_file():
                try:
                    content = p.read_text(encoding="utf-8-sig", errors="replace")
                    ast_loc += len(content.splitlines())
                except Exception:
                    pass

        conflict_degree = sum(
            1 for e in edges if e.source == task.id or e.target == task.id
        )

        return TaskStructuralSignals(
            files_count=files_count,
            max_fan_in=max_fan_in,
            touches_spine=touches_spine,
            ast_loc=ast_loc,
            conflict_degree=conflict_degree,
        )

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
                tasks=[],
            )

        edges = self.build_edges(tasks)
        for t in tasks:
            t.structural_signals = self.compute_structural_signals(t, edges)

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
                    norm_f = normalize_rel_path(f)
                    if not norm_f:
                        continue
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
                        function_scope_notes={
                            (normalize_rel_path(k) or k): v
                            for k, v in t_node.shared_function_notes.items()
                        },
                    )
                )
                unscheduled.remove(tid)
                completed.add(tid)

            waves.append(Wave(wave_number=wave_num, tracks=tracks))
            wave_num += 1

        spine_files, additive_registries = self._collect_role_files(tasks)

        plan = WavePlan(
            sdd_kit=sdd_kit,
            waves=waves,
            edges=edges,
            wave_zero_blockers=list(wave_zero_blockers),
            spine_files=spine_files,
            additive_registries=additive_registries,
            tasks=list(tasks),
        )

        # Enforce 100% Task Coverage Invariant
        if set(plan.scheduled_task_ids) != set(task_order):
            missing = set(task_order) - set(plan.scheduled_task_ids)
            raise CoverageInvariantError(f"Tasks lost during scheduling: {sorted(missing)}")

        return plan

    def _collect_role_files(self, tasks: Sequence[TaskNode]) -> tuple[list[str], list[str]]:
        """Collect sorted SPINE and ADDITIVE_REGISTRY write files across tasks."""
        spine_files = sorted(
            {
                norm
                for t in tasks
                for f in t.write_files
                if (norm := normalize_rel_path(f)) and self.get_file_role(norm) == FileRole.SPINE
            }
        )
        additive_registries = sorted(
            {
                norm
                for t in tasks
                for f in t.write_files
                if (norm := normalize_rel_path(f))
                and self.get_file_role(norm) == FileRole.ADDITIVE_REGISTRY
            }
        )
        return spine_files, additive_registries

    def reschedule_remaining_waves(
        self,
        plan: WavePlan,
        current_wave: int,
        deferred_task_ids: Sequence[str],
        enriched_writes: Mapping[str, Sequence[str]] | None = None,
    ) -> WavePlan:
        """Re-schedule unfinished waves (> current_wave) plus deferred tasks via full DAG."""
        deferred_set = set(deferred_task_ids)
        prefix_waves: list[Wave] = []
        current_wave_gates: list[str] = []
        future_task_ids: set[str] = set()

        for w in plan.waves:
            if w.wave_number < current_wave:
                prefix_waves.append(w)
            elif w.wave_number == current_wave:
                current_wave_gates = list(w.checkpoint_gates)
                updated_tracks: list[WaveTrack] = []
                for track in w.tracks:
                    kept_ids = [tid for tid in track.task_ids if tid not in deferred_set]
                    updated_tracks.append(
                        WaveTrack(
                            track_id=track.track_id,
                            task_ids=kept_ids,
                            effort=track.effort,
                            exclusive_write_files=(
                                list(track.exclusive_write_files) if kept_ids else []
                            ),
                            additive_shared_files=(
                                list(track.additive_shared_files) if kept_ids else []
                            ),
                            function_scope_notes=dict(track.function_scope_notes),
                            model_override=track.model_override,
                            execution_mode=track.execution_mode,
                        )
                    )
                prefix_waves.append(
                    Wave(
                        wave_number=w.wave_number,
                        tracks=updated_tracks,
                        checkpoint_gates=list(w.checkpoint_gates),
                    )
                )
            else:
                future_task_ids.update(w.all_task_ids)

        remaining_ids = future_task_ids | deferred_set
        remaining_tasks = [t for t in plan.tasks if t.id in remaining_ids]

        if enriched_writes:
            for t in remaining_tasks:
                existing_writes = {
                    n for wf in t.write_files if (n := normalize_rel_path(wf))
                }
                for raw_path in enriched_writes.get(t.id, ()):
                    norm = normalize_rel_path(raw_path)
                    if norm and norm not in existing_writes:
                        t.write_files.append(norm)
                        existing_writes.add(norm)

        all_tasks = list(plan.tasks)
        spine_files, additive_registries = self._collect_role_files(all_tasks)
        completed_wave_of: dict[str, int] = {
            tid: w.wave_number
            for w in prefix_waves
            for tid in w.all_task_ids
        }

        if not remaining_tasks:
            edges = (
                self.build_edges(all_tasks, completed_wave_of=completed_wave_of)
                if all_tasks
                else []
            )
            return WavePlan(
                sdd_kit=plan.sdd_kit,
                waves=prefix_waves,
                edges=edges,
                wave_zero_blockers=list(plan.wave_zero_blockers),
                spine_files=spine_files,
                additive_registries=additive_registries,
                tasks=all_tasks,
            )

        sub_plan = self.schedule(
            remaining_tasks,
            sdd_kit=plan.sdd_kit,
            wave_zero_blockers=plan.wave_zero_blockers,
        )
        shifted_waves = [
            Wave(
                wave_number=w.wave_number + current_wave,
                tracks=w.tracks,
                checkpoint_gates=(
                    list(w.checkpoint_gates) if w.checkpoint_gates else list(current_wave_gates)
                ),
            )
            for w in sub_plan.waves
        ]

        all_waves = prefix_waves + shifted_waves
        edges = self.build_edges(all_tasks, completed_wave_of=completed_wave_of)
        new_plan = WavePlan(
            sdd_kit=plan.sdd_kit,
            waves=all_waves,
            edges=edges,
            wave_zero_blockers=list(plan.wave_zero_blockers),
            spine_files=spine_files,
            additive_registries=additive_registries,
            tasks=all_tasks,
        )

        task_order = [t.id for t in all_tasks]
        if all_tasks and set(new_plan.scheduled_task_ids) != set(task_order):
            missing = set(task_order) - set(new_plan.scheduled_task_ids)
            raise CoverageInvariantError(f"Tasks lost during rescheduling: {sorted(missing)}")

        return new_plan

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
