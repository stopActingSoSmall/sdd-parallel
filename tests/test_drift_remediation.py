"""Tests for Autonomous Drift Remediation Engine & Subagent Triage."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
_SRC_DIR = _REPO_ROOT / "src"

for p in (str(_REPO_ROOT), str(_SRC_DIR), str(_SCRIPTS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

import scripts.drift_sentinel as sentinel  # noqa: E402
from scripts.remediate_drift import (  # noqa: E402
    ModelEscalationBlockedError,
    QuotaGate,
    compress_agents_md,
    plan_tier3_i18n_tasks,
    remediate_adapter_matrix,
    remediate_cli_subcommands,
    remediate_repository,
    remediate_tier1_sanitation,
    sanitize_workstation_paths,
)


def test_sanitize_workstation_paths() -> None:
    repo = Path("C:/Users/admin/_PROJECTS/sdd-parallel")
    raw_text = (
        "Root is at C:\\Users\\admin\\_PROJECTS\\sdd-parallel/docs.\n"
        "Config in C:/Users/admin/settings.json.\n"
        "User folder: users/admin/code or users\\admin\\tests.\n"
        "Another drive path: D:\\Users\\admin\\repo.\n"
        "Unix style: /Users/admin/data or /home/admin/data.\n"
    )

    sanitized, count = sanitize_workstation_paths(raw_text, repo_root=repo)
    assert count > 0

    lower = sanitized.lower()
    assert "users/admin" not in lower
    assert "users\\admin" not in lower
    assert "c:\\users\\" not in lower
    assert "c:/users/" not in lower


def test_compress_agents_md() -> None:
    uncompressed = (
        "# Title    \n"
        "Directive 1.   \n\n\n\n"
        "Directive 2.\n"
        "<!-- Comment to strip if needed -->\n"
        "Directive 3.  \n"
    )

    compressed, changed = compress_agents_md(uncompressed, max_bytes=50)
    assert changed is True
    assert "    \n" not in compressed
    assert "\n\n\n" not in compressed
    assert len(compressed.encode("utf-8")) < len(uncompressed.encode("utf-8"))


def test_tier1_sanitation_file_patching(tmp_path: Path) -> None:
    target_file = tmp_path / "leaky_doc.md"
    target_file.write_text(
        "File located at C:\\Users\\admin\\_PROJECTS\\sdd-parallel\\test.py\n",
        encoding="utf-8",
    )

    actions = remediate_tier1_sanitation(
        repo_root=tmp_path,
        dry_run=False,
        target_files=["leaky_doc.md"],
    )
    assert len(actions) == 1
    content = target_file.read_text(encoding="utf-8").lower()
    assert "users\\admin" not in content


def test_tier2_cli_subcommands_sync(tmp_path: Path) -> None:
    skills_dir = tmp_path / ".agent" / "skills" / "sdd-parallel"
    skills_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skills_dir / "SKILL.md"

    # Minimal skill file with only 1 command documented
    skill_file.write_text(
        "# Skill\n\n## Complete CLI Command Reference\n- `plan`: Plan waves.\n",
        encoding="utf-8",
    )

    actions = remediate_cli_subcommands(tmp_path, dry_run=False)
    assert len(actions) == 1

    updated_text = skill_file.read_text(encoding="utf-8")
    assert "`prepare`" in updated_text
    assert "`verify-wave`" in updated_text
    assert "`rollback-wave`" in updated_text


def test_tier2_adapter_matrix_sync(tmp_path: Path) -> None:
    matrix_dir = (
        tmp_path / ".agents" / "skills" / "sdd-dev-playbooks" / "references"
    )
    matrix_dir.mkdir(parents=True, exist_ok=True)
    matrix_file = matrix_dir / "adapter-touchpoint-matrix.md"

    matrix_file.write_text(
        "# Matrix\n\n"
        "| Framework | Adapter Path | Description | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |\n"
        "| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |\n"
        "| **`bmad`** | `src/...` | BMAD | [x] | [x] | [x] | [x] | [x] | [x] | [x] | [x] |\n",
        encoding="utf-8",
    )

    actions = remediate_adapter_matrix(tmp_path, dry_run=False)
    assert len(actions) == 1

    updated_text = matrix_file.read_text(encoding="utf-8")
    assert "**`spec-kit`**" in updated_text
    assert "**`gsd`**" in updated_text
    assert "**`openspec`**" in updated_text
    assert "**`beads`**" in updated_text


def test_tier3_i18n_subagent_task_generation(tmp_path: Path) -> None:
    # Setup canonical doc with 2 H2 sections
    canonical_file = tmp_path / "README.md"
    canonical_file.write_text(
        "# Title\n\n## Section Alpha\nAlpha text.\n\n## Section Beta\nBeta text.\n",
        encoding="utf-8",
    )

    # Setup translation doc with only 1 H2 section
    i18n_ru_dir = tmp_path / "docs" / "i18n" / "ru"
    i18n_ru_dir.mkdir(parents=True, exist_ok=True)
    translated_file = i18n_ru_dir / "README.md"
    translated_file.write_text(
        "# Заголовок\n\n## Секция Альфа\nТекст альфа.\n",
        encoding="utf-8",
    )

    tasks = plan_tier3_i18n_tasks(tmp_path, model="flash")
    assert len(tasks) == 1

    task = tasks[0]
    assert task.language_code == "ru"
    assert task.language_name == "Russian"
    assert "Section Beta" in task.missing_h2_titles
    assert "Isolated Localization Subagent Task" in task.prompt
    assert "Mandatory Invariants:" in task.prompt


def test_argon_quota_gate() -> None:
    # Free models allowed by default
    assert QuotaGate.validate_model("flash") == "flash"
    assert QuotaGate.validate_model("flash_lite") == "flash_lite"
    assert QuotaGate.validate_model("inherit") == "inherit"

    # Pro models blocked without operator approval
    with pytest.raises(ModelEscalationBlockedError, match="blocked by Argon Quota Gate"):
        QuotaGate.validate_model("pro", operator_approved=False)

    with pytest.raises(ModelEscalationBlockedError, match="blocked by Argon Quota Gate"):
        QuotaGate.validate_model("gemini-1.5-pro", operator_approved=False)

    with pytest.raises(ModelEscalationBlockedError, match="blocked by Argon Quota Gate"):
        QuotaGate.validate_model("claude-3-opus", operator_approved=False)

    # Pro models allowed with operator approval
    assert QuotaGate.validate_model("pro", operator_approved=True) == "pro"


def test_remediate_repository_dry_run(tmp_path: Path) -> None:
    doc_file = tmp_path / "doc.md"
    initial_content = "Path is C:\\Users\\admin\\_PROJECTS\\sdd-parallel\n"
    doc_file.write_text(initial_content, encoding="utf-8")

    result = remediate_repository(
        repo_root=tmp_path,
        tier="1",
        dry_run=True,
    )
    # Dry-run plans actions but does not rewrite file on disk
    assert result.tier_requested == "1"
    assert doc_file.read_text(encoding="utf-8") == initial_content


def test_drift_sentinel_auto_fix_flag(tmp_path: Path) -> None:
    # Setup skill file missing subcommands
    skills_dir = tmp_path / ".agent" / "skills" / "sdd-parallel"
    skills_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skills_dir / "SKILL.md"
    skill_file.write_text("# Skill\n\n## Complete CLI Command Reference\n", encoding="utf-8")

    # Run sentinel with --auto-fix
    code = sentinel.main(["--repo", str(tmp_path), "--auto-fix"])
    assert code == 0
    # After auto-fix, SKILL.md has subcommands populated
    updated_skill = skill_file.read_text(encoding="utf-8")
    assert "`plan`" in updated_skill
    assert "`verify-wave`" in updated_skill
