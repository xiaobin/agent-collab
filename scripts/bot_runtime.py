#!/usr/bin/env python3
"""Minimal, single-pass bot runtime with bounded local state writes."""

from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bot_adapters import AdapterError, get_adapter

EXIT_RUNTIME = 5


class LockedError(RuntimeError):
    pass


class StateBlockedError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def bot_dir(project_root: Path, bot_id: str) -> Path:
    return project_root / ".herdr" / "bots" / bot_id


def state_dir(project_root: Path, bot_id: str) -> Path:
    return bot_dir(project_root, bot_id) / "state"


def state_path(project_root: Path, bot_id: str) -> Path:
    return state_dir(project_root, bot_id) / "state.json"


def health_path(project_root: Path, bot_id: str) -> Path:
    return state_dir(project_root, bot_id) / "health.json"


def events_path(project_root: Path, bot_id: str) -> Path:
    return state_dir(project_root, bot_id) / "events.jsonl"


def lifecycle_path(project_root: Path, bot_id: str) -> Path:
    return bot_dir(project_root, bot_id).parent / "lifecycle.jsonl"


def approved_path(project_root: Path, bot_id: str, revision: int) -> Path:
    return project_root / ".herdr" / "bots" / "approved" / bot_id / f"{revision}.json"


def _relative_path(project_root: Path, path: Path) -> Path:
    """Return a lexical path below root without following the final target."""
    root = project_root.resolve()
    absolute = Path(os.path.abspath(path))
    try:
        relative = absolute.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(f"path escapes project root: {path}") from exc
    if not relative.parts or ".." in relative.parts:
        raise RuntimeError(f"unsafe relative path: {path}")
    return relative


