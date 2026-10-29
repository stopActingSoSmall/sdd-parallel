"""Portable plugin packager for Google Antigravity (~/.gemini/config/plugins/sdd-parallel/)."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any


def default_plugin_dir() -> Path:
    """Return standard Google Antigravity plugin installation directory."""
    return Path.home() / ".gemini" / "config" / "plugins" / "sdd-parallel"


def install_plugin(
    target_dir: Path | None = None,
    package_root: Path | None = None,
) -> dict[str, str]:
    """Generate portable sdd-parallel plugin bundle.

    Emits plugin.json, hooks.json, mcp_config.json, and SKILL.md.
    """
    dest = (target_dir or default_plugin_dir()).resolve()
    pkg_root = (
        package_root.resolve()
        if package_root is not None
        else Path(__file__).resolve().parent.parent.parent
    )
    dest.mkdir(parents=True, exist_ok=True)
    skill_dest_dir = dest / "skills" / "sdd-parallel"
    skill_dest_dir.mkdir(parents=True, exist_ok=True)

    python_exe = str(Path(sys.executable).resolve())
    hook_cmd = f"{python_exe} -X utf8 -m sdd_parallel.hooks.blast_radius_guard"

    plugin_manifest: dict[str, Any] = {
        "name": "sdd-parallel",
        "version": "0.1.0",
        "description": (
            "SDD-agnostic multi-track parallel roadmap orchestrator with "
            "AST/CRG blast-radius locks and git worktree automation."
        ),
        "package_root": pkg_root.as_posix(),
        "skills": ["skills/sdd-parallel/SKILL.md"],
        "hooks": "hooks.json",
        "mcp_config": "mcp_config.json",
    }

    hooks_manifest: dict[str, Any] = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": (
                        "write_to_file|replace_file_content|"
                        "multi_replace_file_content|Write|Edit|MultiEdit"
                    ),
                    "hooks": [
                        {
                            "type": "command",
                            "command": hook_cmd,
                        }
                    ],
                }
            ]
        }
    }

    mcp_manifest: dict[str, Any] = {
        "mcpServers": {
            "code-review-graph": {
                "command": "uvx",
                "args": ["code-review-graph", "serve"],
            }
        }
    }

    plugin_json_path = dest / "plugin.json"
    hooks_json_path = dest / "hooks.json"
    mcp_json_path = dest / "mcp_config.json"
    skill_md_path = skill_dest_dir / "SKILL.md"

    plugin_json_path.write_text(
        json.dumps(plugin_manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    hooks_json_path.write_text(
        json.dumps(hooks_manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    mcp_json_path.write_text(
        json.dumps(mcp_manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    src_skill = pkg_root / ".agent" / "skills" / "sdd-parallel" / "SKILL.md"
    if src_skill.is_file():
        shutil.copyfile(src_skill, skill_md_path)
    else:
        try:
            import importlib.resources

            resource_skill = (
                importlib.resources.files("sdd_parallel.resources")
                .joinpath("SKILL.md")
                .read_text(encoding="utf-8")
            )
            skill_md_path.write_text(resource_skill, encoding="utf-8")
        except Exception:
            skill_md_path.write_text(
                "---\nname: sdd-parallel\n"
                "description: SDD-agnostic multi-track parallel roadmap orchestrator.\n---\n"
                "# SDD-Parallel Skill\n",
                encoding="utf-8",
            )

    return {
        "plugin_dir": dest.as_posix(),
        "plugin_json": plugin_json_path.as_posix(),
        "hooks_json": hooks_json_path.as_posix(),
        "mcp_config_json": mcp_json_path.as_posix(),
        "skill_md": skill_md_path.as_posix(),
    }


def sanitize_antigravity_settings_hooks(
    settings_path: Path,
    backup: bool = True,
) -> dict[str, Any]:
    """Remove broken legacy GSD hooks from settings.json while preserving valid settings."""
    if not settings_path.is_file():
        return {"modified": False, "removed_hooks": 0}

    raw_text = settings_path.read_text(encoding="utf-8")
    data: dict[str, Any] = json.loads(raw_text)
    if backup:
        backup_path = settings_path.with_suffix(".json.bak")
        backup_path.write_text(raw_text, encoding="utf-8")

    hooks_section = data.get("hooks")
    removed = 0
    if isinstance(hooks_section, dict):
        for event_name, rules in list(hooks_section.items()):
            if not isinstance(rules, list):
                continue
            kept_rules: list[Any] = []
            for rule in rules:
                rule_str = json.dumps(rule)
                if "gsd-" in rule_str or "get-shit-done" in rule_str:
                    removed += 1
                else:
                    kept_rules.append(rule)
            hooks_section[event_name] = kept_rules

    if removed > 0:
        settings_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    return {"modified": removed > 0, "removed_hooks": removed}
