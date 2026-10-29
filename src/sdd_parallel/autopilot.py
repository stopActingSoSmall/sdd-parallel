"""Multi-Conversation Autopilot launcher for Google Antigravity, Claude Code, and OpenAI Codex."""

from __future__ import annotations

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


def default_agentapi_path() -> Path:
    """Return standard path to Google Antigravity CLI bridge on Windows/POSIX."""
    win_candidate = Path.home() / ".gemini" / "antigravity" / "bin" / "agentapi.bat"
    if win_candidate.is_file():
        return win_candidate
    return Path.home() / ".gemini" / "antigravity" / "bin" / "agentapi"


class AutopilotLauncher:
    """Generates and optionally executes multi-session Depth-0 launch commands per wave track."""

    def __init__(
        self,
        repo_root: Path,
        agentapi_path: Path | None = None,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.worktrees_dir = self.repo_root / ".worktrees"
        self.agentapi_path = agentapi_path or default_agentapi_path()

    @staticmethod
    def resolve_model_for_effort(harness: str, effort: EffortTier) -> str:
        """Map abstract EffortTier (low/medium/high) to harness-specific model ID."""
        h_key = harness.strip().lower()
        tier_map = _MODEL_MAP.get(h_key, _MODEL_MAP["antigravity"])
        return tier_map.get(effort, tier_map[EffortTier.MEDIUM])

    def _build_track_prompt(self, wave_number: int, track: WaveTrack, wt_path: Path) -> str:
        stories = ", ".join(track.task_ids)
        return (
            f"Working directory: {wt_path.as_posix()}. "
            f"Read .tmp/track-context.md and .parallel-lock.json first. "
            f"Execute Wave {wave_number} / {track.track_id} stories ({stories}) "
            "using /bmad-build-auto strictly within the allowed write scope."
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
            wt_path = self.worktrees_dir / f"wave-{wave_number}-{track.track_id}"
            model_id = self.resolve_model_for_effort(h_norm, track.effort)
            stories_str = ", ".join(track.task_ids)
            title = f"Wave {wave_number} ({track.track_id}): {stories_str}"
            prompt = self._build_track_prompt(wave_number, track, wt_path)

            if h_norm == "antigravity":
                argv = [
                    str(self.agentapi_path),
                    "new-conversation",
                    f"--model={model_id}",
                    f"--title={title}",
                    prompt,
                ]
            elif h_norm == "claude-code":
                argv = [
                    "claude",
                    "--worktree",
                    f"wave-{wave_number}-{track.track_id}",
                    "--model",
                    model_id,
                    "-p",
                    prompt,
                ]
            else:
                argv = [
                    "codex",
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
                    "stories": list(track.task_ids),
                    "effort": track.effort.value,
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
        return specs
