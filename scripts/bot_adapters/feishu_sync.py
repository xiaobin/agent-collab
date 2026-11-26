"""Read-only Feishu Project adapter backed by the meegle CLI.

Only auth status, user me, user search, mywork todo, comment list and workitem
get are used.  There is no retry loop, login flow or write command here.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
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
    "MEEGLE_RESOLVE_USER_NAMES": "resolve_user_names",
    "MEEGLE_USER_CACHE_PATH": "user_cache_path",
}
_DEFAULTS = {
    "meegle_bin": "meegle",
    "max_pages": 20,
    "comment_max_pages": 20,
    "timeout_seconds": 30,
    "fetch_details": True,
    "resolve_user_names": True,
    "user_cache_path": "state/user-cache.json",
    "user_cache_ttl_seconds": 86400,
    "user_cache_miss_ttl_seconds": 300,
}
_AUTH_MARKERS = ("401", "token expired", "unauthorized", "not logged in", "authentication expired")
_NETWORK_MARKERS = (
    "server unreachable",
    "timed out",
    "timeout",
    "network",
    "connection",
    "connect",
    "econnrefused",
    "econnreset",
    "enotfound",
    "dns",
    "socket",
    "tls",
)
_MENTION_PATTERN = re.compile(r"@[^\s@]+")
_USER_BATCH = 20


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _classify_auth_failure(reason: Any) -> str:
    text = str(reason or "").lower()
    if any(marker in text for marker in _AUTH_MARKERS):
        return "authentication_expired"
    if any(marker in text for marker in _NETWORK_MARKERS):
        return "upstream_error"
    return "unknown_auth_state"


def _config(config: dict[str, Any]) -> dict[str, Any]:
    merged = dict(_DEFAULTS)
    merged.update(config)
    for env_name, key in _ENV_KEYS.items():
        value = os.environ.get(env_name)
        if value is not None and value != "":
            merged[key] = value
    for key in ("max_pages", "comment_max_pages", "timeout_seconds", "user_cache_ttl_seconds", "user_cache_miss_ttl_seconds"):
        try:
            merged[key] = int(merged[key])
        except (TypeError, ValueError) as exc:
            raise AdapterError("invalid_config", f"feishu adapter: {key} must be an integer", exit_code=2) from exc
        if merged[key] < 1:
            raise AdapterError("invalid_config", f"feishu adapter: {key} must be positive", exit_code=2)
    for key in ("fetch_details", "resolve_user_names"):
        merged[key] = _as_bool(merged[key])
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


def _attachment_dedupe_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


class FeishuAdapter(Adapter):
    def _run(self, config: dict[str, Any], args: list[str], *, allow_list: bool = False) -> Any:
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
            kind = _classify_auth_failure(output)
            if kind == "authentication_expired":
                raise AdapterError("authentication_expired", "feishu adapter: meegle is not authenticated or the token expired; re-authenticate manually", exit_code=6)
            detail = (completed.stderr or completed.stdout).strip()[:500]
            raise AdapterError(
                kind if kind == "unknown_auth_state" else "upstream_error",
                f"feishu adapter: meegle {' '.join(args[:2])} failed: {detail}",
                exit_code=5,
            )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise AdapterError("invalid_response", f"feishu adapter: meegle {' '.join(args[:2])} returned invalid JSON", exit_code=5) from exc
        if allow_list and isinstance(payload, list):
            return payload
        if not isinstance(payload, dict):
            raise AdapterError("invalid_response", "feishu adapter: meegle response must be a JSON object", exit_code=5)
        return payload

    def approval_binding(self, config: dict[str, Any]) -> dict[str, Any]:
        cfg = _config(config)
        project_key = str(cfg.get("project_key") or "").strip()
        user_key = str(cfg.get("expected_user_key") or "").strip()
        if not project_key or not user_key:
            raise AdapterError(
                "approval_binding_missing",
                "feishu adapter: project_key and expected_user_key are required for approval; set them in project config or MEEGLE_PROJECT_KEY/MEEGLE_USER_KEY",
                exit_code=2,
            )
        return {"adapter": "feishu", "project_key": project_key, "user_key": user_key}

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

    def _cache_path(self, config: dict[str, Any], workspace_root: Path | None, project_root: Path) -> Path:
        raw = str(config.get("user_cache_path") or "state/user-cache.json")
        base = (workspace_root or project_root).resolve()
        target = (base / raw).resolve()
        if not target.is_relative_to(base):
            raise AdapterError("invalid_config", "feishu adapter: user_cache_path escapes the workspace", exit_code=2)
        return target

    @staticmethod
    def _load_cache(path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _save_cache(path: Path, payload: dict[str, Any]) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if path.is_symlink():
                raise AdapterError("invalid_config", "feishu adapter: user cache must not be a symlink", exit_code=2)
            fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".user-cache.", text=True)
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        except AdapterError:
            raise
        except OSError:
            # The cache only saves lookups; a failed write must not lose events.
            return

    @staticmethod
    def _name_from_row(row: Any) -> tuple[str | None, str | None]:
        if not isinstance(row, dict):
            return None, None
        for field in ("name_cn", "name_en"):
            value = row.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip(), field
        return None, None

    def _resolve_user_names(self, config: dict[str, Any], keys: list[str], workspace_root: Path | None, project_root: Path) -> dict[str, dict[str, Any]]:
        unique = sorted({str(key) for key in keys if str(key).strip()})
        if not unique:
            return {}
        if not config.get("resolve_user_names", True):
            return {key: {"name": None, "status": "disabled", "source": None, "reason": "resolve_user_names=false"} for key in unique}

        cache_path = self._cache_path(config, workspace_root, project_root)
        cache = self._load_cache(cache_path)
        users = cache.get("users") if isinstance(cache.get("users"), dict) else {}
        misses = cache.get("misses") if isinstance(cache.get("misses"), dict) else {}
        now = time.time()
        ttl = config["user_cache_ttl_seconds"]
        miss_ttl = config["user_cache_miss_ttl_seconds"]
        results: dict[str, dict[str, Any]] = {}
        pending: list[str] = []

        for key in unique:
            hit = users.get(key)
            if isinstance(hit, dict) and isinstance(hit.get("resolved_at"), (int, float)) and now - hit["resolved_at"] <= ttl:
                name = hit.get("name")
                if isinstance(name, str) and name.strip():
                    results[key] = {"name": name, "status": "resolved", "source": hit.get("name_source"), "reason": None}
                    continue
            miss = misses.get(key)
            if isinstance(miss, dict) and isinstance(miss.get("at"), (int, float)) and now - miss["at"] <= miss_ttl:
                results[key] = {"name": None, "status": "unknown", "source": None, "reason": miss.get("reason", "cached_miss")}
                continue
            pending.append(key)

        for offset in range(0, len(pending), _USER_BATCH):
            chunk = pending[offset:offset + _USER_BATCH]
            args = ["user", "search", "--project-key", str(config.get("project_key") or "")]
            for key in chunk:
                args.extend(["--user-keys", key])
            try:
                rows = self._run(config, args, allow_list=True)
            except AdapterError as exc:
                if exc.exit_code == 6:
                    raise
                reason = f"lookup_failed:{exc.code}"
                for key in chunk:
                    results[key] = {"name": None, "status": "unknown", "source": None, "reason": reason}
                    misses[key] = {"at": now, "reason": reason}
                continue
            if not isinstance(rows, list):
                raise AdapterError("invalid_response", "feishu adapter: user search response must be a list", exit_code=5)
            by_key = {str(row.get("user_key")): row for row in rows if isinstance(row, dict) and row.get("user_key")}
            for key in chunk:
                name, source = self._name_from_row(by_key.get(key))
                if name:
                    results[key] = {"name": name, "status": "resolved", "source": source, "reason": None}
                    users[key] = {"name": name, "name_source": source, "status": by_key[key].get("status"), "resolved_at": now}
                    misses.pop(key, None)
                else:
                    reason = "not_found" if key not in by_key else "empty_name"
                    results[key] = {"name": None, "status": "unknown", "source": None, "reason": reason}
                    misses[key] = {"at": now, "reason": reason}

        self._save_cache(cache_path, {"schema_version": 1, "users": users, "misses": misses})
        return results

    def poll(self, *, cursor: Any, config: dict[str, Any], project_root: Path, workspace_root: Path | None = None, read_scope: list[str] | None = None) -> PollResult:
        del read_scope
        cfg = _config(config)
        auth = self._run(cfg, ["auth", "status"])
        if auth.get("authenticated") is not True:
            reason = auth.get("reason") or auth.get("error") or ""
            kind = _classify_auth_failure(reason)
            if kind == "authentication_expired":
                raise AdapterError("authentication_expired", "feishu adapter: meegle is not authenticated; re-authenticate manually", exit_code=6)
            raise AdapterError(
                kind,
                f"feishu adapter: meegle auth status is not usable: {reason or 'no reason given'}",
                exit_code=5,
            )
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
        pending_comments: dict[str, list[dict[str, Any]]] = {}
        pending_previous: dict[str, dict[str, Any]] = {}

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
            pending_comments[key] = comments
            pending_previous[key] = previous
            next_items[key] = {"snapshot_sha256": snapshot_hash, "comment_ids": [], "last_comment_created_at": None}

        creators: list[str] = []
        for comments in pending_comments.values():
            for comment in comments:
                creator = comment.get("creator")
                if creator and str(creator) not in creators:
                    creators.append(str(creator))
        names = self._resolve_user_names(cfg, creators, workspace_root, project_root)

        for key, comments in pending_comments.items():
            previous = pending_previous[key]
            project_key, work_item_id = key.split(":", 1)
            reference = _ref(project_key, work_item_id)
            source = {"adapter": "feishu", **reference}
            seen = set(previous.get("comment_ids", [])) if isinstance(previous.get("comment_ids"), list) else set()
            for comment in comments:
                comment_id = str(comment["comment_id"])
                if comment_id in seen:
                    continue
                content = str(comment.get("content") or "")
                file_url = str(comment.get("file_url") or "")
                creator = str(comment.get("creator") or "")
                identity = names.get(creator, {"name": None, "status": "unknown", "source": None, "reason": "not_queried"})
                events.append({
                    "event_id": f"comment:{project_key}:{work_item_id}:{comment_id}",
                    "kind": "comment.created",
                    "source": source,
                    "payload": {
                        **reference,
                        "comment_id": comment_id,
                        "created_at": comment.get("created_at"),
                        "creator_user_key": comment.get("creator"),
                        "creator_name": identity["name"],
                        "creator_name_status": identity["status"],
                        "creator_name_source": identity["source"],
                        "creator_name_reason": identity["reason"],
                        "content": content,
                        "mentions_text": _MENTION_PATTERN.findall(content),
                        "mentions_are_structured": False,
                        "attachments": [{
                            "name": None,
                            "name_status": "unavailable",
                            "dedupe_key": _attachment_dedupe_key(file_url),
                            "url": file_url,
                        }] if file_url else [],
                    },
                })
                seen.add(comment_id)
            next_items[key] = {
                "snapshot_sha256": next_items[key]["snapshot_sha256"],
                "comment_ids": sorted(seen),
                "last_comment_created_at": max((str(item.get("created_at", "")) for item in comments), default=None),
            }
        return PollResult(events=events, next_cursor={"version": 1, "items": next_items})
