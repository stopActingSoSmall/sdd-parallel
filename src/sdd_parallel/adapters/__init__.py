"""Unified SDD kit auto-detection and adapter registry (BMAD, Spec Kit, GSD, OpenSpec, Beads)."""

from __future__ import annotations

import json
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from sdd_parallel.adapters.beads import BeadsAdapter
from sdd_parallel.adapters.bmad import BMADAdapter
from sdd_parallel.adapters.gsd_hijack import GSDHijackAdapter
from sdd_parallel.adapters.openspec import OpenSpecAdapter
from sdd_parallel.adapters.spec_kit import SpecKitAdapter
from sdd_parallel.models import TaskNode, Wave, WaveTrack


class SDDAdapterProtocol(Protocol):
    """Common adapter contract across all SDD kits."""

    kit_id: str

    def __init__(self, repo_root: Path) -> None: ...
    def detect(self) -> bool: ...
    def load_tasks(
        self,
        include_done: bool = False,
        plan_source: str = "auto",
    ) -> list[TaskNode]: ...
    def reset_tasks_status(
        self,
        task_ids: Sequence[str],
        target_status: str = "ready-for-dev",
    ) -> None: ...
    def cleanup_wave_artifacts(
        self,
        wave_id: int,
        task_ids: Sequence[str],
    ) -> None: ...
    def check_wave_zero_blockers(self, wave: Wave | None = None) -> list[str]: ...
    def check_preflight_context_gate(self, wave: Wave) -> list[str]: ...
    def get_allowed_write_globs(self, track: WaveTrack) -> list[str]: ...
    def get_forbidden_files(self, wave: Wave, track: WaveTrack) -> dict[str, str]: ...
    def get_track_env_vars(self, track: WaveTrack) -> dict[str, str]: ...
    def prepare_worktree_overlay(self, worktree_path: Path, track: WaveTrack) -> list[str]: ...
    def verify_track_spec_scope(
        self,
        worktree_path: Path,
        track: WaveTrack,
        forbidden_files: dict[str, str],
    ) -> list[str]: ...
    def render_worker_command(self, track: WaveTrack) -> str: ...
    def default_artifacts_dir(self) -> Path: ...
    def get_status_files(self) -> list[str]: ...


def detect_sdd_kits(repo_root: Path) -> list[str]:
    """Return list of all SDD kits detected in `repo_root` in priority order."""
    root = repo_root.resolve()
    detected: list[str] = []
    for kit_name, adapter_cls in ADAPTER_REGISTRY.items():
        if adapter_cls(root).detect():
            detected.append(kit_name)
    return detected


def _get_configured_default_kit(repo_root: Path) -> str | None:
    """Read user-configured default_kit from .parallel-config.json or pyproject.toml."""
    cfg_file = repo_root / ".parallel-config.json"
    if cfg_file.is_file():
        try:
            data = json.loads(cfg_file.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                val = data.get("default_kit") or data.get("kit")
                if isinstance(val, str) and val.strip():
                    return val.strip().lower()
        except Exception:
            pass

    pyproject_file = repo_root / "pyproject.toml"
    if pyproject_file.is_file():
        try:
            with open(pyproject_file, "rb") as f:
                data = tomllib.load(f)
            tool_cfg = data.get("tool", {}).get("sdd-parallel", {})
            if isinstance(tool_cfg, dict):
                val = tool_cfg.get("default_kit") or tool_cfg.get("kit")
                if isinstance(val, str) and val.strip():
                    return val.strip().lower()
        except Exception:
            pass
    return None


ADAPTER_REGISTRY: dict[str, type[SDDAdapterProtocol]] = {
    "bmad": BMADAdapter,
    "spec-kit": SpecKitAdapter,
    "gsd": GSDHijackAdapter,
    "openspec": OpenSpecAdapter,
    "beads": BeadsAdapter,
}


def _instantiate_adapter(kit_name: str, root: Path) -> tuple[str, SDDAdapterProtocol]:
    norm = kit_name.strip().lower()
    if norm == "speckit":
        norm = "spec-kit"
    if norm in ADAPTER_REGISTRY:
        adapter_cls = ADAPTER_REGISTRY[norm]
        return norm, adapter_cls(root)
    raise ValueError(f"Unsupported SDD kit: '{kit_name}'")


def diagnose_multi_kit_coexistence(repo_root: Path, selected_kit: str) -> list[str]:
    """Return diagnostic warning if multiple SDD kits coexist in repo_root."""
    detected = detect_sdd_kits(repo_root)
    if len(detected) > 1:
        return [
            f"Multiple SDD frameworks detected in repository ({', '.join(detected)}). "
            f"Active adapter: '{selected_kit}'. Use '--kit <name>' or set 'default_kit' "
            "in .parallel-config.json to switch."
        ]
    return []


def get_adapter(repo_root: Path, kit: str = "auto") -> tuple[str, SDDAdapterProtocol]:
    """Instantiate the requested SDD kit adapter (or auto-detect the primary kit)."""
    root = repo_root.resolve()
    normalized = kit.strip().lower()

    if normalized == "auto":
        cfg_kit = _get_configured_default_kit(root)
        if cfg_kit is not None:
            normalized = cfg_kit
        else:
            detected = detect_sdd_kits(root)
            if len(detected) > 1:
                # Select the first detected kit that has active (non-done) tasks
                selected: str | None = None
                for cand in detected:
                    _, cand_adapter = _instantiate_adapter(cand, root)
                    try:
                        active_tasks = cand_adapter.load_tasks(include_done=False)
                        if len(active_tasks) > 0:
                            selected = cand
                            break
                    except Exception:
                        continue
                normalized = selected if selected is not None else detected[0]
            elif len(detected) == 1:
                normalized = detected[0]
            else:
                normalized = "bmad"

    return _instantiate_adapter(normalized, root)


__all__ = [
    "ADAPTER_REGISTRY",
    "BMADAdapter",
    "BeadsAdapter",
    "GSDHijackAdapter",
    "OpenSpecAdapter",
    "SDDAdapterProtocol",
    "SpecKitAdapter",
    "detect_sdd_kits",
    "diagnose_multi_kit_coexistence",
    "get_adapter",
]
