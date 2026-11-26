#!/usr/bin/env python3
"""Deterministic onboarding helper for the bot-onboarding skill.

`plan` probes the read-only meegle CLI and the project's registered owner,
renders the shipped feishu templates and prints (optionally saves) a plan with
per-file SHA-256 digests. `write` applies exactly that plan: it refuses a
tampered plan and never silently overwrites a config that drifted.

The agent runs these commands; the human only approves and logs in.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from bot_adapters.base import AdapterError
from bot_adapters.feishu_sync import FeishuAdapter, _config as feishu_config

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
OWNER_FILE = ".herdr/bot-owner.json"
BOT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
NEEDS_HUMAN = 3
CONFLICT = 4


class OnboardingError(Exception):
    def __init__(self, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _dump(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _load_owner(project_root: Path) -> str:
    path = project_root / OWNER_FILE
    if not path.is_file():
        raise OnboardingError(
            f"owner_ref is not registered: ask the user which owner identifier to use, then write {OWNER_FILE} "
            'as {"owner_ref": "<user supplied value>"}. Never fall back to git email or whoami.',
            NEEDS_HUMAN,
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise OnboardingError(f"{OWNER_FILE} is not valid JSON: {exc}", 2) from exc
    owner_ref = payload.get("owner_ref") if isinstance(payload, dict) else None
    if not isinstance(owner_ref, str) or not owner_ref.strip():
        raise OnboardingError(
            f"{OWNER_FILE} has an empty owner_ref; ask the user for the value instead of inventing one.",
            NEEDS_HUMAN,
        )
    return owner_ref.strip()


def _probe(meegle_bin: str | None) -> dict[str, Any]:
    """Read-only probes, reusing the adapter so CLI flags stay in one place."""
    adapter = FeishuAdapter()
    raw: dict[str, Any] = {}
    if meegle_bin:
        raw["meegle_bin"] = meegle_bin
    config = feishu_config(raw)
    try:
        auth = adapter._run(config, ["auth", "status"])
        if not auth.get("authenticated"):
            raise OnboardingError(
                f"meegle is not authenticated ({auth.get('reason') or 'no reason given'}); "
                "ask the user to log in interactively. Do not run the login flow for them.",
                NEEDS_HUMAN,
            )
        me = adapter._run(config, ["user", "me"])
        user_key = str(me.get("user_key") or "").strip()
        if not user_key:
            raise OnboardingError("meegle user me returned no user_key; cannot bind an approval identity.", 2)
        todos = adapter._run(config, ["mywork", "todo", "--action", "todo"], allow_list=True)
    except AdapterError as exc:
        code = NEEDS_HUMAN if exc.code == "authentication_expired" else 2
        raise OnboardingError(str(exc), code) from exc
    items = todos.get("items", []) if isinstance(todos, dict) else todos
    project_keys = sorted({str(item.get("project_key")) for item in items if isinstance(item, dict) and item.get("project_key")})
    return {"auth": auth, "user_key": user_key, "project_keys": project_keys}


def _select_project_key(probes: dict[str, Any], requested: str | None) -> str:
    candidates = probes["project_keys"]
    if requested:
        return requested
    if len(candidates) == 1:
        return candidates[0]
    raise OnboardingError(
        "feishu project space is ambiguous; ask the user to choose one of: "
        + (", ".join(candidates) if candidates else "<no todo items returned, pass --project-key explicitly>"),
        2,
    )


def _render_definition(template: str, replacements: dict[str, str], bot_id: str, interval_seconds: int) -> dict[str, Any]:
    text = template
    for token, value in replacements.items():
        text = text.replace(token, value)
    if bot_id != "feishu-sync":
        text = text.replace("feishu-sync", bot_id)
    if "REPLACE-WITH-" in text or "replace-me" in text:
        raise OnboardingError("definition template still contains an unfilled placeholder", 2)
    definition = json.loads(text)
    for trigger in definition["triggers"]:
        if trigger.get("type") == "interval":
            trigger["interval_seconds"] = interval_seconds
    definition["triage"]["batch_interval_seconds"] = interval_seconds
    return definition


def cmd_plan(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    if not project_root.is_dir():
        raise OnboardingError(f"project root does not exist: {project_root}", 2)
    if not BOT_ID_PATTERN.match(args.bot_id):
        raise OnboardingError(f"invalid bot id: {args.bot_id}", 2)
    if not args.goal_ref.strip():
        raise OnboardingError("goal_ref is required; take it from the task card or the user's instruction", 2)
    if args.interval_seconds < 60:
        raise OnboardingError("interval must be >= 60 seconds", 2)

    owner_ref = _load_owner(project_root)
    probes = _probe(args.meegle_bin)
    project_key = _select_project_key(probes, args.project_key)

    definition = _render_definition(
        (PLUGIN_ROOT / "templates" / "bot-definition-feishu.json").read_text(encoding="utf-8"),
        {
            "REPLACE-WITH-GOAL-REF": args.goal_ref,
            "REPLACE-WITH-OWNER-REF": owner_ref,
            "REPLACE-WITH-WORKSPACE-LABEL": project_root.name,
            "REPLACE-WITH-PROJECT-ROOT": str(project_root),
        },
        args.bot_id,
        args.interval_seconds,
    )
    adapter_config = {
        "project_key": project_key,
        "expected_user_key": probes["user_key"],
    }
    policy = json.loads((PLUGIN_ROOT / "templates" / "bot-policy-feishu.json").read_text(encoding="utf-8"))

    files = []
    for relative, payload in (
        (".herdr/bot-policy.json", policy),
        (f".herdr/bots/{args.bot_id}/definition.json", definition),
        (f".herdr/bots/{args.bot_id}/adapter-config.json", adapter_config),
    ):
        content = _dump(payload)
        files.append({"path": relative, "sha256": _sha256(content), "content": content})

    plan = {
        "schema_version": 1,
        "bot_id": args.bot_id,
        "project_root": str(project_root),
        "goal_ref": args.goal_ref,
        "owner_ref": owner_ref,
        "project_key": project_key,
        "expected_user_key": probes["user_key"],
        "interval_seconds": args.interval_seconds,
        "files": files,
        "probes": {"user_key": probes["user_key"], "project_key_candidates": probes["project_keys"]},
        "approve": {"approver_ref": owner_ref, "definition": f".herdr/bots/{args.bot_id}/definition.json"},
        "summary": [
            "数据来源：飞书项目待办、工作项详情与评论；只读（auth status / user me / user search / mywork todo / workitem get / comment list），不做任何飞书写操作",
            f"空间：{project_key}",
            f"当前身份：{probes['user_key']}（来自 meegle user me，批准与运行必须一致）",
            f"频率：每 {args.interval_seconds} 秒一次，另支持手动触发",
            f"写范围：.herdr/bot-policy.json 与 .herdr/bots/{args.bot_id}/**，不碰业务代码",
            f"落盘：definition.json / adapter-config.json / state/events.jsonl / reports/triage/（均在 .herdr/bots/{args.bot_id}/ 下）",
            f"批准：approver_ref={owner_ref}，必须等你的一句明确确认；配置漂移后需重新批准",
        ]
        + [f"sha256 {item['path']}: {item['sha256']}" for item in files],
    }
    rendered = json.dumps(plan, ensure_ascii=False, indent=2)
    if args.output:
        output = Path(args.output)
        if not output.is_absolute():
            output = project_root / output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


def cmd_write(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    plan_path = Path(args.plan_file)
    if not plan_path.is_absolute():
        plan_path = project_root / plan_path
    if not plan_path.is_file():
        raise OnboardingError(f"plan file not found: {plan_path}; run plan first", 2)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("schema_version") != 1 or not isinstance(plan.get("files"), list):
        raise OnboardingError("unrecognized plan schema", 2)
    if Path(plan.get("project_root", "")).resolve() != project_root:
        raise OnboardingError("plan was rendered for a different project root; re-run plan here", CONFLICT)

    written, unchanged = [], []
    for item in plan["files"]:
        content = item["content"]
        if _sha256(content) != item["sha256"]:
            raise OnboardingError(f"plan digest mismatch for {item['path']}: plan was edited; re-run plan", CONFLICT)
        target = (project_root / item["path"]).resolve()
        if project_root not in target.parents:
            raise OnboardingError(f"plan path escapes the project root: {item['path']}", CONFLICT)
        if target.exists():
            if target.read_text(encoding="utf-8") == content:
                unchanged.append(item["path"])
                continue
            raise OnboardingError(
                f"refusing to overwrite {item['path']}: existing content differs from the approved plan; "
                "show the user the diff and re-run plan instead of overwriting",
                CONFLICT,
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(item["path"])
    print(json.dumps({"written": written, "unchanged": unchanged}, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bot_onboarding.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", help="probe and render a plan; writes no definition files")
    plan.add_argument("--project-root", default=".")
    plan.add_argument("--bot-id", default="feishu-sync")
    plan.add_argument("--goal-ref", required=True)
    plan.add_argument("--project-key", default=None)
    plan.add_argument("--interval-seconds", type=int, default=86400)
    plan.add_argument("--meegle-bin", default=os.environ.get("MEEGLE_BIN"))
    plan.add_argument("--output", default=None, help="optional path for the plan JSON")
    plan.set_defaults(handler=cmd_plan)
    write = sub.add_parser("write", help="apply a plan exactly, refusing drift and tampering")
    write.add_argument("--project-root", default=".")
    write.add_argument("--plan-file", required=True)
    write.set_defaults(handler=cmd_write)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except OnboardingError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return exc.exit_code


if __name__ == "__main__":
    raise SystemExit(main())