"""PreToolUse runtime scope guard enforcing .parallel-lock.json inside git worktrees."""

from __future__ import annotations

import fnmatch
import json
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from sdd_parallel.models import LockManifest

_WRITE_TOOLS = frozenset(
    {
        "write_to_file",
        "replace_file_content",
        "multi_replace_file_content",
        "Write",
        "Edit",
        "MultiEdit",
    }
)

_EXEC_TOOLS = frozenset(
    {
        "Bash",
        "bash",
        "run_command",
        "execute_command",
        "cmd",
        "powershell",
        "terminal",
        "Terminal",
    }
)

_TARGET_PATH_KEYS = (
    "TargetFile",
    "file_path",
    "path",
    "AbsolutePath",
)

_COMMAND_KEYS = (
    "command",
    "CommandLine",
    "cmd",
    "input",
    "script",
)

_CMD_WRITE_INDICATORS = (
    ">",
    ">>",
    "sed -i",
    "tee ",
    "rm ",
    "rmdir ",
    "del ",
    "erase ",
    "mv ",
    "move ",
    "cp ",
    "copy ",
    "Set-Content",
    "Out-File",
    "Add-Content",
    "Remove-Item",
    "'w'",
    '"w"',
    "'a'",
    '"a"',
    "'w+'",
    '"w+"',
    "'x'",
    '"x"',
    "write_text",
    "write_bytes",
    ".write(",
)


@dataclass(slots=True, frozen=True)
class HookDecision:
    """Decision returned to the agent harness PreToolUse hook."""

    decision: Literal["allow", "deny"]
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"decision": self.decision}
        if self.reason:
            payload["reason"] = self.reason
        return payload


def find_lock_manifest(start_path: Path) -> tuple[Path, LockManifest] | None:
    """Walk upward from start_path to locate .parallel-lock.json."""
    curr = start_path.resolve()
    if curr.is_file():
        curr = curr.parent

    for directory in (curr, *curr.parents):
        lock_file = directory / ".parallel-lock.json"
        if lock_file.is_file():
            try:
                raw = json.loads(lock_file.read_text(encoding="utf-8"))
                return directory, LockManifest.from_dict(raw)
            except Exception:
                return None
    return None


def _matches_any_glob(rel_posix: str, globs: list[str]) -> bool:
    for pattern in globs:
        norm_pat = pattern.replace("\\", "/")
        if fnmatch.fnmatch(rel_posix, norm_pat):
            return True
        if norm_pat.endswith("/**") and rel_posix.startswith(norm_pat[:-2]):
            return True
    return False


def _evaluate_command_access(cmd_str: str, base_dir: Path) -> HookDecision:
    """Evaluate whether a shell command attempts to modify protected or out-of-scope files."""
    found = find_lock_manifest(base_dir)
    if found is None:
        return HookDecision(decision="allow")

    worktree_root, lock = found
    norm_cmd = cmd_str.replace("\\", "/")

    # Check 1: Forbidden files referenced with write indicators
    has_write_indicator = any(ind in cmd_str for ind in _CMD_WRITE_INDICATORS)

    for forbid_path, reason in lock.forbidden_files.items():
        norm_forbid = forbid_path.replace("\\", "/")
        fname = Path(norm_forbid).name
        # Match full relative path or explicit file token
        in_cmd = (
            norm_forbid in norm_cmd
            or f"/{fname}" in norm_cmd
            or f"'{fname}'" in norm_cmd
            or f'"{fname}"' in norm_cmd
            or norm_cmd.endswith(f" {fname}")
        )
        if in_cmd and has_write_indicator:
            return HookDecision(
                decision="deny",
                reason=(
                    f"BLAST RADIUS VIOLATION (Terminal Guard): Command attempts to modify "
                    f"forbidden file '{norm_forbid}' (locked: {reason})."
                ),
            )

    # Check 2: Redirections (e.g. echo 'foo' > path or >> path)
    redirect_matches = re.findall(r"(?:>>|>)\s*([^\s;&|\'\"]+)", cmd_str)
    for raw_target in redirect_matches:
        cleaned_target = raw_target.strip().strip("'\"")
        if not cleaned_target:
            continue
        if cleaned_target in {"$null", "/dev/null", "NUL", "nul"} or cleaned_target.startswith("&"):
            continue

        target_path = Path(cleaned_target)
        if not target_path.is_absolute():
            target_path = (base_dir / target_path).resolve()
        else:
            target_path = target_path.resolve()

        try:
            rel_posix = target_path.relative_to(worktree_root).as_posix()
        except ValueError:
            return HookDecision(
                decision="deny",
                reason=(
                    f"BLAST RADIUS VIOLATION (Terminal Guard): Target '{target_path.as_posix()}' "
                    f"is outside assigned worktree root '{worktree_root.as_posix()}'."
                ),
            )

        if rel_posix in lock.forbidden_files:
            return HookDecision(
                decision="deny",
                reason=(
                    f"BLAST RADIUS VIOLATION (Terminal Guard): Target '{rel_posix}' is forbidden "
                    f"({lock.forbidden_files[rel_posix]})."
                ),
            )

        if lock.allowed_write_globs and not _matches_any_glob(rel_posix, lock.allowed_write_globs):
            return HookDecision(
                decision="deny",
                reason=(
                    f"BLAST RADIUS VIOLATION (Terminal Guard): Target '{rel_posix}' is outside "
                    "declared Allowed Write Scope."
                ),
            )

    return HookDecision(decision="allow")


