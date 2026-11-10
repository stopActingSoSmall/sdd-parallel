"""Unit tests for GitHub Spec Kit Extension packaging and commands (Task 36)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from sdd_parallel.adapters.spec_kit import SpecKitAdapter
from sdd_parallel.plugin_installer import (
    default_spec_kit_commands,
    install_spec_kit_extension,
    validate_spec_kit_extension,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent


def test_spec_kit_extension_manifest_contract() -> None:
    manifest_path = _REPO_ROOT / "extension.yml"
    assert manifest_path.is_file(), f"extension.yml missing at {_REPO_ROOT}"

    yaml_parser = YAML(typ="safe")
    data: dict[str, Any] = yaml_parser.load(manifest_path.read_text(encoding="utf-8-sig"))

    assert data.get("schema_version") == "1.0"
    assert data.get("id") == "sdd-parallel"
    assert data.get("name") == "SDD-Parallel Waves & Worktrees"
    assert data.get("version") == "0.2.1"

    desc = data.get("description", "")
    assert isinstance(desc, str) and 0 < len(desc) < 100, (
        f"Description must be between 1 and 99 characters, got {len(desc)}: '{desc}'"
    )

    requires = data.get("requires", {})
    assert isinstance(requires, dict)
    assert requires.get("speckit_version") == ">=0.1.0"

    commands = data.get("commands", {})
    assert isinstance(commands, dict)
    for expected_cmd in ("plan-waves", "prepare-wave", "merge-wave"):
        assert expected_cmd in commands, f"Missing command '{expected_cmd}' in commands mapping"
        cmd_rel_path = commands[expected_cmd]
        cmd_file = _REPO_ROOT / cmd_rel_path
        assert cmd_file.is_file(), f"Command file for '{expected_cmd}' not found at {cmd_file}"


def test_spec_kit_command_markdown_structure() -> None:
    yaml_parser = YAML(typ="safe")
    expected_commands = default_spec_kit_commands()
    assert expected_commands == ["plan-waves", "prepare-wave", "merge-wave"]

    for cmd_name in expected_commands:
        cmd_path = _REPO_ROOT / "commands" / f"{cmd_name}.md"
        assert cmd_path.is_file(), f"Command file {cmd_path} does not exist"

        text = cmd_path.read_text(encoding="utf-8-sig")
        assert text.startswith("---"), f"{cmd_name}.md missing opening '---'"
        parts = text.split("---", 2)
        assert len(parts) >= 3, f"{cmd_name}.md malformed YAML frontmatter"

        fm = yaml_parser.load(parts[1])
        assert isinstance(fm, dict)
        assert fm.get("name") == cmd_name
        assert fm.get("description"), f"{cmd_name}.md has empty description"
        assert fm.get("syntax"), f"{cmd_name}.md has empty syntax"

        reqs = fm.get("requirements", {})
        assert isinstance(reqs, dict)
        tools = reqs.get("tools", [])
        assert "uv" in tools, f"{cmd_name}.md requirements must include 'uv'"
        assert "git" in tools, f"{cmd_name}.md requirements must include 'git'"

        body = parts[2]
        assert "## 🎯 Purpose & Behavior" in body or "## Instructions for Agent" in body


def test_spec_kit_adapter_extension_api() -> None:
    adapter = SpecKitAdapter(_REPO_ROOT)
    assert adapter.has_extension_manifest() is True
    assert adapter.get_extension_manifest_path() == _REPO_ROOT / "extension.yml"

    manifest = adapter.load_extension_manifest()
    assert manifest is not None
    assert manifest.get("id") == "sdd-parallel"

    commands = adapter.get_extension_commands()
    assert sorted(commands) == ["merge-wave", "plan-waves", "prepare-wave"]

    validation = adapter.validate_extension()
    assert validation["valid"] is True, f"Validation failed with errors: {validation['errors']}"
    assert len(validation["errors"]) == 0


def test_spec_kit_extension_validation_error_cases(tmp_path: Path) -> None:
    # 1. Missing manifest
    res = validate_spec_kit_extension(package_root=tmp_path)
    assert res["valid"] is False
    assert any("Missing Spec Kit extension manifest" in err for err in res["errors"])

    commands_yaml = (
        "commands:\n"
        '  plan-waves: "commands/plan-waves.md"\n'
        '  prepare-wave: "commands/prepare-wave.md"\n'
        '  merge-wave: "commands/merge-wave.md"\n'
    )

    # 2. Schema version invalid
    man_path = tmp_path / "extension.yml"
    man_path.write_text(
        'schema_version: "2.0"\n'
        'id: "sdd-parallel"\n'
        'description: "Short desc"\n'
        'requires: { speckit_version: ">=0.1.0" }\n' + commands_yaml,
        encoding="utf-8",
    )
    res = validate_spec_kit_extension(package_root=tmp_path)
    assert res["valid"] is False
    assert any("Invalid schema_version" in err for err in res["errors"])

    # 3. Description exceeding 99 characters
    man_path.write_text(
        'schema_version: "1.0"\n'
        'id: "sdd-parallel"\n'
        f'description: "{"A" * 105}"\n'
        'requires: { speckit_version: ">=0.1.0" }\n' + commands_yaml,
        encoding="utf-8",
    )
    res = validate_spec_kit_extension(package_root=tmp_path)
    assert res["valid"] is False
    assert any("limit (<100 chars)" in err for err in res["errors"])

    # 4. Missing command file
    man_path.write_text(
        'schema_version: "1.0"\n'
        'id: "sdd-parallel"\n'
        'description: "Valid desc"\n'
        'requires: { speckit_version: ">=0.1.0" }\n' + commands_yaml,
        encoding="utf-8",
    )
    res = validate_spec_kit_extension(package_root=tmp_path)
    assert res["valid"] is False
    assert any("Command file for 'plan-waves' not found" in err for err in res["errors"])


def test_install_spec_kit_extension_roundtrip(tmp_path: Path) -> None:
    installed = install_spec_kit_extension(target_dir=tmp_path, package_root=_REPO_ROOT)
    assert (tmp_path / "extension.yml").is_file()
    assert (tmp_path / "commands" / "plan-waves.md").is_file()
    assert (tmp_path / "commands" / "prepare-wave.md").is_file()
    assert (tmp_path / "commands" / "merge-wave.md").is_file()
    assert installed["extension_dir"] == tmp_path.resolve().as_posix()

    validation = validate_spec_kit_extension(package_root=tmp_path)
    assert validation["valid"] is True, f"Errors in installed bundle: {validation['errors']}"
    assert len(validation["errors"]) == 0


def test_spec_kit_distribution_artifacts() -> None:
    tag = "v0.2.1"
    archive_url = f"https://github.com/stopActingSoSmall/sdd-parallel/archive/refs/tags/{tag}.zip"
    expected_direct_cmd = f"specify extension add --from {archive_url}"
    assert "v0.2.1.zip" in expected_direct_cmd
    assert expected_direct_cmd.startswith("specify extension add --from https://github.com/")

    # Catalog submission structure
    catalog_entry = {
        "id": "sdd-parallel",
        "name": "SDD-Parallel Waves & Worktrees",
        "description": "Deterministic BSP wave scheduler and isolated git worktrees for Spec Kit",
        "repository": "https://github.com/stopActingSoSmall/sdd-parallel",
        "version": "0.2.1",
        "tags": ["waves", "parallel", "git-worktree", "merge-conflicts"],
    }
    assert len(catalog_entry["description"]) < 100
    assert catalog_entry["id"] == "sdd-parallel"
