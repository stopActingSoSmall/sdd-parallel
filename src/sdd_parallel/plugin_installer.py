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

    python_exe = Path(sys.executable).resolve().as_posix()
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
                        "multi_replace_file_content|Write|Edit|MultiEdit|"
                        "NotebookEdit|Bash|run_command|cmd|powershell"
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


def configure_claude_settings(target_dir: Path) -> Path:
    """Configure Claude Code settings.local.json with directory lock and non-blocking refusal.

    Ensures native directory write protection (Claude Code v2.1.314+) and
    non-blocking refusal (denyInsteadOfPrompting in v2.1.319+) without
    polluting git status (when paired with git info/exclude).
    """
    claude_dir = target_dir / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    settings_file = claude_dir / "settings.local.json"
    data: dict[str, Any] = {}
    if settings_file.is_file():
        try:
            data = json.loads(settings_file.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    perms = data.setdefault("permissions", {})
    if isinstance(perms, dict):
        perms["blockWritesOutsideWorkingDirectories"] = True
        perms["denyInsteadOfPrompting"] = True
    else:
        data["permissions"] = {
            "blockWritesOutsideWorkingDirectories": True,
            "denyInsteadOfPrompting": True,
        }
    sandbox = data.setdefault("sandbox", {})
    if isinstance(sandbox, dict):
        sandbox["autoAllowBashIfSandboxed"] = True
    else:
        data["sandbox"] = {"autoAllowBashIfSandboxed": True}
    settings_file.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return settings_file


def default_spec_kit_commands() -> list[str]:
    """Return standard list of Spec Kit extension command names."""
    return ["plan-waves", "prepare-wave", "merge-wave"]


def install_spec_kit_extension(
    target_dir: Path | None = None,
    package_root: Path | None = None,
) -> dict[str, str]:
    """Package and install GitHub Spec Kit extension bundle (extension.yml and commands/)."""
    pkg_root = (
        package_root.resolve()
        if package_root is not None
        else Path(__file__).resolve().parent.parent.parent
    )
    dest = (target_dir or pkg_root).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    commands_dest_dir = dest / "commands"
    commands_dest_dir.mkdir(parents=True, exist_ok=True)

    src_manifest = pkg_root / "extension.yml"
    dest_manifest = dest / "extension.yml"
    if src_manifest.is_file() and src_manifest.resolve() != dest_manifest.resolve():
        shutil.copyfile(src_manifest, dest_manifest)
    elif not dest_manifest.is_file():
        desc_text = (
            "Deterministic BSP wave scheduler and isolated git worktrees for Spec Kit"
        )
        dest_manifest.write_text(
            'schema_version: "1.0"\n'
            'id: "sdd-parallel"\n'
            'name: "SDD-Parallel Waves & Worktrees"\n'
            'version: "0.2.1"\n'
            f'description: "{desc_text}"\n'
            'requires:\n'
            '  speckit_version: ">=0.1.0"\n'
            'commands:\n'
            '  plan-waves: commands/plan-waves.md\n'
            '  prepare-wave: commands/prepare-wave.md\n'
            '  merge-wave: commands/merge-wave.md\n'
            'extension:\n'
            '  id: "sdd-parallel"\n'
            '  name: "SDD-Parallel Waves & Worktrees"\n'
            '  version: "0.2.1"\n'
            f'  description: "{desc_text}"\n',
            encoding="utf-8",
        )

    src_commands_dir = pkg_root / "commands"
    installed_commands: dict[str, str] = {}
    for cmd_name in ("plan-waves.md", "prepare-wave.md", "merge-wave.md"):
        src_cmd = src_commands_dir / cmd_name
        dest_cmd = commands_dest_dir / cmd_name
        if src_cmd.is_file() and src_cmd.resolve() != dest_cmd.resolve():
            shutil.copyfile(src_cmd, dest_cmd)
        installed_commands[cmd_name] = dest_cmd.as_posix()

    return {
        "extension_dir": dest.as_posix(),
        "extension_yml": dest_manifest.as_posix(),
        "commands_dir": commands_dest_dir.as_posix(),
        **installed_commands,
    }


def validate_spec_kit_extension(
    package_root: Path | None = None,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    """Validate Spec Kit extension.yml and command files per upstream specification."""
    pkg_root = (
        package_root.resolve()
        if package_root is not None
        else Path(__file__).resolve().parent.parent.parent
    )
    manifest_file = manifest_path or (pkg_root / "extension.yml")
    errors: list[str] = []
    warnings: list[str] = []

    if not manifest_file.is_file():
        return {
            "valid": False,
            "errors": [f"Missing Spec Kit extension manifest at {manifest_file.as_posix()}"],
            "warnings": warnings,
            "manifest": None,
            "commands": {},
        }

    from ruamel.yaml import YAML

    yaml_parser = YAML(typ="safe")
    try:
        data = yaml_parser.load(manifest_file.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return {
            "valid": False,
            "errors": [f"Failed to parse YAML from {manifest_file.as_posix()}: {exc}"],
            "warnings": warnings,
            "manifest": None,
            "commands": {},
        }

    if not isinstance(data, dict):
        return {
            "valid": False,
            "errors": ["Root of extension.yml must be a mapping/dictionary"],
            "warnings": warnings,
            "manifest": None,
            "commands": {},
        }

    schema_version = str(data.get("schema_version", ""))
    if schema_version != "1.0":
        errors.append(f"Invalid schema_version '{schema_version}': expected '1.0'")

    ext_obj = data.get("extension")
    ext_id = (
        data.get("id")
        or (ext_obj.get("id") if isinstance(ext_obj, dict) else None)
    )
    if not ext_id or ext_id != "sdd-parallel":
        errors.append(f"Invalid extension id '{ext_id}': expected 'sdd-parallel'")

    description = (
        data.get("description")
        or (ext_obj.get("description") if isinstance(ext_obj, dict) else "")
        or ""
    )
    if not description:
        errors.append("Missing required 'description' field")
    elif len(description) >= 100:
        errors.append(
            f"Description length ({len(description)}) exceeds Spec Kit limit (<100 chars)"
        )

    requires = data.get("requires")
    if not isinstance(requires, dict) or "speckit_version" not in requires:
        errors.append("Missing 'requires.speckit_version' dependency declaration")

    commands_map: dict[str, str] = {}
    commands_section = data.get("commands")
    if isinstance(commands_section, dict):
        for k, v in commands_section.items():
            commands_map[str(k)] = str(v)
    elif isinstance(commands_section, list):
        for item in commands_section:
            if isinstance(item, dict) and "name" in item and "path" in item:
                commands_map[str(item["name"])] = str(item["path"])

    required_commands = ("plan-waves", "prepare-wave", "merge-wave")
    for cmd in required_commands:
        if cmd not in commands_map:
            errors.append(f"Missing required command '{cmd}' in extension.yml commands section")
        else:
            rel_cmd_path = commands_map[cmd]
            cmd_file = pkg_root / rel_cmd_path
            if not cmd_file.is_file():
                errors.append(
                    f"Command file for '{cmd}' not found at {cmd_file.as_posix()}"
                )
            else:
                cmd_text = cmd_file.read_text(encoding="utf-8-sig")
                if not cmd_text.startswith("---"):
                    errors.append(
                        f"Command file '{rel_cmd_path}' missing opening YAML frontmatter '---'"
                    )
                else:
                    parts = cmd_text.split("---", 2)
                    if len(parts) < 3:
                        errors.append(
                            f"Command file '{rel_cmd_path}' has malformed frontmatter"
                        )
                    else:
                        fm = parts[1]
                        if (
                            f"name: {cmd}" not in fm
                            and f"name: '{cmd}'" not in fm
                            and f'name: "{cmd}"' not in fm
                        ):
                            errors.append(
                                f"Command file '{rel_cmd_path}' frontmatter missing 'name: {cmd}'"
                            )
                        if "syntax:" not in fm:
                            warnings.append(
                                f"Command file '{rel_cmd_path}' recommended 'syntax:' missing"
                            )

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "manifest": data,
        "commands": commands_map,
    }


