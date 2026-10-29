"""Unit tests for portable plugin installer, settings sanitizer, and Autopilot launcher."""

from __future__ import annotations

import json
from pathlib import Path

from sdd_parallel.autopilot import AutopilotLauncher
from sdd_parallel.cli import main
from sdd_parallel.models import EffortTier, TaskNode
from sdd_parallel.plugin_installer import (
    install_plugin,
    sanitize_antigravity_settings_hooks,
)
from sdd_parallel.scheduler import BSPWaveScheduler


def test_install_plugin_creates_bundle(tmp_path: Path) -> None:
    target = tmp_path / "plugins" / "sdd-parallel"
    manifest_paths = install_plugin(target_dir=target)

    assert Path(manifest_paths["plugin_json"]).is_file()
    assert Path(manifest_paths["hooks_json"]).is_file()
    assert Path(manifest_paths["mcp_config_json"]).is_file()
    assert Path(manifest_paths["skill_md"]).is_file()

    hooks_data = json.loads(Path(manifest_paths["hooks_json"]).read_text(encoding="utf-8"))
    pre_tool = hooks_data["hooks"]["PreToolUse"][0]
    assert "write_to_file" in pre_tool["matcher"]
    assert "sdd_parallel.hooks.blast_radius_guard" in pre_tool["hooks"][0]["command"]


def test_install_plugin_falls_back_to_embedded_skill_resource(tmp_path: Path) -> None:
    target = tmp_path / "plugins" / "sdd-parallel"
    empty_pkg_root = tmp_path / "empty_pkg"
    empty_pkg_root.mkdir()

    manifest_paths = install_plugin(target_dir=target, package_root=empty_pkg_root)
    skill_file = Path(manifest_paths["skill_md"])
    assert skill_file.is_file()
    content = skill_file.read_text(encoding="utf-8")

    # Must be the full skill from resources, NOT the placeholder
    assert "# ⚡ SDD-Parallel — Multi-Track Orchestration Skill" in content
    assert "Complete CLI Command Reference" in content


def test_sanitize_antigravity_settings_hooks_removes_broken_gsd_hooks(
    tmp_path: Path,
) -> None:
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": (
                                        "node /mock/user/.claude/hooks/gsd-check-update.js"
                                    ),
                                }
                            ]
                        }
                    ],
                    "PreToolUse": [
                        {
                            "matcher": "write_to_file",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "python -m custom_guard",
                                }
                            ],
                        }
                    ],
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    res = sanitize_antigravity_settings_hooks(settings_file, backup=True)
    assert res["modified"] is True
    assert res["removed_hooks"] == 1
    assert settings_file.with_suffix(".json.bak").is_file()

    updated = json.loads(settings_file.read_text(encoding="utf-8"))
    assert updated["hooks"]["SessionStart"] == []
    assert len(updated["hooks"]["PreToolUse"]) == 1


def test_autopilot_launcher_routes_models_across_harnesses(tmp_path: Path) -> None:
    tasks = [
        TaskNode(
            id="11-2",
            title="High effort story",
            effort=EffortTier.HIGH,
            write_files=["src/demo/a.py"],
        ),
        TaskNode(
            id="14-3",
            title="Low effort story",
            effort=EffortTier.LOW,
            write_files=["src/demo/b.py"],
        ),
    ]
    scheduler = BSPWaveScheduler(max_tracks=2)
    plan = scheduler.schedule(tasks)

    launcher = AutopilotLauncher(tmp_path, agentapi_path=Path("agentapi.bat"))
    ag_specs = launcher.build_launch_specs(plan, wave_number=1, harness="antigravity")
    assert len(ag_specs) == 2
    assert ag_specs[0]["model"] == "pro"
    assert ag_specs[1]["model"] == "flash_lite"
    assert "new-conversation" in ag_specs[0]["command_line"]
    assert "--cwd=" not in ag_specs[0]["command_line"]

    cc_specs = launcher.build_launch_specs(plan, wave_number=1, harness="claude-code")
    assert cc_specs[0]["model"] == "claude-opus-4-6"
    assert cc_specs[1]["model"] == "claude-haiku-4-5"

    cx_specs = launcher.build_launch_specs(plan, wave_number=1, harness="codex")
    assert cx_specs[0]["model"] == "o3-high"
    assert cx_specs[1]["model"] == "codex-mini"

    # CLI install-plugin check
    rc = main(["install-plugin", "--target-dir", str(tmp_path / "cli-plugin")])
    assert rc == 0
    assert (tmp_path / "cli-plugin" / "plugin.json").is_file()

    # CLI autopilot missing wave check
    rc_err = main(["autopilot", "--repo", str(tmp_path), "--wave", "99"])
    assert rc_err == 1
