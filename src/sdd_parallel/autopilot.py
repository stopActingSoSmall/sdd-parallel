"""Multi-Conversation Autopilot launcher for Google Antigravity, Claude Code, and OpenAI Codex."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from sdd_parallel.models import EffortTier, WavePlan, WaveTrack

_MODEL_MAP: dict[str, dict[EffortTier, str]] = {
    "antigravity": {
        EffortTier.HIGH: "pro",
        EffortTier.MEDIUM: "flash",
        EffortTier.LOW: "flash_lite",
    },
    "claude-code": {
        EffortTier.HIGH: "claude-opus-4-6",
        EffortTier.MEDIUM: "claude-sonnet-4-6",
        EffortTier.LOW: "claude-haiku-4-5",
    },
    "codex": {
        EffortTier.HIGH: "o3-high",
        EffortTier.MEDIUM: "codex-medium",
        EffortTier.LOW: "codex-mini",
    },
}


def extract_session_id_from_stdout(stdout: str) -> str | None:
    """Extract conversation/session ID from stdout (JSON object or JSONL lines)."""
    if not stdout or not stdout.strip():
        return None
    text = stdout.strip()
    candidates = [text]
    candidates.extend(reversed([line.strip() for line in text.splitlines() if line.strip()]))
    for cand in candidates:
        if not (cand.startswith("{") and cand.endswith("}")):
            continue
        try:
            data = json.loads(cand)
            if isinstance(data, dict):
                for key in ("session_id", "sessionId", "conversation_id", "conversationId"):
                    val = data.get(key)
                    if val:
                        return str(val)
        except Exception:
            continue
    return None


class AutopilotLauncher:
    """Generates and optionally executes multi-session Depth-0 launch commands per wave track."""

    def __init__(
        self,
        repo_root: Path,
        agentapi_path: Path | None = None,
        fixed_slots: bool = True,
        agy_cmd: str | None = None,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.worktrees_dir = self.repo_root / ".worktrees"
        self.fixed_slots = fixed_slots
        # Prefer explicit agy_cmd, fallback to shutil.which("agy") or "agy"
        if agy_cmd:
            self.agy_cmd = agy_cmd
        elif agentapi_path and agentapi_path.name != "agentapi.bat":
            self.agy_cmd = str(agentapi_path)
        else:
            self.agy_cmd = shutil.which("agy") or "agy"

    @staticmethod
    def resolve_model_for_effort(harness: str, effort: EffortTier) -> str:
        """Map abstract EffortTier (low/medium/high) to harness-specific model ID."""
        h_key = harness.strip().lower()
        tier_map = _MODEL_MAP.get(h_key, _MODEL_MAP["antigravity"])
        return tier_map.get(effort, tier_map[EffortTier.MEDIUM])

    def _build_track_prompt(
        self,
        wave_number: int,
        track: WaveTrack,
        wt_path: Path,
        sdd_kit: str = "bmad",
    ) -> str:
        from sdd_parallel.adapters import get_adapter

        try:
            _, adapter = get_adapter(self.repo_root, kit=sdd_kit)
            worker_cmd = adapter.render_worker_command(track).strip("`")
        except ValueError:
            worker_cmd = f"/bmad-build {' '.join(track.task_ids)}"

        stories = ", ".join(track.task_ids)
        mode_prefix = (
            f"[EXECUTION_MODE: {track.execution_mode}] "
            if track.execution_mode and track.execution_mode != "NORMAL"
            else ""
        )
        return (
            f"{mode_prefix}Working directory: {wt_path.as_posix()}. "
            f"Read .tmp/track-context.md and .parallel-lock.json first. "
            f"Execute Wave {wave_number} / {track.track_id} stories ({stories}) "
            f"using `{worker_cmd}` strictly within the allowed write scope."
        )

    def build_launch_specs(
        self,
        plan: WavePlan,
        wave_number: int,
        harness: str = "antigravity",
    ) -> list[dict[str, Any]]:
        """Build structured launch specifications for all parallel tracks in `wave_number`."""
        target_wave = next((w for w in plan.waves if w.wave_number == wave_number), None)
        if target_wave is None:
            raise ValueError(f"Wave {wave_number} not found in plan")

        h_norm = harness.strip().lower()
        if h_norm not in _MODEL_MAP:
            raise ValueError(f"Unsupported harness '{harness}'. Choose from: {sorted(_MODEL_MAP)}")

        specs: list[dict[str, Any]] = []
        for track in target_wave.tracks:
            if self.fixed_slots:
                wt_path = self.worktrees_dir / track.track_id
                slot_name = track.track_id
            else:
                wt_path = self.worktrees_dir / f"wave-{wave_number}-{track.track_id}"
                slot_name = f"wave-{wave_number}-{track.track_id}"
            if track.model_override and track.model_override.strip():
                model_id = track.model_override.strip()
            else:
                model_id = self.resolve_model_for_effort(h_norm, track.effort)
            stories_str = ", ".join(track.task_ids)
            title = f"Wave {wave_number} ({track.track_id}): {stories_str}"
            prompt = self._build_track_prompt(
                wave_number, track, wt_path, sdd_kit=plan.sdd_kit
            )

            if h_norm == "antigravity":
                argv = [
                    self.agy_cmd,
                    "-p",
                    prompt,
                    "--model",
                    model_id,
                    "--effort",
                    track.effort.value,
                    "--mode",
                    "auto",
                    "--output-format",
                    "json",
                ]
            elif h_norm == "claude-code":
                argv = [
                    "claude",
                    "-p",
                    prompt,
                    "--model",
                    model_id,
                    "--output-format",
                    "json",
                ]
            else:
                argv = [
                    "codex",
                    "exec",
                    "--cd",
                    wt_path.as_posix(),
                    "--model",
                    model_id,
                    prompt,
                ]

            specs.append(
                {
                    "wave": wave_number,
                    "track_id": track.track_id,
                    "slot_name": slot_name,
                    "stories": list(track.task_ids),
                    "effort": track.effort.value,
                    "execution_mode": track.execution_mode,
                    "harness": h_norm,
                    "model": model_id,
                    "worktree": wt_path.as_posix(),
                    "title": title,
                    "prompt": prompt,
                    "argv": argv,
                    "command_line": " ".join(f'"{a}"' if " " in a else a for a in argv),
                }
            )
        return specs

    def launch_wave(
        self,
        plan: WavePlan,
        wave_number: int,
        harness: str = "antigravity",
        execute: bool = False,
    ) -> list[dict[str, Any]]:
        """Build launch specs and optionally execute them via subprocess."""
        specs = self.build_launch_specs(
            plan=plan,
            wave_number=wave_number,
            harness=harness,
        )
        if not execute:
            return specs

        for item in specs:
            argv: list[str] = item["argv"]
            target_cwd = (
                Path(item["worktree"]).resolve()
                if Path(item["worktree"]).is_dir()
                else self.repo_root
            )
            proc = subprocess.run(
                argv,
                cwd=target_cwd,
                capture_output=True,
                text=True,
                check=False,
            )
            item["returncode"] = proc.returncode
            item["stdout"] = proc.stdout.strip()
            item["stderr"] = proc.stderr.strip()
            session_id = extract_session_id_from_stdout(proc.stdout)
            item["session_id"] = session_id

            if target_cwd.is_dir():
                tmp_dir = target_cwd / ".tmp"
                tmp_dir.mkdir(parents=True, exist_ok=True)
                session_file = tmp_dir / "runner-session.json"
                session_payload: dict[str, Any] = {
                    "session_id": session_id,
                    "harness": item.get("harness"),
                    "exit_code": proc.returncode,
                    "policy": "single-shot",
                    "iterations_used": 1,
                }
                session_file.write_text(
                    json.dumps(session_payload, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
        return specs
