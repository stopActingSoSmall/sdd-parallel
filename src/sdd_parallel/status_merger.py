"""Deterministic 3-way status rank resolver for BMAD sprint-status.yaml,
Spec Kit/OpenSpec tasks.md, and Beads issues.jsonl.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sdd_parallel.models import TaskStatus

_DEV_STATUS_HEADER_RE = re.compile(r"^development_status:\s*$")
_KV_LINE_RE = re.compile(r"^(\s+)([A-Za-z0-9_.-]+):\s*([A-Za-z0-9_.-]+)(\s*(?:#.*)?)$")
_LAST_UPDATED_RE = re.compile(r"^(last_updated:\s*).*$")

_CHECKLIST_LINE_RE = re.compile(r"^(\s*[-*]\s*\[)([ xX])(\]\s*)(.+)$")
_CHECKLIST_KEY_RE = re.compile(r"^`?([A-Za-z]+[-_]?\d+|\d+[-_]\d+)`?\b", re.IGNORECASE)


def _extract_status_map(yaml_text: str) -> dict[str, str]:
    """Extract key -> status mapping inside development_status block."""
    yaml_text = yaml_text.lstrip("\ufeff")
    statuses: dict[str, str] = {}
    in_block = False
    for line in yaml_text.splitlines():
        if _DEV_STATUS_HEADER_RE.match(line):
            in_block = True
            continue
        if in_block:
            if line.strip() and not line.startswith(" ") and not line.startswith("#"):
                in_block = False
                continue
            match = _KV_LINE_RE.match(line)
            if match:
                key = match.group(2)
                val = match.group(3)
                statuses[key] = val
    return statuses


def _pick_highest_status(val_a: str | None, val_b: str | None, val_c: str | None) -> str:
    """Return status string with the highest monotonic progress rank."""
    candidates = [v for v in (val_a, val_b, val_c) if v is not None]
    if not candidates:
        return TaskStatus.BACKLOG.value
    best = max(candidates, key=lambda raw: TaskStatus.from_raw(raw).rank())
    return best


def merge_sprint_status_yaml(
    base_text: str,
    ours_text: str,
    theirs_text: str,
    updated_at: str | None = None,
) -> str:
    """Merge three versions of sprint-status.yaml by choosing max status rank per key.

    Preserves all comments and line structure of `ours_text`, while appending any new
    keys introduced only in `theirs_text`.
    """
    base_text = base_text.lstrip("\ufeff")
    ours_text = ours_text.lstrip("\ufeff")
    theirs_text = theirs_text.lstrip("\ufeff")

    base_map = _extract_status_map(base_text)
    ours_map = _extract_status_map(ours_text)
    theirs_map = _extract_status_map(theirs_text)

    merged_keys: dict[str, str] = {}
    all_keys = list(dict.fromkeys([*base_map.keys(), *ours_map.keys(), *theirs_map.keys()]))
    for key in all_keys:
        merged_keys[key] = _pick_highest_status(
            base_map.get(key),
            ours_map.get(key),
            theirs_map.get(key),
        )

    timestamp = updated_at or datetime.now(tz=UTC).strftime("%m-%d-%Y %H:%M")
    output_lines: list[str] = []
    seen_keys: set[str] = set()
    in_block = False
    last_indent = "  "

    for line in ours_text.splitlines():
        if _LAST_UPDATED_RE.match(line):
            output_lines.append(f'last_updated: "{timestamp}"')
            continue

        if _DEV_STATUS_HEADER_RE.match(line):
            in_block = True
            output_lines.append(line)
            continue

        if in_block:
            if line.strip() and not line.startswith(" ") and not line.startswith("#"):
                # Append any keys that existed only in theirs before leaving block
                for key in all_keys:
                    if key not in seen_keys:
                        output_lines.append(f"{last_indent}{key}: {merged_keys[key]}")
                        seen_keys.add(key)
                in_block = False
                output_lines.append(line)
                continue

            match = _KV_LINE_RE.match(line)
            if match:
                indent, key, _old_val, trailing = match.groups()
                last_indent = indent
                seen_keys.add(key)
                new_val = merged_keys.get(key, _old_val)
                output_lines.append(f"{indent}{key}: {new_val}{trailing}")
                continue

        output_lines.append(line)

    if in_block:
        for key in all_keys:
            if key not in seen_keys:
                output_lines.append(f"{last_indent}{key}: {merged_keys[key]}")
                seen_keys.add(key)

    return "\n".join(output_lines) + "\n"


def merge_sprint_status_files(
    base_path: Path,
    ours_path: Path,
    theirs_path: Path,
    output_path: Path,
    updated_at: str | None = None,
) -> None:
    """Read base/ours/theirs sprint-status.yaml files and write resolved YAML to output_path."""
    merged = merge_sprint_status_yaml(
        base_text=base_path.read_text(encoding="utf-8-sig"),
        ours_text=ours_path.read_text(encoding="utf-8-sig"),
        theirs_text=theirs_path.read_text(encoding="utf-8-sig"),
        updated_at=updated_at,
    )
    output_path.write_text(merged, encoding="utf-8")


def _extract_checklist_key(desc: str) -> str:
    """Extract semantic matching key for a markdown checklist item."""
    cleaned = desc.strip()
    cleaned = re.sub(r"^\[(?:p|parallel)\]\s*", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"^(?:US\d+|Phase\s*\d+)\s*[:/-]\s*", "", cleaned, flags=re.IGNORECASE).strip()
    m = _CHECKLIST_KEY_RE.match(cleaned)
    if m:
        return m.group(1).upper()
    return " ".join(cleaned.split()).lower()


def merge_markdown_task_checklists(
    base_text: str,
    ours_text: str,
    theirs_text: str,
) -> str:
    """Monotonic 3-way merge for Markdown task checklists (Spec Kit & OpenSpec tasks.md).

    Applies the join semi-lattice max(todo, done) = done across ours and theirs:
    if a task is checked [x] in either branch, it is marked [x] in the result.
    Preserves all line ordering, indentation, and comments of ours_text without conflict markers.
    """
    base_text = base_text.lstrip("\ufeff")
    ours_text = ours_text.lstrip("\ufeff")
    theirs_text = theirs_text.lstrip("\ufeff")

    def get_done_keys(text: str) -> set[str]:
        done: set[str] = set()
        for line in text.splitlines():
            m = _CHECKLIST_LINE_RE.match(line)
            if m and m.group(2).lower() == "x":
                done.add(_extract_checklist_key(m.group(4)))
        return done

    all_done_keys = get_done_keys(ours_text) | get_done_keys(theirs_text)

    output_lines: list[str] = []
    seen_keys: set[str] = set()

    for line in ours_text.splitlines():
        m = _CHECKLIST_LINE_RE.match(line)
        if m:
            key = _extract_checklist_key(m.group(4))
            seen_keys.add(key)
            if key in all_done_keys:
                output_lines.append(f"{m.group(1)}x{m.group(3)}{m.group(4)}")
            else:
                output_lines.append(line)
        else:
            output_lines.append(line)

    # Append any brand-new checklist items introduced solely in theirs
    new_theirs: list[str] = []
    for line in theirs_text.splitlines():
        m = _CHECKLIST_LINE_RE.match(line)
        if m:
            key = _extract_checklist_key(m.group(4))
            if key not in seen_keys:
                seen_keys.add(key)
                if key in all_done_keys:
                    new_theirs.append(f"{m.group(1)}x{m.group(3)}{m.group(4)}")
                else:
                    new_theirs.append(line)

    if new_theirs:
        output_lines.extend(new_theirs)

    return "\n".join(output_lines) + "\n"


def merge_beads_issues_jsonl(
    base_text: str,
    ours_text: str,
    theirs_text: str,
) -> str:
    """Monotonic 3-way status merge for Beads issue tracker (.beads/issues.jsonl).

    Chooses the issue record with the highest TaskStatus rank per issue id,
    resolving conflicts without <<<<<<< conflict markers.
    """
    base_text = base_text.lstrip("\ufeff")
    ours_text = ours_text.lstrip("\ufeff")
    theirs_text = theirs_text.lstrip("\ufeff")

    def parse_jsonl(text: str) -> dict[str, dict[str, Any]]:
        records: dict[str, dict[str, Any]] = {}
        for line in text.splitlines():
            line_str = line.strip()
            if not line_str:
                continue
            try:
                obj = json.loads(line_str)
                if isinstance(obj, dict) and "id" in obj:
                    records[str(obj["id"])] = obj
            except json.JSONDecodeError:
                continue
        return records

    base_map = parse_jsonl(base_text)
    ours_map = parse_jsonl(ours_text)
    theirs_map = parse_jsonl(theirs_text)

    all_ids = list(dict.fromkeys([*ours_map.keys(), *theirs_map.keys(), *base_map.keys()]))
    merged_records: list[dict[str, Any]] = []

    for issue_id in all_ids:
        base_obj = base_map.get(issue_id)
        ours_obj = ours_map.get(issue_id)
        theirs_obj = theirs_map.get(issue_id)

        if ours_obj is None and theirs_obj is not None:
            merged_records.append(theirs_obj)
            continue
        if theirs_obj is None and ours_obj is not None:
            merged_records.append(ours_obj)
            continue
        if ours_obj is not None and theirs_obj is not None:
            rank_ours = TaskStatus.from_raw(str(ours_obj.get("status", "backlog"))).rank()
            rank_theirs = TaskStatus.from_raw(str(theirs_obj.get("status", "backlog"))).rank()

            if rank_theirs > rank_ours:
                merged_records.append(theirs_obj)
            elif rank_ours > rank_theirs:
                merged_records.append(ours_obj)
            else:
                if theirs_obj != base_obj and ours_obj == base_obj:
                    merged_records.append(theirs_obj)
                else:
                    merged_records.append(ours_obj)

    lines = [json.dumps(rec, ensure_ascii=False) for rec in merged_records]
    return "\n".join(lines) + ("\n" if lines else "")


__all__ = [
    "merge_beads_issues_jsonl",
    "merge_markdown_task_checklists",
    "merge_sprint_status_files",
    "merge_sprint_status_yaml",
]
