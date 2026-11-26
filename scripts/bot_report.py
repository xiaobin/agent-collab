"""Read-only summary of what a bot has already collected.

Everything here is rendered from local files: ``state/state.json``,
``state/events.jsonl`` and ``state/user-cache.json``.  No adapter or network
call is made unless the caller explicitly asks for one with ``--sync``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EXIT_OK = 0
EXIT_INVALID = 2
EXIT_BLOCKED = 3
EXIT_RUNTIME = 5

DEFAULT_LIMIT = 10
DEFAULT_BOT_ID = "feishu-sync"


def _bot_dir(project_root: Path, bot_id: str) -> Path:
    return project_root / ".herdr" / "bots" / bot_id


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _read_events(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _names(rows: Any) -> list[str]:
    if not isinstance(rows, list):
        return []
    names = []
    for row in rows:
        name = _text(row.get("name")) if isinstance(row, dict) else ""
        if name:
            names.append(name)
    return names


def _owner_label(rows: Any, user_keys: list[str]) -> str:
    names = _names(rows)
    if names:
        return "、".join(dict.fromkeys(names))
    if user_keys:
        return "、".join(f"未知（{key}）" for key in dict.fromkeys(user_keys))
    return "未指派"


def _item_from_event(event: dict[str, Any]) -> dict[str, Any] | None:
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return None
    snapshot = payload.get("snapshot")
    if not isinstance(snapshot, dict):
        return None
    todo = snapshot.get("todo") if isinstance(snapshot.get("todo"), dict) else {}
    detail = snapshot.get("detail") if isinstance(snapshot.get("detail"), dict) else {}
    attribute = detail.get("work_item_attribute") if isinstance(detail.get("work_item_attribute"), dict) else {}
    info = todo.get("work_item_info") if isinstance(todo.get("work_item_info"), dict) else {}
    work_item_id = _text(payload.get("work_item_id") or info.get("work_item_id") or attribute.get("work_item_id"))
    if not work_item_id:
        return None
    project_key = _text(payload.get("project_key") or todo.get("project_key") or attribute.get("owned_project", {}).get("key"))
    title = _text(attribute.get("work_item_name") or info.get("work_item_name")) or f"工作项 {work_item_id}"
    status = ""
    status_row = attribute.get("work_item_status")
    if isinstance(status_row, dict):
        status = _text(status_row.get("name"))
    nodes = detail.get("work_item_current_node")
    node_names: list[str] = []
    owner_rows: list[dict[str, Any]] = []
    if isinstance(nodes, list):
        for node in nodes:
            if not isinstance(node, dict):
                continue
            name = _text(node.get("name"))
            if name and name not in node_names:
                node_names.append(name)
            if isinstance(node.get("owners"), list):
                owner_rows.extend(row for row in node["owners"] if isinstance(row, dict))
    if not node_names:
        node_name = _text((todo.get("node_info") or {}).get("node_name"))
        if node_name:
            node_names.append(node_name)
    space_name = ""
    project_row = attribute.get("owned_project")
    if isinstance(project_row, dict):
        space_name = _text(project_row.get("name"))
    space_name = space_name or _text(todo.get("project_name"))
    return {
        "work_item_id": work_item_id,
        "title": title,
        "status": status,
        "nodes": node_names,
        "owners": _owner_label(owner_rows, [str(row.get("key")) for row in owner_rows if row.get("key")]),
        "comments": 0,
        "space_name": space_name,
        "project_key": project_key,
    }


def summarize(project_root: Path, bot_id: str, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    """Build the summary from local state only (never calls an adapter)."""
    state = _read_json(_bot_dir(project_root, bot_id) / "state" / "state.json")
    events = _read_events(_bot_dir(project_root, bot_id) / "state" / "events.jsonl")

    items: dict[str, dict[str, Any]] = {}
    comment_counts: dict[str, int] = {}
    seen_comments: set[str] = set()
    for event in events:
        kind = _text(event.get("kind"))
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if kind.startswith("work_item."):
            item = _item_from_event(event)
            if item is not None:
                key = f"{item['project_key']}:{item['work_item_id']}"
                comment_total = comment_counts.get(key, 0)
                item["comments"] = comment_total
                items[key] = item
        elif kind == "comment.created":
            comment_id = _text(payload.get("comment_id")) or _text(event.get("event_id"))
            if comment_id in seen_comments:
                continue
            seen_comments.add(comment_id)
            key = f"{_text(payload.get('project_key'))}:{_text(payload.get('work_item_id'))}"
            comment_counts[key] = comment_counts.get(key, 0) + 1

    ordered = list(items.values())
    for item in ordered:
        key = f"{item['project_key']}:{item['work_item_id']}"
        item["comments"] = comment_counts.get(key, item["comments"])

    space_name = next((item["space_name"] for item in ordered if item["space_name"]), "")
    project_key = next((item["project_key"] for item in ordered if item["project_key"]), "")

    state_name = _text((state or {}).get("state"))
    last_error = _text((state or {}).get("last_error"))
    reason = None
    if state is None:
        reason = "uninitialized"
    elif state_name == "approval_invalidated":
        reason = "approval_invalidated"
    elif state_name in {"paused", "stopped"} and "auth" in last_error.lower():
        reason = "auth_expired"
    elif not events:
        reason = "no_events"

    shown = ordered[: max(0, limit)]
    return {
        "bot_id": bot_id,
        "state": state_name or "uninitialized",
        "reason": reason,
        "last_success_at": (state or {}).get("last_success_at"),
        "last_error": last_error or None,
        "space_name": space_name,
        "project_key": project_key,
        "items": shown,
        "total_items": len(ordered),
        "total_comments": sum(comment_counts.values()),
        "hidden": max(0, len(ordered) - len(shown)),
        "limit": limit,
    }


def _reason_line(summary: dict[str, Any]) -> str | None:
    reason = summary["reason"]
    state = summary["state"]
    if reason == "uninitialized":
        return "还没有初始化：找不到本地状态（state/state.json）。先完成 bot-onboarding 的批准与第一次采集。"
    if reason == "no_events":
        return "还没有采集到任何数据。若刚批准，先手动跑一次采集；否则检查上次运行是否失败。"
    if reason == "approval_invalidated" or state == "approval_invalidated":
        return "批准已失效：配置、身份或策略在批准后发生了变化。需要重新出计划并再次确认，不会自动重批。"
    if reason == "auth_expired":
        detail = f"（{summary['last_error']}）" if summary.get("last_error") else ""
        return f"飞书登录已失效{detail}：请在终端完成 meegle 登录，再让 agent 恢复运行。以下只展示上次成功采集到的数据。"
    if state == "stopped":
        return "bot 已停止：需要显式恢复后才会继续采集。以下只展示上次成功采集到的数据。"
    if state == "paused":
        return "bot 已暂停：恢复后才会继续采集。以下只展示上次成功采集到的数据。"
    return None


def render(summary: dict[str, Any]) -> str:
    lines = [f"飞书待办摘要（bot: {summary['bot_id']}）"]
    if summary["space_name"]:
        lines.append(f"空间：{summary['space_name']}")
    elif summary["project_key"]:
        lines.append(f"空间：{summary['project_key']}")
    if summary.get("last_success_at"):
        lines.append(f"最近成功采集：{summary['last_success_at']}")
    reason = _reason_line(summary)
    if reason:
        lines.append("")
        lines.append(reason)
    if not summary["total_items"]:
        return "\n".join(lines)

    lines.append("")
    lines.append(f"未完成待办：{summary['total_items']} 个 · 评论：{summary['total_comments']} 条")
    lines.append("")
    limit = summary["limit"]
    header = f"待办（最多显示 {limit} 条）：" if summary["total_items"] > limit else "待办："
    lines.append(header)
    for index, item in enumerate(summary["items"], start=1):
        parts = [f"{index}. {item['work_item_id']} · {item['title']}"]
        if item["status"]:
            parts.append(f"状态：{item['status']}")
        if item["nodes"]:
            parts.append(f"节点：{' / '.join(item['nodes'])}")
        parts.append(f"节点负责人：{item['owners']}")
        parts.append(f"评论 {item['comments']} 条")
        lines.append(" · ".join(parts))
    if summary["hidden"]:
        lines.append("")
        lines.append(f"其余 {summary['hidden']} 条见报告文件。")
    return "\n".join(lines)


def write_report(project_root: Path, bot_id: str, text: str, *, now: datetime | None = None) -> Path:
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    target_dir = _bot_dir(project_root, bot_id) / "reports"
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"summary-{stamp}.md"
    path.write_text(text + "\n", encoding="utf-8")
    return path


def _run_once(project_root: Path, bot_id: str) -> tuple[int, str]:
    script = Path(__file__).resolve().parent / "bot.py"
    proc = subprocess.run(
        [sys.executable, str(script), "run", "--once", "--project-root", str(project_root), "--bot-id", bot_id],
        capture_output=True,
        text=True,
        timeout=600,
    )
    return proc.returncode, (proc.stdout + proc.stderr)


def cmd_report(args: argparse.Namespace) -> int:
    project_root = args.project_root.resolve()
    bot_id = args.bot_id
    if args.sync:
        code, output = _run_once(project_root, bot_id)
        tail = output.strip().splitlines()[-1] if output.strip() else ""
        if code == EXIT_BLOCKED:
            if "approval" in output.lower():
                print("同步没有执行：批准门禁未通过（exit 3）。重新出计划并请用户确认，不会自动重批。", file=sys.stderr)
            else:
                print(f"同步没有执行（exit 3）：{tail}", file=sys.stderr)
            if tail and "approval" in output.lower():
                print(tail, file=sys.stderr)
            return EXIT_BLOCKED
        if code != EXIT_OK:
            print(f"同步失败（exit {code}）：{tail or '没有输出'}", file=sys.stderr)
            return code

    summary = summarize(project_root, bot_id, args.limit)
    text = render(summary)
    print(text)
    if not args.no_write:
        path = write_report(project_root, bot_id, text)
        print(f"\n报告已写入：{path.relative_to(project_root)}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render a read-only summary of collected bot data.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    report = subparsers.add_parser("report", help="render a local summary")
    report.add_argument("--project-root", type=Path, default=Path("."))
    report.add_argument("--bot-id", default=DEFAULT_BOT_ID)
    report.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    report.add_argument("--sync", action="store_true", help="run one approved polling pass first (exit 3 if not approved)")
    report.add_argument("--no-write", action="store_true", help="print only; do not write a timestamped report file")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.limit < 0:
        print("ERROR: --limit must be zero or positive", file=sys.stderr)
        return EXIT_INVALID
    return cmd_report(args)


if __name__ == "__main__":
    raise SystemExit(main())