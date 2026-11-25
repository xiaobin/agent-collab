#!/usr/bin/env python3
"""Stage-one bot CLI: validate, approve, run once, status, stop, resume."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bot_runtime import (
    LockedError,
    StateBlockedError,
    append_lifecycle,
    approved_path,
    bot_dir,
    load_state,
    now_iso,
    read_bytes,
    read_json,
    resume_bot,
    run_once,
    stop_bot,
    write_json,
)
from bot_schema import definition_digest, validate_definition

EXIT_OK = 0
EXIT_INVALID = 2
EXIT_APPROVAL = 3
EXIT_LOCKED = 4
EXIT_RUNTIME = 5
EXIT_AUTH = 6
BOT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _parse_time(value: Any, field: str, *, allow_none: bool = True) -> datetime | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} is not an ISO-8601 timestamp") from exc
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc)


def _print_errors(result: Any) -> int:
    for warning in result.warnings:
        print(f"WARNING: {warning}")
    for error in result.errors:
        print(f"ERROR: {error}", file=sys.stderr)
    return EXIT_INVALID


def _load(definition: Path, project_root: Path):
    return validate_definition(definition.resolve(), project_root.resolve())


def _file_sha256(project_root: Path, path: Path) -> str:
    return hashlib.sha256(read_bytes(project_root, path)).hexdigest()


def _check_bot_id(value: str) -> str | None:
    if not BOT_ID_PATTERN.match(value):
        return "bot-id must use lowercase letters, digits, and hyphens, and start with a letter or digit"
    return None


def cmd_validate(args: argparse.Namespace) -> int:
    result = _load(args.definition, args.project_root)
    for warning in result.warnings:
        print(f"WARNING: {warning}")
    if not result.ok:
        for error in result.errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return EXIT_INVALID
    definition = result.definition or {}
    print(f"OK: {definition.get('bot_id')} revision {definition.get('revision')} adapter={definition.get('adapter', {}).get('name')}")
    return EXIT_OK


def cmd_explain(args: argparse.Namespace) -> int:
    result = _load(args.definition, args.project_root)
    if not result.ok:
        return _print_errors(result)
    definition = result.definition or {}
    runtime = definition.get("runtime", {})
    permissions = definition.get("permissions", {})
    print(f"Bot: {definition.get('display_name')} ({definition.get('bot_id')})")
    print(f"Goal: {definition.get('goal_ref')}")
    print(f"Revision: {definition.get('revision')}")
    print(f"Adapter: {definition.get('adapter', {}).get('name')}")
    print(f"Runtime: {runtime.get('kind')} concurrency={runtime.get('concurrency')}")
    print(f"Autonomy ceiling: {permissions.get('autonomy_ceiling')}")
    print(f"Workspace: {definition.get('workspace', {}).get('root')}")
    print("Outputs: " + ", ".join(str(output.get("type")) for output in definition.get("outputs", [])))
    print("Stage-one note: run supports only --once and never calls a model.")
    return EXIT_OK


def _other_bot_count(project_root: Path, bot_id: str) -> int:
    bots_root = project_root / ".herdr" / "bots"
    if not bots_root.is_dir():
        return 0
    return sum(
        1
        for child in bots_root.iterdir()
        if child.name != bot_id and child.is_dir() and (child / "definition.json").is_file()
    )


def cmd_approve(args: argparse.Namespace) -> int:
    result = _load(args.definition, args.project_root)
    if not result.ok:
        return _print_errors(result)
    if result.policy is None:
        print("ERROR: approval requires .herdr/bot-policy.json; structural validation alone is not approval", file=sys.stderr)
        return EXIT_APPROVAL
    if not args.approval_id.strip() or not args.approver_ref.strip():
        print("ERROR: --approval-id and --approver-ref are required", file=sys.stderr)
        return EXIT_INVALID
    try:
        expires_at = _parse_time(args.expires_at, "--expires-at")
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_INVALID
    if expires_at is not None and expires_at <= datetime.now(timezone.utc):
        print("ERROR: --expires-at is already in the past", file=sys.stderr)
        return EXIT_INVALID

    definition = result.definition or {}
    policy = result.policy or {}
    project_root = args.project_root.resolve()
    bot_id = definition["bot_id"]
    revision = definition["revision"]
    max_bots = policy.get("max_bots")
    if isinstance(max_bots, int) and _other_bot_count(project_root, bot_id) + 1 > max_bots:
        print(f"ERROR: policy max_bots={max_bots} would be exceeded", file=sys.stderr)
        return EXIT_INVALID

    target = approved_path(project_root, bot_id, revision)
    try:
        policy_sha256 = _file_sha256(project_root, project_root / ".herdr" / "bot-policy.json")
        adapter_config_sha256 = _file_sha256(project_root, (project_root / definition["adapter"]["config_ref"]).resolve())
    except (OSError, RuntimeError) as exc:
        print(f"ERROR: cannot read approval inputs: {exc}", file=sys.stderr)
        return EXIT_INVALID
    snapshot = {
        "schema_version": 1,
        "approval": {
            "approval_id": args.approval_id,
            "approver_ref": args.approver_ref,
            "source_ref": args.source_ref,
            "approved_at": now_iso(),
            "expires_at": expires_at.isoformat() if expires_at else None,
            "bot_id": bot_id,
            "revision": revision,
            "project_root": str(project_root),
            "run_user": getpass.getuser(),
            "scope": {
                "autonomy_ceiling": definition["permissions"]["autonomy_ceiling"],
                "triggers": [trigger.get("type") for trigger in definition.get("triggers", []) if isinstance(trigger, dict)],
                "outputs": [output["type"] for output in definition["outputs"]],
                "external_actions": definition["permissions"]["external_actions"],
            },
        },
        "definition_sha256": definition_digest(definition),
        "policy_sha256": policy_sha256,
        "adapter_config_sha256": adapter_config_sha256,
        "definition": definition,
    }
    try:
        write_json(project_root, target, snapshot, exclusive=True)
        append_lifecycle(project_root, bot_id, "approved", approval_id=args.approval_id, approver_ref=args.approver_ref, revision=revision, definition_sha256=snapshot["definition_sha256"])
    except FileExistsError:
        print(f"ERROR: {bot_id} revision {revision} is already approved; increase revision for behavior changes", file=sys.stderr)
        return EXIT_INVALID
    except (OSError, RuntimeError) as exc:
        print(f"ERROR: cannot write approval snapshot: {exc}", file=sys.stderr)
        return EXIT_RUNTIME
    print(json.dumps({"approval_id": args.approval_id, "bot_id": bot_id, "revision": revision, "snapshot": str(target)}, ensure_ascii=False, sort_keys=True))
    return EXIT_OK


def _approval_error(snapshot: Any, project_root: Path, bot_id: str, revision: int) -> str | None:
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("definition"), dict):
        return "approval snapshot is malformed"
    approval = snapshot.get("approval")
    if not isinstance(approval, dict):
        return "approval metadata is missing or not an object"
    for field in ("approval_id", "approver_ref", "approved_at", "bot_id", "project_root", "run_user"):
        if not isinstance(approval.get(field), str) or not approval[field].strip():
            return f"approval metadata field {field} is required"
    if approval.get("bot_id") != bot_id or approval.get("revision") != revision:
        return "approval identity does not match the requested bot revision"
    if approval.get("project_root") != str(project_root):
        return "approval belongs to a different project root"
    if approval.get("run_user") != getpass.getuser():
        return "approval belongs to a different run user"
    if not isinstance(approval.get("scope"), dict):
        return "approval scope is required"
    try:
        _parse_time(approval.get("approved_at"), "approved_at", allow_none=False)
        expires_at = _parse_time(approval.get("expires_at"), "expires_at")
    except ValueError as exc:
        return str(exc)
    if expires_at is not None and expires_at <= datetime.now(timezone.utc):
        return "approval has expired; re-approve the current revision"
    if definition_digest(snapshot["definition"]) != snapshot.get("definition_sha256"):
        return "approval snapshot digest is internally inconsistent"
    return None


def cmd_run(args: argparse.Namespace) -> int:
    if not args.once:
        print("ERROR: resident mode is not implemented in stage one; use --once", file=sys.stderr)
        return EXIT_INVALID
    problem = _check_bot_id(args.bot_id)
    if problem:
        print(f"ERROR: {problem}", file=sys.stderr)
        return EXIT_INVALID
    project_root = args.project_root.resolve()
    definition_path = bot_dir(project_root, args.bot_id) / "definition.json"
    result = _load(definition_path, project_root)
    if not result.ok:
        return _print_errors(result)
    if result.policy is None:
        print("ERROR: run requires .herdr/bot-policy.json", file=sys.stderr)
        return EXIT_APPROVAL
    definition = result.definition or {}
    if definition.get("bot_id") != args.bot_id:
        print("ERROR: definition bot_id does not match --bot-id", file=sys.stderr)
        return EXIT_INVALID
    if definition.get("runtime", {}).get("kind") != "process":
        print("ERROR: agent runtime not implemented in stage one; only runtime.kind=process is allowed", file=sys.stderr)
        return EXIT_INVALID

    revision = definition["revision"]
    snapshot_path = approved_path(project_root, args.bot_id, revision)
    try:
        snapshot = read_json(project_root, snapshot_path)
    except FileNotFoundError:
        print(f"ERROR: approval required for {args.bot_id} revision {revision}; run `bot.py approve` first", file=sys.stderr)
        return EXIT_APPROVAL
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: cannot read approval snapshot: {exc}", file=sys.stderr)
        return EXIT_APPROVAL
    error = _approval_error(snapshot, project_root, args.bot_id, revision)
    if error:
        print(f"ERROR: {error}", file=sys.stderr)
        return EXIT_APPROVAL
    if snapshot.get("definition_sha256") != definition_digest(definition) or snapshot.get("definition") != definition:
        print("ERROR: definition does not match the approved snapshot; re-approve a new revision", file=sys.stderr)
        return EXIT_APPROVAL
    try:
        if snapshot.get("policy_sha256") != _file_sha256(project_root, project_root / ".herdr" / "bot-policy.json"):
            print("ERROR: project policy changed since approval; re-approve the bot", file=sys.stderr)
            return EXIT_APPROVAL
        if snapshot.get("adapter_config_sha256") != _file_sha256(project_root, (project_root / definition["adapter"]["config_ref"]).resolve()):
            print("ERROR: adapter config changed since approval; re-approve the bot", file=sys.stderr)
            return EXIT_APPROVAL
    except (OSError, RuntimeError) as exc:
        print(f"ERROR: cannot verify approval inputs: {exc}", file=sys.stderr)
        return EXIT_APPROVAL

    try:
        payload, code = run_once(project_root, definition, result.policy)
    except LockedError as exc:
        print(f"ERROR: bot is locked: {exc}", file=sys.stderr)
        return EXIT_LOCKED
    except StateBlockedError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_APPROVAL
    except (OSError, RuntimeError) as exc:
        print(f"ERROR: runtime failure: {exc}", file=sys.stderr)
        return EXIT_RUNTIME
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    if code != EXIT_OK:
        print(f"ERROR: {payload.get('message', payload.get('error'))}", file=sys.stderr)
    return code


def cmd_status(args: argparse.Namespace) -> int:
    problem = _check_bot_id(args.bot_id)
    if problem:
        print(f"ERROR: {problem}", file=sys.stderr)
        return EXIT_INVALID
    print(json.dumps(load_state(args.project_root.resolve(), args.bot_id), ensure_ascii=False, sort_keys=True))
    return EXIT_OK


def cmd_stop(args: argparse.Namespace) -> int:
    problem = _check_bot_id(args.bot_id)
    if problem:
        print(f"ERROR: {problem}", file=sys.stderr)
        return EXIT_INVALID
    try:
        state = stop_bot(args.project_root.resolve(), args.bot_id, args.reason)
    except LockedError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_LOCKED
    except (OSError, RuntimeError) as exc:
        print(f"ERROR: cannot stop bot: {exc}", file=sys.stderr)
        return EXIT_RUNTIME
    print(json.dumps(state, ensure_ascii=False, sort_keys=True))
    return EXIT_OK


def cmd_resume(args: argparse.Namespace) -> int:
    problem = _check_bot_id(args.bot_id)
    if problem:
        print(f"ERROR: {problem}", file=sys.stderr)
        return EXIT_INVALID
    try:
        state = resume_bot(args.project_root.resolve(), args.bot_id)
    except LockedError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_LOCKED
    except StateBlockedError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_APPROVAL
    except (OSError, RuntimeError) as exc:
        print(f"ERROR: cannot resume bot: {exc}", file=sys.stderr)
        return EXIT_RUNTIME
    print(json.dumps(state, ensure_ascii=False, sort_keys=True))
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Stage-one bot definition, approval, and single-pass runtime.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate", help="validate a bot definition")
    validate.add_argument("--definition", required=True, type=Path)
    validate.add_argument("--project-root", default=Path("."), type=Path)
    validate.set_defaults(func=cmd_validate)
    explain = subparsers.add_parser("explain", help="print a human summary")
    explain.add_argument("--definition", required=True, type=Path)
    explain.add_argument("--project-root", default=Path("."), type=Path)
    explain.set_defaults(func=cmd_explain)
    approve = subparsers.add_parser("approve", help="write an approval snapshot")
    approve.add_argument("--definition", required=True, type=Path)
    approve.add_argument("--project-root", default=Path("."), type=Path)
    approve.add_argument("--approval-id", required=True)
    approve.add_argument("--approver-ref", required=True)
    approve.add_argument("--source-ref", default=None)
    approve.add_argument("--expires-at", default=None)
    approve.set_defaults(func=cmd_approve)
    run = subparsers.add_parser("run", help="run one polling pass")
    run.add_argument("--project-root", default=Path("."), type=Path)
    run.add_argument("--bot-id", required=True)
    run.add_argument("--once", action="store_true")
    run.set_defaults(func=cmd_run)
    status = subparsers.add_parser("status", help="print local bot state")
    status.add_argument("--project-root", default=Path("."), type=Path)
    status.add_argument("--bot-id", required=True)
    status.set_defaults(func=cmd_status)
    stop = subparsers.add_parser("stop", help="mark a bot stopped")
    stop.add_argument("--project-root", default=Path("."), type=Path)
    stop.add_argument("--bot-id", required=True)
    stop.add_argument("--reason", default=None)
    stop.set_defaults(func=cmd_stop)
    resume = subparsers.add_parser("resume", help="explicitly resume a stopped bot")
    resume.add_argument("--project-root", default=Path("."), type=Path)
    resume.add_argument("--bot-id", required=True)
    resume.set_defaults(func=cmd_resume)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
