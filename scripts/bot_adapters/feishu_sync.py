"""Read-only Feishu Project adapter backed by the meegle CLI.

Only auth status, user me, mywork todo, comment list and workitem get are
used.  There is no retry loop, login flow or write command in this module.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .base import Adapter, AdapterError, PollResult

_ENV_KEYS = {
    "MEEGLE_BIN": "meegle_bin",
    "MEEGLE_PROJECT_KEY": "project_key",
    "MEEGLE_WORK_ITEM_ID": "work_item_id",
    "MEEGLE_USER_KEY": "expected_user_key",
    "MEEGLE_MAX_PAGES": "max_pages",
    "MEEGLE_COMMENT_MAX_PAGES": "comment_max_pages",
    "MEEGLE_TIMEOUT_SECONDS": "timeout_seconds",
    "MEEGLE_FETCH_DETAILS": "fetch_details",
}
_DEFAULTS = {
    "meegle_bin": "meegle",
    "max_pages": 20,
    "comment_max_pages": 20,
    "timeout_seconds": 30,
    "fetch_details": True,
}
_AUTH_MARKERS = ("401", "token expired", "unauthorized", "not logged in", "authentication expired")
_MENTION_PATTERN = re.compile(r"@[^\s@]+")


def _config(config: dict[str, Any]) -> dict[str, Any]:
    merged = dict(_DEFAULTS)
    merged.update(config)
    for env_name, key in _ENV_KEYS.items():
        value = os.environ.get(env_name)
        if value is not None and value != "":
            merged[key] = value
    for key in ("max_pages", "comment_max_pages", "timeout_seconds"):
        try:
            merged[key] = int(merged[key])
        except (TypeError, ValueError) as exc:
            raise AdapterError("invalid_config", f"feishu adapter: {key} must be an integer", exit_code=2) from exc
        if merged[key] < 1:
            raise AdapterError("invalid_config", f"feishu adapter: {key} must be positive", exit_code=2)
    if isinstance(merged["fetch_details"], str):
        merged["fetch_details"] = merged["fetch_details"].strip().lower() in {"1", "true", "yes", "on"}
    if merged.get("work_item_id") is not None and not merged.get("project_key"):
        raise AdapterError("invalid_config", "feishu adapter: project_key is required when work_item_id is set", exit_code=2)
    return merged


def _canonical(value: Any) -> Any:
    """Make API object order deterministic.

    Real workitem responses return role_members, current nodes and owners in a
    different order on consecutive calls.  Those arrays are sets for change
    detection, so sort them recursively before hashing and storing.
    """
    if isinstance(value, dict):
        return {key: _canonical(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        items = [_canonical(item) for item in value]
        return sorted(items, key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    return value


def _digest(value: Any) -> str:
    payload = json.dumps(_canonical(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _ref(project_key: str, work_item_id: Any) -> dict[str, str]:
    return {"project_key": str(project_key), "work_item_id": str(work_item_id)}


class FeishuAdapter(Adapter):
    def _run(self, config: dict[str, Any], args: list[str]) -> dict[str, Any]:
        binary = shutil.which(str(config["meegle_bin"])) or (str(config["meegle_bin"]) if Path(str(config["meegle_bin"])).is_file() else None)
        if not binary:
            raise AdapterError("invalid_config", "feishu adapter: meegle executable not found", exit_code=2)
        command = [binary, *args, "--format", "json"]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=config["timeout_seconds"], check=False)
        except subprocess.TimeoutExpired as exc:
            raise AdapterError("upstream_timeout", f"feishu adapter: meegle {' '.join(args[:2])} timed out", exit_code=5) from exc
        except OSError as exc:
            raise AdapterError("upstream_exec", f"feishu adapter: cannot execute meegle: {exc}", exit_code=5) from exc
        output = f"{completed.stdout}\n{completed.stderr}".lower()
        if completed.returncode != 0:
            if any(marker in output for marker in _AUTH_MARKERS):
                raise AdapterError("authentication_expired", "feishu adapter: meegle is not authenticated or the token expired; re-authenticate manually", exit_code=6)
            detail = (completed.stderr or completed.stdout).strip()[:500]
            raise AdapterError("upstream_error", f"feishu adapter: meegle {' '.join(args[:2])} failed: {detail}", exit_code=5)
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise AdapterError("invalid_response", f"feishu adapter: meegle {' '.join(args[:2])} returned invalid JSON", exit_code=5) from exc
        if not isinstance(payload, dict):
            raise AdapterError("invalid_response", "feishu adapter: meegle response must be a JSON object", exit_code=5)
        return payload

    def _todo_items(self, config: dict[str, Any]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for page in range(1, config["max_pages"] + 1):
            payload = self._run(config, ["mywork", "todo", "--action", "todo", "--page-num", str(page)])
            page_items = payload.get("list", [])
            if not isinstance(page_items, list):
                raise AdapterError("invalid_response", "feishu adapter: mywork todo list must be a list", exit_code=5)
            items.extend(item for item in page_items if isinstance(item, dict))
            total = payload.get("total")
            if not page_items or (isinstance(total, int) and len(items) >= total) or len(page_items) < 50:
                break
        else:
            raise AdapterError("incomplete_pagination", "feishu adapter: mywork todo exceeded max_pages; cursor not advanced", exit_code=5)
        project_key = config.get("project_key")
        if project_key:
            items = [item for item in items if str(item.get("project_key")) == str(project_key)]
        unique: dict[tuple[str, str], dict[str, Any]] = {}
        for item in items:
            info = item.get("work_item_info") if isinstance(item.get("work_item_info"), dict) else {}
            unique[(str(item.get("project_key")), str(info.get("work_item_id")))] = item
        return list(unique.values())

    def _comments(self, config: dict[str, Any], project_key: str, work_item_id: str) -> list[dict[str, Any]]:
        comments: dict[str, dict[str, Any]] = {}
        for page in range(1, config["comment_max_pages"] + 1):
            payload = self._run(config, ["comment", "list", "--project-key", project_key, "--work-item-id", work_item_id, "--page-num", str(page)])
            page_comments = payload.get("comments", [])
            if not isinstance(page_comments, list):
                raise AdapterError("invalid_response", "feishu adapter: comments must be a list", exit_code=5)
            for comment in page_comments:
                if not isinstance(comment, dict) or not str(comment.get("comment_id", "")).strip():
                    raise AdapterError("invalid_response", "feishu adapter: every comment needs a non-empty comment_id", exit_code=5)
                comments[str(comment["comment_id"])] = comment
            pagination = payload.get("pagination") if isinstance(payload.get("pagination"), dict) else {}
            total_pages = pagination.get("total_pages")
            if not page_comments or (isinstance(total_pages, int) and page >= total_pages) or (not isinstance(total_pages, int) and len(page_comments) < 20):
                break
        else:
            raise AdapterError("incomplete_pagination", f"feishu adapter: comments for {project_key}/{work_item_id} exceeded comment_max_pages; cursor not advanced", exit_code=5)
        return sorted(comments.values(), key=lambda item: (str(item.get("created_at", "")), str(item["comment_id"])))

    def _target_item(self, config: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        project_key = str(config["project_key"])
        work_item_id = str(config["work_item_id"])
        detail = self._run(config, ["workitem", "get", "--project-key", project_key, "--work-item-id", work_item_id])
        attribute = detail.get("work_item_attribute") if isinstance(detail.get("work_item_attribute"), dict) else {}
        item = {
            "project_key": project_key,
            "work_item_info": {
                "work_item_id": work_item_id,
                "work_item_name": attribute.get("work_item_name"),
                "work_item_type_key": (attribute.get("work_item_type") or {}).get("key") if isinstance(attribute.get("work_item_type"), dict) else None,
            },
        }
        return item, detail

    def poll(self, *, cursor: Any, config: dict[str, Any], project_root: Path, workspace_root: Path | None = None, read_scope: list[str] | None = None) -> PollResult:
        del project_root, workspace_root, read_scope
        cfg = _config(config)
        auth = self._run(cfg, ["auth", "status"])
        if auth.get("authenticated") is not True:
            raise AdapterError("authentication_expired", "feishu adapter: meegle is not authenticated; re-authenticate manually", exit_code=6)
        expected_user = cfg.get("expected_user_key")
        if expected_user:
            user = self._run(cfg, ["user", "me"])
            if str(user.get("user_key")) != str(expected_user):
                raise AdapterError("identity_mismatch", "feishu adapter: authenticated user does not match expected_user_key", exit_code=6)

        targeted = bool(cfg.get("work_item_id"))
        target_detail: dict[str, Any] = {}
        if targeted:
            target_item, target_detail = self._target_item(cfg)
            items = [target_item]
        else:
            items = self._todo_items(cfg)
        previous_items = cursor.get("items", {}) if isinstance(cursor, dict) and isinstance(cursor.get("items"), dict) else {}
        next_items: dict[str, Any] = {}
        events: list[dict[str, Any]] = []

        def item_key(item: dict[str, Any]) -> tuple[str, str]:
            info = item.get("work_item_info") if isinstance(item.get("work_item_info"), dict) else {}
            return str(item.get("project_key")), str(info.get("work_item_id"))

        items.sort(key=item_key)
        for item in items:
            project_key, work_item_id = item_key(item)
            if not project_key or not work_item_id or work_item_id == "None":
                raise AdapterError("invalid_response", "feishu adapter: todo item lacks project_key/work_item_id", exit_code=5)
            reference = _ref(project_key, work_item_id)
            source = {"adapter": "feishu", **reference}
            key = f"{project_key}:{work_item_id}"
            previous = previous_items.get(key) if isinstance(previous_items.get(key), dict) else {}
            detail = target_detail if targeted else (self._run(cfg, ["workitem", "get", "--project-key", project_key, "--work-item-id", work_item_id]) if cfg["fetch_details"] else {})
            snapshot = _canonical({"todo": item, "detail": detail})
            snapshot_hash = _digest(snapshot)
            if previous.get("snapshot_sha256") != snapshot_hash:
                kind = "work_item.updated" if previous else "work_item.discovered"
                events.append({
                    "event_id": f"workitem:{project_key}:{work_item_id}:{snapshot_hash[:24]}",
                    "kind": kind,
                    "source": source,
                    "payload": {**reference, "snapshot": snapshot, "snapshot_sha256": snapshot_hash},
                })
            comments = self._comments(cfg, project_key, work_item_id)
            seen = set(previous.get("comment_ids", [])) if isinstance(previous.get("comment_ids"), list) else set()
            for comment in comments:
                comment_id = str(comment["comment_id"])
                if comment_id in seen:
                    continue
                content = str(comment.get("content") or "")
                file_url = str(comment.get("file_url") or "")
                events.append({
                    "event_id": f"comment:{project_key}:{work_item_id}:{comment_id}",
                    "kind": "comment.created",
                    "source": source,
                    "payload": {
                        **reference,
                        "comment_id": comment_id,
                        "created_at": comment.get("created_at"),
                        "creator_user_key": comment.get("creator"),
                        "creator_name": None,
                        "content": content,
                        "mentions_text": _MENTION_PATTERN.findall(content),
                        "mentions_are_structured": False,
                        "attachments": [{"name": None, "url": file_url}] if file_url else [],
                    },
                })
                seen.add(comment_id)
            next_items[key] = {
                "snapshot_sha256": snapshot_hash,
                "comment_ids": sorted(seen),
                "last_comment_created_at": max((str(item.get("created_at", "")) for item in comments), default=None),
            }
        return PollResult(events=events, next_cursor={"version": 1, "items": next_items})