def _open_parent(project_root: Path, path: Path, *, create: bool) -> tuple[int, str]:
    """Open every parent with O_NOFOLLOW; return a dirfd and final basename."""
    relative = _relative_path(project_root, path)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(project_root.resolve(), flags)
    try:
        for component in relative.parts[:-1]:
            if create:
                try:
                    os.mkdir(component, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            next_fd = os.open(component, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd, relative.name
    except BaseException:
        os.close(fd)
        raise


def _reject_symlink(parent_fd: int, name: str) -> None:
    try:
        info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode):
        raise RuntimeError(f"refusing symlink target: {name}")


def read_bytes(project_root: Path, path: Path) -> bytes:
    parent_fd, name = _open_parent(project_root, path, create=False)
    try:
        _reject_symlink(parent_fd, name)
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
        with os.fdopen(fd, "rb") as handle:
            return handle.read()
    finally:
        os.close(parent_fd)


def read_json(project_root: Path, path: Path) -> Any:
    return json.loads(read_bytes(project_root, path).decode("utf-8"))


def _write_temporary(parent_fd: int, name: str, data: bytes) -> str:
    temporary = f".{name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    fd = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=parent_fd,
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            os.unlink(temporary, dir_fd=parent_fd)
        except FileNotFoundError:
            pass
        raise
    return temporary


def write_json(
    project_root: Path,
    path: Path,
    payload: dict[str, Any],
    *,
    exclusive: bool = False,
) -> None:
    """Atomically write JSON, optionally refusing to replace an existing file."""
    data = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    parent_fd, name = _open_parent(project_root, path, create=True)
    temporary: str | None = None
    try:
        _reject_symlink(parent_fd, name)
        temporary = _write_temporary(parent_fd, name, data)
        if exclusive:
            # link() is an atomic no-replace commit, unlike exists()+replace().
            os.link(
                temporary,
                name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
                follow_symlinks=False,
            )
            os.unlink(temporary, dir_fd=parent_fd)
            temporary = None
        else:
            os.replace(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            temporary = None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
        os.close(parent_fd)


def append_jsonl(project_root: Path, path: Path, payload: dict[str, Any]) -> None:
    data = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    parent_fd, name = _open_parent(project_root, path, create=True)
    try:
        _reject_symlink(parent_fd, name)
        fd = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent_fd,
        )
        with os.fdopen(fd, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(parent_fd)


def append_lifecycle(project_root: Path, bot_id: str, event: str, **fields: Any) -> None:
    append_jsonl(
        project_root,
        lifecycle_path(project_root, bot_id),
        {"schema_version": 1, "bot_id": bot_id, "event": event, "at": now_iso(), **fields},
    )


def _default_state(bot_id: str, state: str = "uninitialized", error: str | None = None) -> dict[str, Any]:
    return {"schema_version": 1, "bot_id": bot_id, "state": state, "cursor": None, "last_error": error}


def load_state(project_root: Path, bot_id: str) -> dict[str, Any]:
    try:
        value = read_json(project_root, state_path(project_root, bot_id))
    except FileNotFoundError:
        return _default_state(bot_id)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
        return _default_state(bot_id, "degraded", "unreadable_state")
    if not isinstance(value, dict):
        return _default_state(bot_id, "degraded", "unreadable_state")
    return value


def load_health(project_root: Path, bot_id: str) -> dict[str, Any]:
    try:
        value = read_json(project_root, health_path(project_root, bot_id))
    except (FileNotFoundError, OSError, RuntimeError, ValueError, json.JSONDecodeError):
        return {"consecutive_failures": 0}
    return value if isinstance(value, dict) else {"consecutive_failures": 0}


class FileLock:
    """Non-blocking exclusive process lock using a no-follow local file."""

    def __init__(self, project_root: Path, path: Path) -> None:
        self.project_root = project_root
        self.path = path
        self.handle = None

    def __enter__(self) -> "FileLock":
        parent_fd, name = _open_parent(self.project_root, self.path, create=True)
        try:
            _reject_symlink(parent_fd, name)
            fd = os.open(
                name,
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent_fd,
            )
            self.handle = os.fdopen(fd, "a+")
        finally:
            os.close(parent_fd)
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.handle.close()
            self.handle = None
            raise LockedError(f"bot lock is held: {self.path}") from exc
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.handle is not None:
            try:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            finally:
                self.handle.close()
                self.handle = None


@contextmanager
def _quota_slot(project_root: Path, limit: Any):
    """Hold one project-wide active-run slot for the duration of a run."""
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise StateBlockedError(f"policy max_active_bots={limit!r} blocks every run")
    bots_root = project_root / ".herdr" / "bots"
    for index in range(limit):
        lock = FileLock(project_root, bots_root / f".quota-{index}.lock")
        try:
            lock.__enter__()
        except LockedError:
            continue
        try:
            yield
        finally:
            lock.__exit__(None, None, None)
        return
    raise StateBlockedError(f"policy max_active_bots={limit} reached")


def _valid_event_id(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 256


def _existing_event_ids(project_root: Path, path: Path) -> set[str]:
    try:
        raw = read_bytes(project_root, path).decode("utf-8")
    except FileNotFoundError:
        return set()
    ids: set[str] = set()
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"events.jsonl contains invalid JSON: {exc}") from exc
        if not isinstance(event, dict) or not _valid_event_id(event.get("event_id")):
            raise RuntimeError("events.jsonl contains an invalid event record")
        ids.add(event["event_id"])
    return ids


def _check_run_policy(project_root: Path, definition: dict[str, Any], policy: dict[str, Any], state: dict[str, Any]) -> None:
    bot_id = definition["bot_id"]
    max_active = policy.get("max_active_bots")
    if isinstance(max_active, int):
        active = 0
        bots_root = project_root / ".herdr" / "bots"
        if bots_root.is_dir():
            for child in bots_root.iterdir():
                if child.name == bot_id or not child.is_dir():
                    continue
                other = load_state(project_root, child.name)
                if other.get("state") in {"running", "starting", "paused", "degraded"}:
                    active += 1
        if active >= max_active:
            raise StateBlockedError(f"policy max_active_bots={max_active} reached")

    trigger_types = {
        trigger.get("type")
        for trigger in definition.get("triggers", [])
        if isinstance(trigger, dict)
    }
    if "interval" in trigger_types:
        minimum = policy.get("min_interval_seconds", 0)
        last_run = state.get("last_run_at")
        if isinstance(last_run, str) and isinstance(minimum, int) and minimum > 0:
            try:
                previous = datetime.fromisoformat(last_run.replace("Z", "+00:00"))
                elapsed = (datetime.now(timezone.utc) - previous).total_seconds()
                if elapsed < minimum:
                    raise StateBlockedError(f"minimum interval {minimum}s has not elapsed")
            except (ValueError, TypeError):
                pass


def _fail(project_root: Path, definition: dict[str, Any], state: dict[str, Any], error: AdapterError) -> tuple[dict[str, Any], int]:
    bot_id = definition["bot_id"]
    previous_health = load_health(project_root, bot_id)
    try:
        failures = int(previous_health.get("consecutive_failures", 0) or 0) + 1
    except (TypeError, ValueError):
        failures = 1
    updated = dict(state)
    updated.update({"schema_version": 1, "bot_id": bot_id, "state": "degraded", "last_error": error.code, "last_run_at": now_iso()})
    write_json(project_root, state_path(project_root, bot_id), updated)
    write_json(
        project_root,
        health_path(project_root, bot_id),
        {
            "schema_version": 1,
            "bot_id": bot_id,
            "state": "degraded",
            "last_run_at": updated["last_run_at"],
            "last_error": error.code,
            "consecutive_failures": failures,
            "approval_revision": definition.get("revision"),
            "adapter_auth_status": "unauthorized" if error.exit_code == 6 else "unknown",
        },
    )
    append_lifecycle(project_root, bot_id, "run_failed", error=error.code, exit_code=error.exit_code, agent_calls=0)
    return {
        "bot_id": bot_id,
        "status": "degraded",
        "error": error.code,
        "message": str(error),
        "events_new": 0,
        "events_skipped": 0,
        "cursor": state.get("cursor"),
        "agent_calls": 0,
    }, error.exit_code


def run_once(project_root: Path, definition: dict[str, Any], policy: dict[str, Any]) -> tuple[dict[str, Any], int]:
    bot_id = definition["bot_id"]
    with FileLock(project_root, state_dir(project_root, bot_id) / "lock"), _quota_slot(
        project_root, policy.get("max_active_bots", 1)
    ):
        state = load_state(project_root, bot_id)
        if state.get("state") in {"stopped", "paused", "retired"}:
            raise StateBlockedError(f"bot is {state.get('state')}; explicit resume is required")
        _check_run_policy(project_root, definition, policy, state)
        try:
            adapter_config = read_json(project_root, project_root / definition["adapter"]["config_ref"])
            if not isinstance(adapter_config, dict):
                raise ValueError("adapter config must be an object")
            known_ids = _existing_event_ids(project_root, events_path(project_root, bot_id))
            adapter = get_adapter(definition["adapter"]["name"])
            polled = adapter.poll(
                cursor=state.get("cursor"),
                config=adapter_config,
                project_root=project_root,
                workspace_root=project_root / definition["workspace"]["root"],
                read_scope=definition["workspace"].get("read_scope", []),
            )
            new_events: list[dict[str, Any]] = []
            skipped = 0
            for event in polled.events:
                if not isinstance(event, dict) or not _valid_event_id(event.get("event_id")):
                    raise AdapterError("invalid_event", "every event needs a non-empty event_id", exit_code=5)
                if event["event_id"] in known_ids:
                    skipped += 1
                    continue
                known_ids.add(event["event_id"])
                new_events.append(event)
            for event in new_events:
                append_jsonl(
                    project_root,
                    events_path(project_root, bot_id),
                    {
                        "schema_version": 1,
                        "event_id": event["event_id"],
                        "kind": event.get("kind"),
                        "source": event.get("source"),
                        "received_at": now_iso(),
                        "payload": event.get("payload"),
                    },
                )
            completed_at = now_iso()
            write_json(
                project_root,
                state_path(project_root, bot_id),
                {"schema_version": 1, "bot_id": bot_id, "state": "ok", "cursor": polled.next_cursor, "last_error": None, "last_run_at": completed_at, "last_success_at": completed_at},
            )
            write_json(
                project_root,
                health_path(project_root, bot_id),
                {
                    "schema_version": 1,
                    "bot_id": bot_id,
                    "state": "ok",
                    "last_run_at": completed_at,
                    "last_success_at": completed_at,
                    "lag_seconds": 0,
                    "last_error": None,
                    "consecutive_failures": 0,
                    "approval_revision": definition.get("revision"),
                    "adapter_auth_status": "ok",
                },
            )
            append_lifecycle(project_root, bot_id, "run_succeeded", events_new=len(new_events), events_skipped=skipped, cursor=polled.next_cursor, agent_calls=0)
            return {"bot_id": bot_id, "status": "ok", "events_new": len(new_events), "events_skipped": skipped, "cursor": polled.next_cursor, "agent_calls": 0}, 0
        except AdapterError as exc:
            return _fail(project_root, definition, state, exc)
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
            return _fail(project_root, definition, state, AdapterError("runtime_io", str(exc), exit_code=EXIT_RUNTIME))


def stop_bot(project_root: Path, bot_id: str, reason: str | None) -> dict[str, Any]:
    with FileLock(project_root, state_dir(project_root, bot_id) / "lock"):
        state = load_state(project_root, bot_id)
        state.update({"schema_version": 1, "bot_id": bot_id, "state": "stopped", "stopped_at": now_iso(), "stop_reason": reason})
        write_json(project_root, state_path(project_root, bot_id), state)
        append_lifecycle(project_root, bot_id, "stopped", reason=reason)
        return state


def resume_bot(project_root: Path, bot_id: str) -> dict[str, Any]:
    with FileLock(project_root, state_dir(project_root, bot_id) / "lock"):
        state = load_state(project_root, bot_id)
        if state.get("state") == "retired":
            raise StateBlockedError("retired bot cannot be resumed; create a new bot or revision")
        state.update({"schema_version": 1, "bot_id": bot_id, "state": "running", "resumed_at": now_iso()})
        state.pop("stop_reason", None)
        write_json(project_root, state_path(project_root, bot_id), state)
        append_lifecycle(project_root, bot_id, "resumed")
        return state
