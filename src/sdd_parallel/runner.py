"""Official Depth-0 Wave Runner and TrackExecutionPolicy Protocol (ADR-0009).

Orchestrates multi-track parallel wave execution directly via official harness CLIs
(agy -p, claude -p, codex exec) without Parent-Worker LLM polling.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from sdd_parallel.autopilot import AutopilotLauncher, extract_session_id_from_stdout
from sdd_parallel.models import EffortTier, WavePlan, WaveTrack


@dataclass(frozen=True)
class IterationDirective:
    """Per-iteration parameter & prompt-mode escalation rule."""

    effort: EffortTier | None = None
    model: str | None = None
    execution_mode: str = "NORMAL"  # e.g. "NORMAL", "SUBTRACTIVE", "CONTRACT_HARDENING"


@dataclass(frozen=True)
class ReviewVerdict:
    """Structured output of a Read-Only Reviewer pass in cyclic policies."""

    iteration: int
    critical_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0
    local_blockers: int | None = None
    divergence_flag: str = "CONVERGING"  # CONVERGING | WHACK_A_MOLE | ADDITIVE_BLOAT
    summary: str = ""
    split_candidates: tuple[str, ...] = ()

    @property
    def effective_blockers(self) -> int:
        if self.local_blockers is not None:
            return self.critical_count + self.local_blockers
        return self.critical_count + self.high_count

    @property
    def is_qualified(self) -> bool:
        return self.effective_blockers == 0


InvokerFn = Callable[
    [str, str | None, EffortTier | None, Path],
    subprocess.CompletedProcess[str],
]
ReviewerFn = Callable[["TrackExecutionContext", int], ReviewVerdict]


@dataclass
class TrackExecutionContext:
    """Execution context and dependency bag for a single track run."""

    repo_root: Path
    worktree_path: Path
    wave_number: int
    track: WaveTrack
    harness: str = "antigravity"
    sdd_kit: str = "bmad"
    timeout_seconds: float = 1800.0
    invoker: InvokerFn | None = None
    reviewer_fn: ReviewerFn | None = None
    extra_env: Mapping[str, str] = field(default_factory=dict)


@dataclass
class TrackExecutionResult:
    """Outcome and execution facts of a track execution pass."""

    track_id: str
    status: str  # "SUCCEEDED", "FAILED", "TIMED_OUT", "EXHAUSTED_NEEDS_SPLIT", "ESCALATED_DEFERRED"
    session_id: str | None = None
    exit_code: int = 0
    iterations_used: int = 1
    stdout: str = ""
    stderr: str = ""
    error_message: str | None = None
    verdicts: list[ReviewVerdict] = field(default_factory=list)
    duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "track_id": self.track_id,
            "status": self.status,
            "session_id": self.session_id,
            "exit_code": self.exit_code,
            "iterations_used": self.iterations_used,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "error_message": self.error_message,
            "duration_seconds": self.duration_seconds,
            "verdicts_count": len(self.verdicts),
        }


class TrackExecutionPolicy(Protocol):
    """Protocol for track execution policies (single-shot, cyclic review, etc.)."""

    policy_name: str

    def execute(self, ctx: TrackExecutionContext) -> TrackExecutionResult: ...


def _create_default_invoker(
    harness: str,
    timeout_seconds: float,
    extra_env: Mapping[str, str] | None = None,
) -> Callable[[str, str | None, EffortTier | None, Path], subprocess.CompletedProcess[str]]:
    """Create default system subprocess invoker targeting official harness CLI."""
    h_norm = harness.strip().lower()

    def _invoker(
        prompt: str,
        model: str | None,
        effort: EffortTier | None,
        cwd: Path,
    ) -> subprocess.CompletedProcess[str]:
        env = dict(extra_env or {})
        env_full = {**dict(os.environ), **env} if env else None

        if h_norm == "antigravity":
            argv = [
                "agy",
                "-p",
                prompt,
                "--model",
                model or "flash",
                "--effort",
                (effort.value if effort else "medium"),
                "--mode",
                "auto",
                "--output-format",
                "json",
            ]
        elif h_norm == "claude-code":
            if effort is not None:
                env.setdefault("CLAUDE_CODE_SUBAGENT_EFFORT", effort.value)
            env.setdefault("CLAUDE_CODE_PLUGIN_DIR_WATCH", "0")
            env.setdefault("CLAUDE_CODE_DISABLE_MCP_TASK_BACKGROUND", "1")
            env_full = {**dict(os.environ), **env}
            argv = [
                "claude",
                "-p",
                prompt,
                "--model",
                model or "claude-sonnet-4-6",
                "--output-format",
                "json",
            ]
        elif h_norm == "codex":
            argv = [
                "codex",
                "exec",
                "--cd",
                cwd.as_posix(),
                "--trust",
                "--model",
                model or "codex-medium",
                prompt,
            ]
        else:
            raise ValueError(f"Unsupported harness: {harness}")

        return subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=env_full,
            check=False,
        )

    return _invoker


class SingleShotExecutionPolicy:
    """Default linear single-pass execution policy for built-in SDD kits."""

    policy_name: str = "single-shot"

    def execute(self, ctx: TrackExecutionContext) -> TrackExecutionResult:
        t0 = datetime.now(UTC)
        launcher = AutopilotLauncher(ctx.repo_root)
        prompt = launcher._build_track_prompt(
            ctx.wave_number, ctx.track, ctx.worktree_path, sdd_kit=ctx.sdd_kit
        )
        model = (
            ctx.track.model_override.strip()
            if ctx.track.model_override and ctx.track.model_override.strip()
            else launcher.resolve_model_for_effort(ctx.harness, ctx.track.effort)
        )
        invoker = ctx.invoker or _create_default_invoker(
            ctx.harness, ctx.timeout_seconds, extra_env=ctx.extra_env
        )

        status = "FAILED"
        session_id: str | None = None
        exit_code = 1
        stdout = ""
        stderr = ""
        error_message: str | None = None

        try:
            proc = invoker(prompt, model, ctx.track.effort, ctx.worktree_path)
            exit_code = proc.returncode
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            session_id = extract_session_id_from_stdout(stdout)
            status = "SUCCEEDED" if exit_code == 0 else "FAILED"
        except subprocess.TimeoutExpired as exc:
            status = "TIMED_OUT"
            exit_code = 124
            error_message = f"Track {ctx.track.track_id} timed out after {ctx.timeout_seconds}s"
            stdout = (
                exc.stdout.decode("utf-8", errors="replace")
                if isinstance(exc.stdout, bytes)
                else (exc.stdout or "")
            )
            stderr = (
                exc.stderr.decode("utf-8", errors="replace")
                if isinstance(exc.stderr, bytes)
                else (exc.stderr or "")
            )
            session_id = extract_session_id_from_stdout(stdout)
        except Exception as exc:
            status = "FAILED"
            exit_code = 1
            error_message = f"Track invocation error: {exc}"

        t1 = datetime.now(UTC)
        duration = round((t1 - t0).total_seconds(), 2)

        # Record .tmp/runner-session.json inside worktree slot
        if ctx.worktree_path.is_dir():
            tmp_dir = ctx.worktree_path / ".tmp"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            session_file = tmp_dir / "runner-session.json"
            session_payload: dict[str, Any] = {
                "session_id": session_id,
                "harness": ctx.harness,
                "started_at": t0.isoformat(),
                "finished_at": t1.isoformat(),
                "duration_seconds": duration,
                "exit_code": exit_code,
                "policy": self.policy_name,
                "iterations_used": 1,
            }
            session_file.write_text(
                json.dumps(session_payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

        return TrackExecutionResult(
            track_id=ctx.track.track_id,
            status=status,
            session_id=session_id,
            exit_code=exit_code,
            iterations_used=1,
            stdout=stdout,
            stderr=stderr,
            error_message=error_message,
            duration_seconds=duration,
        )


class BoundedCyclicReviewPolicy:
    """Iterative convergence state machine (PreFlightGate -> Executor <-> Reviewer)."""

    policy_name: str = "cyclic-review"

    def __init__(
        self,
        max_iterations: int = 3,
        max_preflight_bounces: int = 2,
        preflight_commands: tuple[Sequence[str], ...] = (),
        iteration_schedule: Mapping[int, IterationDirective] | None = None,
        on_exhaustion: Literal["propose_split", "defer_wave", "halt"] = "propose_split",
    ) -> None:
        self.max_iterations = max_iterations
        self.max_preflight_bounces = max_preflight_bounces
        self.preflight_commands = tuple(preflight_commands)
        self.iteration_schedule = dict(iteration_schedule or {})
        self.on_exhaustion = on_exhaustion

    def execute(self, ctx: TrackExecutionContext) -> TrackExecutionResult:
        t0 = datetime.now(UTC)
        launcher = AutopilotLauncher(ctx.repo_root)
        invoker = ctx.invoker or _create_default_invoker(
            ctx.harness, ctx.timeout_seconds, extra_env=ctx.extra_env
        )

        session_id: str | None = None
        verdicts: list[ReviewVerdict] = []
        last_verdict: ReviewVerdict | None = None
        preflight_bounces = 0
        latest_stdout = ""
        latest_stderr = ""
        iterations_used = 0
        terminal_status = "FAILED"
        exit_code = 0
        error_msg: str | None = None

        for k in range(1, self.max_iterations + 1):
            iterations_used = k
            sched = self.iteration_schedule.get(k)
            eff = sched.effort if (sched and sched.effort) else ctx.track.effort
            mod = (
                sched.model
                if (sched and sched.model)
                else (
                    ctx.track.model_override.strip()
                    if (ctx.track.model_override and ctx.track.model_override.strip())
                    else launcher.resolve_model_for_effort(ctx.harness, eff)
                )
            )
            mode = (
                sched.execution_mode
                if (sched and sched.execution_mode != "NORMAL")
                else ctx.track.execution_mode
            )

            # Divergence oscillation detection:
            if last_verdict and last_verdict.divergence_flag in {
                "WHACK_A_MOLE",
                "ADDITIVE_BLOAT",
            }:
                mode = "SUBTRACTIVE"

            track_copy = WaveTrack(
                track_id=ctx.track.track_id,
                task_ids=ctx.track.task_ids,
                effort=eff,
                execution_mode=mode,
                model_override=mod,
            )
            base_prompt = launcher._build_track_prompt(
                ctx.wave_number, track_copy, ctx.worktree_path, sdd_kit=ctx.sdd_kit
            )

            if k >= 2 and last_verdict:
                feedback_prefix = (
                    f"[PREVIOUS REVIEW FEEDBACK (Iteration {k-1})]: {last_verdict.summary} "
                    f"(Blockers: {last_verdict.effective_blockers}, "
                    f"Critical: {last_verdict.critical_count}, High: {last_verdict.high_count}). "
                )
                prompt = feedback_prefix + base_prompt
            else:
                prompt = base_prompt

            # Inner loop for Zero-Budget Pre-Flight NACK
            while True:
                try:
                    proc = invoker(prompt, mod, eff, ctx.worktree_path)
                    latest_stdout = proc.stdout or ""
                    latest_stderr = proc.stderr or ""
                    exit_code = proc.returncode
                    sid = extract_session_id_from_stdout(latest_stdout)
                    if sid:
                        session_id = sid
                except subprocess.TimeoutExpired:
                    terminal_status = "TIMED_OUT"
                    exit_code = 124
                    error_msg = (
                        f"Track timed out after {ctx.timeout_seconds}s on iteration {k}"
                    )
                    break
                except Exception as exc:
                    terminal_status = "FAILED"
                    exit_code = 1
                    error_msg = f"Invocation error on iteration {k}: {exc}"
                    break

                # Preflight Gate Check
                nack_detected = False
                if self.preflight_commands and exit_code == 0:
                    for pf_cmd in self.preflight_commands:
                        pf_res = subprocess.run(
                            list(pf_cmd),
                            cwd=ctx.worktree_path,
                            capture_output=True,
                            text=True,
                            check=False,
                        )
                        if pf_res.returncode != 0:
                            if preflight_bounces < self.max_preflight_bounces:
                                preflight_bounces += 1
                                nack_detected = True
                                cmd_str = " ".join(pf_cmd)
                                ret_code = pf_res.returncode
                                nack_msg = (
                                    f"[PREFLIGHT GATE NACK]: Command '{cmd_str}' "
                                    f"failed with returncode {ret_code}:\n"
                                    f"{pf_res.stderr or pf_res.stdout}\n"
                                    f"Fix preflight gate failure before requesting review.\n\n"
                                )
                                prompt = nack_msg + prompt
                                break
                            else:
                                break

                if nack_detected:
                    continue  # Zero-budget bounce: retry without incrementing k!
                break

            if terminal_status == "TIMED_OUT" or error_msg:
                break

            # Reviewer Step
            if ctx.reviewer_fn is not None:
                verdict = ctx.reviewer_fn(ctx, k)
            else:
                verdict = ReviewVerdict(
                    iteration=k,
                    summary="Default qualified verdict (no external reviewer configured).",
                    critical_count=0,
                    high_count=0,
                )
            verdicts.append(verdict)
            last_verdict = verdict

            # Convergence Check (Freeze Invariant)
            if verdict.is_qualified:
                terminal_status = "SUCCEEDED"
                exit_code = 0
                break

            # Exhaustion Gate
            if k == self.max_iterations:
                triage_path = ctx.worktree_path / ".tmp" / "exhaustion-triage.json"
                triage_path.parent.mkdir(parents=True, exist_ok=True)
                triage_data = {
                    "task_ids": list(ctx.track.task_ids),
                    "iterations_used": k,
                    "remaining_blockers": verdict.effective_blockers,
                    "divergence_flag": verdict.divergence_flag,
                    "recommended_action": self.on_exhaustion,
                    "split_candidates": list(verdict.split_candidates),
                }
                triage_path.write_text(
                    json.dumps(triage_data, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
                if self.on_exhaustion == "propose_split":
                    terminal_status = "EXHAUSTED_NEEDS_SPLIT"
                elif self.on_exhaustion == "defer_wave":
                    terminal_status = "ESCALATED_DEFERRED"
                else:
                    terminal_status = "FAILED"
                exit_code = 1
                break

        t1 = datetime.now(UTC)
        duration = round((t1 - t0).total_seconds(), 2)

        if ctx.worktree_path.is_dir():
            tmp_dir = ctx.worktree_path / ".tmp"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            session_file = tmp_dir / "runner-session.json"
            session_payload: dict[str, Any] = {
                "session_id": session_id,
                "harness": ctx.harness,
                "started_at": t0.isoformat(),
                "finished_at": t1.isoformat(),
                "duration_seconds": duration,
                "exit_code": exit_code,
                "policy": self.policy_name,
                "iterations_used": iterations_used,
            }
            session_file.write_text(
                json.dumps(session_payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

        return TrackExecutionResult(
            track_id=ctx.track.track_id,
            status=terminal_status,
            session_id=session_id,
            exit_code=exit_code,
            iterations_used=iterations_used,
            stdout=latest_stdout,
            stderr=latest_stderr,
            error_message=error_msg,
            verdicts=verdicts,
            duration_seconds=duration,
        )


@dataclass
class WaveRunSummary:
    """Summary of complete wave parallel execution."""

    wave_id: int
    total_tracks: int
    succeeded_tracks: int
    failed_tracks: int
    track_results: dict[str, TrackExecutionResult]
    all_succeeded: bool
    auto_merged: bool = False
    manifest_path: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "wave_id": self.wave_id,
            "total_tracks": self.total_tracks,
            "succeeded_tracks": self.succeeded_tracks,
            "failed_tracks": self.failed_tracks,
            "track_results": {k: v.to_dict() for k, v in self.track_results.items()},
            "all_succeeded": self.all_succeeded,
            "auto_merged": self.auto_merged,
            "manifest_path": self.manifest_path,
            "error_message": self.error_message,
        }


class WaveRunner:
    """Parallel Depth-0 runner orchestrating execution across wave track slots."""

    def __init__(
        self,
        repo_root: Path,
        harness: str = "antigravity",
        fixed_slots: bool = True,
        worktrees_dir: Path | None = None,
        timeout_seconds: float = 1800.0,
        policy: TrackExecutionPolicy | None = None,
        invoker: InvokerFn | None = None,
        reviewer_fn: ReviewerFn | None = None,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.harness = harness.strip().lower()
        self.fixed_slots = fixed_slots
        self.worktrees_dir = worktrees_dir or (self.repo_root / ".worktrees")
        self.timeout_seconds = timeout_seconds
        self.policy = policy or SingleShotExecutionPolicy()
        self.invoker = invoker
        self.reviewer_fn = reviewer_fn

    def run_wave(
        self,
        plan: WavePlan,
        wave_number: int,
        auto_merge: bool = False,
    ) -> WaveRunSummary:
        """Execute tracks in `wave_number` concurrently with barrier and manifest harvest."""
        target_wave = next((w for w in plan.waves if w.wave_number == wave_number), None)
        if target_wave is None:
            raise ValueError(f"Wave {wave_number} not found in plan")

        # 1. Pre-Flight Harness Binary Verification (for real execution without mock invoker)
        if self.invoker is None:
            bin_map = {
                "antigravity": "agy",
                "claude-code": "claude",
                "codex": "codex",
            }
            expected_bin = bin_map.get(self.harness, self.harness)
            if not shutil.which(expected_bin):
                err = (
                    f"Pre-Flight Check Failed: Harness CLI binary '{expected_bin}' "
                    f"not found in system PATH. Cannot execute Depth-0 wave tracks."
                )
                return WaveRunSummary(
                    wave_id=wave_number,
                    total_tracks=len(target_wave.tracks),
                    succeeded_tracks=0,
                    failed_tracks=len(target_wave.tracks),
                    track_results={},
                    all_succeeded=False,
                    auto_merged=False,
                    manifest_path=None,
                    error_message=err,
                )

        # 2. Build TrackExecutionContexts
        contexts: list[TrackExecutionContext] = []
        for track in target_wave.tracks:
            if self.fixed_slots:
                wt_path = self.worktrees_dir / track.track_id
            else:
                wt_path = self.worktrees_dir / f"wave-{wave_number}-{track.track_id}"
            extra_env: dict[str, str] = {}
            if self.harness == "claude-code":
                extra_env["CLAUDE_CODE_SUBAGENT_EFFORT"] = track.effort.value
                extra_env["CLAUDE_CODE_PLUGIN_DIR_WATCH"] = "0"
                extra_env["CLAUDE_CODE_DISABLE_MCP_TASK_BACKGROUND"] = "1"
            ctx = TrackExecutionContext(
                repo_root=self.repo_root,
                worktree_path=wt_path,
                wave_number=wave_number,
                track=track,
                harness=self.harness,
                sdd_kit=plan.sdd_kit,
                timeout_seconds=self.timeout_seconds,
                invoker=self.invoker,
                reviewer_fn=self.reviewer_fn,
                extra_env=extra_env,
            )
            contexts.append(ctx)

        # 3. Concurrent Execution with ThreadPoolExecutor (Global Sync Barrier)
        track_results: dict[str, TrackExecutionResult] = {}
        max_workers = max(1, len(contexts))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_ctx = {
                executor.submit(self.policy.execute, ctx): ctx for ctx in contexts
            }
            for future in concurrent.futures.as_completed(future_to_ctx):
                ctx = future_to_ctx[future]
                try:
                    res = future.result()
                except Exception as exc:
                    res = TrackExecutionResult(
                        track_id=ctx.track.track_id,
                        status="FAILED",
                        exit_code=1,
                        error_message=f"Unhandled runner exception: {exc}",
                    )
                track_results[ctx.track.track_id] = res

        succeeded = sum(1 for r in track_results.values() if r.status == "SUCCEEDED")
        failed = len(track_results) - succeeded
        all_succeeded = (failed == 0 and len(track_results) > 0)

        # 4. Manifest Persistence (Task 30 correlation)
        from sdd_parallel.manifest import harvest_wave_manifest, save_wave_manifest

        manifest = harvest_wave_manifest(
            repo_root=self.repo_root,
            plan=plan,
            wave_id=wave_number,
            sdd_kit=plan.sdd_kit,
            fixed_slots=self.fixed_slots,
            worktrees_dir=self.worktrees_dir,
        )
        manifest_path = str(save_wave_manifest(self.repo_root, manifest))

        # 5. Auto-Merge Barrier (if requested)
        auto_merged = False
        error_msg: str | None = None
        if auto_merge:
            if all_succeeded:
                from sdd_parallel.worktree import WorktreeManager

                wm = WorktreeManager(self.repo_root, fixed_slots=self.fixed_slots)
                wm.worktrees_dir = self.worktrees_dir
                violations = wm.verify_wave(plan, wave_number)
                if violations:
                    error_msg = f"Auto-merge blocked: Scope Guard violations detected: {violations}"
                else:
                    try:
                        wm.merge_wave(
                            plan,
                            wave_number,
                            run_quality_gates=True,
                            cleanup_worktrees=False,
                        )
                        auto_merged = True
                    except Exception as merge_exc:
                        error_msg = f"Auto-merge failed during git merge/gates: {merge_exc}"
            else:
                error_msg = f"Auto-merge blocked: {failed} tracks failed or incomplete."

        return WaveRunSummary(
            wave_id=wave_number,
            total_tracks=len(target_wave.tracks),
            succeeded_tracks=succeeded,
            failed_tracks=failed,
            track_results=track_results,
            all_succeeded=all_succeeded,
            auto_merged=auto_merged,
            manifest_path=manifest_path,
            error_message=error_msg,
        )