def evaluate_tool_access(
    tool_name: str,
    tool_input: Mapping[str, Any],
    cwd: Path | None = None,
) -> HookDecision:
    """Evaluate whether a tool invocation violates the active worktree's blast-radius lock."""
    base_dir = (cwd or Path.cwd()).resolve()

    if tool_name in _EXEC_TOOLS:
        cmd_str: str | None = None
        for key in _COMMAND_KEYS:
            val = tool_input.get(key)
            if isinstance(val, str) and val.strip():
                cmd_str = val.strip()
                break
        if not cmd_str:
            return HookDecision(decision="allow")
        return _evaluate_command_access(cmd_str, base_dir)

    if tool_name not in _WRITE_TOOLS:
        return HookDecision(decision="allow")

    raw_target: str | None = None
    for key in _TARGET_PATH_KEYS:
        val = tool_input.get(key)
        if isinstance(val, str) and val.strip():
            raw_target = val.strip()
            break

    if not raw_target:
        return HookDecision(decision="allow")

    target_path = Path(raw_target)
    if not target_path.is_absolute():
        target_path = (base_dir / target_path).resolve()
    else:
        target_path = target_path.resolve()

    found = find_lock_manifest(target_path) or find_lock_manifest(base_dir)
    if found is None:
        return HookDecision(decision="allow")

    worktree_root, lock = found
    try:
        rel_posix = target_path.relative_to(worktree_root).as_posix()
    except ValueError:
        return HookDecision(
            decision="deny",
            reason=(
                f"BLAST RADIUS VIOLATION: Target '{target_path.as_posix()}' is outside "
                f"assigned worktree root '{worktree_root.as_posix()}' "
                f"(Wave {lock.wave_id}, Track {lock.track_id})."
            ),
        )

    # 1. Explicit forbidden check
    if rel_posix in lock.forbidden_files:
        lock_reason = lock.forbidden_files[rel_posix]
        return HookDecision(
            decision="deny",
            reason=(
                f"BLAST RADIUS VIOLATION: File '{rel_posix}' cannot be modified by "
                f"Wave {lock.wave_id} / {lock.track_id} (stories {lock.stories}). "
                f"Reason: {lock_reason}."
            ),
        )

    # 2. Allowed glob check
    if lock.allowed_write_globs and not _matches_any_glob(rel_posix, lock.allowed_write_globs):
        return HookDecision(
            decision="deny",
            reason=(
                f"BLAST RADIUS VIOLATION: File '{rel_posix}' is outside the declared "
                f"Allowed Write Scope of Wave {lock.wave_id} / {lock.track_id}. "
                "Update .parallel-lock.json only after verifying zero collision with siblings."
            ),
        )

    if rel_posix in lock.additive_shared_files:
        return HookDecision(
            decision="allow",
            reason=(
                f"ADDITIVE REGISTRY MODE: '{rel_posix}' is shared across tracks in Wave "
                f"{lock.wave_id}. Restrict edits to append-only registrations or declared "
                "isolated function scope."
            ),
        )

    return HookDecision(decision="allow")


def run_stdin_hook() -> int:
    """Entry point for PreToolUse stdin JSON hook protocol."""
    try:
        raw_stdin = sys.stdin.read().strip()
        if not raw_stdin:
            print(json.dumps({"decision": "allow"}))
            return 0
        payload = json.loads(raw_stdin)
        tool_call = payload.get("toolCall") if isinstance(payload.get("toolCall"), dict) else {}
        tool_name = str(
            tool_call.get("name") or payload.get("tool_name") or payload.get("tool") or ""
        )
        tool_input = (
            tool_call.get("args") or payload.get("tool_input") or payload.get("arguments") or {}
        )
        if not isinstance(tool_input, dict):
            tool_input = {}

        cwd_path: Path | None = None
        if "cwd" in payload and payload["cwd"]:
            cwd_path = Path(str(payload["cwd"]))
        elif (
            "workspacePaths" in payload
            and isinstance(payload["workspacePaths"], list)
            and payload["workspacePaths"]
        ):
            cwd_path = Path(str(payload["workspacePaths"][0]))

        decision = evaluate_tool_access(
            tool_name=tool_name,
            tool_input=tool_input,
            cwd=cwd_path,
        )
        print(json.dumps(decision.to_dict(), ensure_ascii=False))
        return 0
    except Exception:
        # Fail-open on malformed stdin so unrelated tools are never bricked
        print(json.dumps({"decision": "allow"}))
        return 0


if __name__ == "__main__":
    raise SystemExit(run_stdin_hook())
