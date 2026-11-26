#!/usr/bin/env python3
"""Minimal, single-pass bot runtime with bounded local state writes."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import signal
import threading
from contextlib import contextmanager
import stat
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from bot_adapters import AdapterError, get_adapter

EXIT_APPROVAL = 3
EXIT_RUNTIME = 5
RESIDENT_BACKOFF_INITIAL_SECONDS = 1.0
RESIDENT_BACKOFF_MAX_SECONDS = 30.0


class LockedError(RuntimeError):
    pass


class StateBlockedError(RuntimeError):
    pass


def pid_alive(pid: Any) -> bool:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


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


def resident_path(project_root: Path, bot_id: str) -> Path:
    return state_dir(project_root, bot_id) / "resident.json"


def run_log_path(project_root: Path, bot_id: str) -> Path:
    return state_dir(project_root, bot_id) / "run.log"


def load_resident(project_root: Path, bot_id: str) -> dict[str, Any]:
    try:
        value = read_json(project_root, resident_path(project_root, bot_id))
    except (FileNotFoundError, OSError, RuntimeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


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


def write_text(project_root: Path, path: Path, text: str) -> None:
    """Atomically write UTF-8 text through the same no-follow path checks."""
    data = text.encode("utf-8")
    parent_fd, name = _open_parent(project_root, path, create=True)
    temporary: str | None = None
    try:
        _reject_symlink(parent_fd, name)
        temporary = _write_temporary(parent_fd, name, data)
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
        if state.get("state") in {"stopped", "paused", "retired", "approval_invalidated"}:
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


def _cursor_metadata(cursor: Any) -> dict[str, Any]:
    """Return non-content cursor telemetry suitable for logs and panel output."""
    encoded = json.dumps(cursor, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    items = cursor.get("items") if isinstance(cursor, dict) else None
    return {
        "cursor_sha256": hashlib.sha256(encoded).hexdigest(),
        "cursor_items": len(items) if isinstance(items, dict) else 0,
    }


def _resident_log(project_root: Path, bot_id: str, event: str, **fields: Any) -> None:
    # Deliberately allow only telemetry fields here; never log exception text or payloads.
    append_jsonl(
        project_root,
        run_log_path(project_root, bot_id),
        {"schema_version": 1, "at": now_iso(), "pid": os.getpid(), "bot_id": bot_id, "event": event, **fields},
    )


def _future_iso(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def run_resident(
    project_root: Path,
    definition: dict[str, Any],
    policy: dict[str, Any],
    *,
    interval_seconds: float,
    max_rounds: int | None = None,
    once: Callable[..., tuple[dict[str, Any], int]] | None = None,
    preflight: Callable[[], str | None] | None = None,
    emit: Callable[[dict[str, Any]], None] | None = None,
) -> int:
    """Run read-only polling until stopped, with a separate long-lived lease lock.

    The resident lock is held for the process lifetime; run_once still uses its
    short lock, so stop/resume and a manual once run are not blocked by sleep.
    """
    if isinstance(interval_seconds, bool) or not isinstance(interval_seconds, (int, float)) or interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    project_root = project_root.resolve()
    if max_rounds is not None and (isinstance(max_rounds, bool) or not isinstance(max_rounds, int) or max_rounds <= 0):
        raise ValueError("max_rounds must be positive")
    bot_id = definition["bot_id"]
    once = once or run_once
    emit = emit or (lambda payload: print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True))
    runtime = definition.get("runtime", {})
    heartbeat_seconds = max(1, int(runtime.get("heartbeat_seconds", 5)))
    lease_seconds = max(float(runtime.get("lock_ttl_seconds", heartbeat_seconds * 3)), heartbeat_seconds * 3.0)

    # This lock is intentionally different from run_once's short lock.
    with FileLock(project_root, state_dir(project_root, bot_id) / "resident.lock"):
        previous_resident = load_resident(project_root, bot_id)
        previous_pid = previous_resident.get("pid")
        if (
            previous_resident.get("state") in {"starting", "running", "sleeping", "backoff", "stopping"}
            and previous_pid != os.getpid()
            and pid_alive(previous_pid)
        ):
            raise LockedError(
                f"another resident process is active for {bot_id} (pid {previous_pid}); "
                "stop it before starting a new one"
            )
        initial = load_state(project_root, bot_id)
        if initial.get("state") in {"stopped", "paused", "retired", "approval_invalidated"}:
            raise StateBlockedError(f"bot is {initial.get('state')}; explicit resume is required")

        stop_event = threading.Event()
        heartbeat_stop = threading.Event()
        status_lock = threading.Lock()
        received_signals: list[int] = []
        previous_handlers: dict[int, Any] = {}
        started_at = now_iso()
        resident = {
            "schema_version": 1,
            "bot_id": bot_id,
            "pid": os.getpid(),
            "state": "starting",
            "started_at": started_at,
            "heartbeat_at": started_at,
            "lease_expires_at": _future_iso(lease_seconds),
            "round": 0,
        }

        def write_resident(**fields: Any) -> None:
            with status_lock:
                resident.update(fields)
                resident["heartbeat_at"] = now_iso()
                if resident.get("state") not in {"stopped", "auth_required"}:
                    resident["lease_expires_at"] = _future_iso(lease_seconds)
                write_json(project_root, resident_path(project_root, bot_id), resident)

        def heartbeat() -> None:
            while not heartbeat_stop.wait(heartbeat_seconds):
                try:
                    # A stop command only takes the short lock; wake the sleeper
                    # from this thread without waiting for a long poll interval.
                    if load_state(project_root, bot_id).get("state") == "stopped":
                        stop_event.set()
                    write_resident()
                except (OSError, RuntimeError, ValueError) as exc:
                    try:
                        _resident_log(project_root, bot_id, "heartbeat_error", error=type(exc).__name__)
                    except (OSError, RuntimeError):
                        pass

        def request_stop(signum: int, _frame: Any) -> None:
            received_signals.append(signum)
            stop_event.set()

        for signum in (signal.SIGTERM, signal.SIGINT):
            try:
                previous_handlers[signum] = signal.getsignal(signum)
                signal.signal(signum, request_stop)
            except ValueError:
                # Direct unit tests may run outside the main thread; CLI runs do not.
                previous_handlers.pop(signum, None)

        initial = dict(initial)
        initial.update({"schema_version": 1, "bot_id": bot_id, "state": "running", "resident_started_at": started_at})
        write_json(project_root, state_path(project_root, bot_id), initial)
        append_lifecycle(project_root, bot_id, "resident_started", pid=os.getpid(), interval_seconds=interval_seconds)
        _resident_log(project_root, bot_id, "daemon_start", interval_seconds=interval_seconds)
        write_resident(state="running")
        heartbeat_thread = threading.Thread(target=heartbeat, name=f"bot-{bot_id}-heartbeat", daemon=True)
        heartbeat_thread.start()

        round_number = 0
        consecutive_failures = 0
        successful_rounds = 0
        last_failure_code = 0
        last_failure_error: str | None = None
        backoff = float(RESIDENT_BACKOFF_INITIAL_SECONDS)
        stop_reason = "max_rounds"

        def requested_stop_reason() -> str:
            if load_state(project_root, bot_id).get("state") == "stopped":
                return "external_stop"
            return "signal:" + (signal.Signals(received_signals[-1]).name if received_signals else "requested")

        def stop_heartbeat() -> None:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=max(2.0, heartbeat_seconds + 1.0))

        def finish_stopped(reason: str) -> None:
            stop_heartbeat()
            for signum in received_signals:
                _resident_log(project_root, bot_id, "signal", signal=signal.Signals(signum).name)
            with FileLock(project_root, state_dir(project_root, bot_id) / "lock"):
                current = load_state(project_root, bot_id)
                current.update({"schema_version": 1, "bot_id": bot_id, "state": "stopped", "stopped_at": now_iso(), "stop_reason": reason})
                write_json(project_root, state_path(project_root, bot_id), current)
                health = load_health(project_root, bot_id)
                health.update({"schema_version": 1, "bot_id": bot_id, "state": "stopped", "daemon_pid": os.getpid(), "last_heartbeat_at": now_iso()})
                write_json(project_root, health_path(project_root, bot_id), health)
            write_resident(state="stopped", stop_reason=reason, stopped_at=now_iso(), lease_expires_at=None)
            append_lifecycle(project_root, bot_id, "resident_stopped", reason=reason, rounds=round_number)
            _resident_log(project_root, bot_id, "daemon_stop", reason=reason, rounds=round_number)

        def finish_auth_required(error: str) -> None:
            stop_heartbeat()
            with FileLock(project_root, state_dir(project_root, bot_id) / "lock"):
                current = load_state(project_root, bot_id)
                current.update({"schema_version": 1, "bot_id": bot_id, "state": "paused", "auth_required": True, "last_error": error, "paused_at": now_iso()})
                write_json(project_root, state_path(project_root, bot_id), current)
                health = load_health(project_root, bot_id)
                health.update({"schema_version": 1, "bot_id": bot_id, "state": "auth_required", "last_error": error, "adapter_auth_status": "unauthorized", "daemon_pid": os.getpid(), "last_heartbeat_at": now_iso()})
                write_json(project_root, health_path(project_root, bot_id), health)
            write_resident(state="auth_required", stop_reason="authentication_expired", stopped_at=now_iso(), lease_expires_at=None)
            append_lifecycle(project_root, bot_id, "resident_auth_required", error=error, rounds=round_number)
            _resident_log(project_root, bot_id, "auth_stop", error=error, rounds=round_number)

        def finish_approval_invalidated(problem: str) -> None:
            stop_heartbeat()
            with FileLock(project_root, state_dir(project_root, bot_id) / "lock"):
                current = load_state(project_root, bot_id)
                current.update({"schema_version": 1, "bot_id": bot_id, "state": "approval_invalidated", "last_error": problem, "paused_at": now_iso()})
                write_json(project_root, state_path(project_root, bot_id), current)
                health = load_health(project_root, bot_id)
                health.update({"schema_version": 1, "bot_id": bot_id, "state": "approval_invalidated", "last_error": problem, "daemon_pid": os.getpid(), "last_heartbeat_at": now_iso()})
                write_json(project_root, health_path(project_root, bot_id), health)
            write_resident(state="approval_invalidated", stop_reason=problem, stopped_at=now_iso(), lease_expires_at=None)
            append_lifecycle(project_root, bot_id, "resident_approval_invalidated", error=problem, rounds=round_number)
            _resident_log(project_root, bot_id, "approval_invalidated", error=problem, rounds=round_number)

        def finish_failed(error: str, code: int) -> None:
            stop_heartbeat()
            with FileLock(project_root, state_dir(project_root, bot_id) / "lock"):
                current = load_state(project_root, bot_id)
                current.update({"schema_version": 1, "bot_id": bot_id, "state": "degraded", "last_error": error, "last_run_at": now_iso()})
                write_json(project_root, state_path(project_root, bot_id), current)
                health = load_health(project_root, bot_id)
                health.update({"schema_version": 1, "bot_id": bot_id, "state": "degraded", "last_error": error, "daemon_pid": os.getpid(), "last_heartbeat_at": now_iso()})
                write_json(project_root, health_path(project_root, bot_id), health)
            write_resident(state="failed", stop_reason="max_rounds_failed", stopped_at=now_iso(), lease_expires_at=None, last_error=error)
            append_lifecycle(project_root, bot_id, "resident_failed", error=error, exit_code=code, rounds=round_number)
            _resident_log(project_root, bot_id, "daemon_failed", error=error, rounds=round_number)

        try:
            while max_rounds is None or round_number < max_rounds:
                if stop_event.is_set():
                    stop_reason = requested_stop_reason()
                    break
                current = load_state(project_root, bot_id)
                if current.get("state") == "stopped":
                    stop_reason = "external_stop"
                    break
                if current.get("state") in {"paused", "retired", "approval_invalidated"}:
                    raise StateBlockedError(f"bot is {current.get('state')}; explicit resume is required")
                if preflight is not None:
                    problem = preflight()
                    if problem:
                        finish_approval_invalidated(problem)
                        emit({"bot_id": bot_id, "status": "approval_invalidated", "rounds": round_number, "error": problem, "agent_calls": 0})
                        return EXIT_APPROVAL

                round_number += 1
                write_resident(state="running", round=round_number)
                _resident_log(project_root, bot_id, "round_start", round=round_number)
                try:
                    payload, code = once(project_root, definition, policy)
                except LockedError:
                    payload, code = {"status": "degraded", "error": "locked", "events_new": 0, "events_skipped": 0, "cursor": current.get("cursor"), "agent_calls": 0}, EXIT_RUNTIME
                except StateBlockedError as exc:
                    payload, code = {"status": "skipped", "error": str(exc), "events_new": 0, "events_skipped": 0, "cursor": current.get("cursor"), "agent_calls": 0}, EXIT_APPROVAL
                except AdapterError as exc:
                    payload, code = {"status": "degraded", "error": exc.code, "events_new": 0, "events_skipped": 0, "cursor": current.get("cursor"), "agent_calls": 0}, exc.exit_code
                except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
                    payload, code = {"status": "degraded", "error": "runtime_io", "events_new": 0, "events_skipped": 0, "cursor": current.get("cursor"), "agent_calls": 0}, EXIT_RUNTIME

                error = payload.get("error") if isinstance(payload, dict) else "runtime_io"
                metadata = _cursor_metadata(payload.get("cursor") if isinstance(payload, dict) else None)
                status = "skipped" if isinstance(payload, dict) and payload.get("status") == "skipped" else ("ok" if code == 0 else ("auth_required" if code == 6 else "degraded"))
                summary = {
                    "bot_id": bot_id,
                    "round": round_number,
                    "status": status,
                    "events_new": int(payload.get("events_new", 0)) if isinstance(payload, dict) else 0,
                    "events_skipped": int(payload.get("events_skipped", 0)) if isinstance(payload, dict) else 0,
                    "agent_calls": 0,
                    **metadata,
                }
                if error:
                    summary["error"] = error
                emit(summary)
                _resident_log(
                    project_root, bot_id, "round_end", round=round_number, status=status,
                    events_new=summary["events_new"], events_skipped=summary["events_skipped"],
                    error=error, **metadata,
                )

                if code == 6:
                    finish_auth_required(str(error or "authentication_expired"))
                    return 6
                if status == "skipped":
                    if stop_event.is_set():
                        stop_reason = requested_stop_reason()
                        break
                    if max_rounds is not None and round_number >= max_rounds:
                        break
                    if stop_event.wait(float(interval_seconds)):
                        stop_reason = requested_stop_reason()
                        break
                    continue
                if code == 0:
                    successful_rounds += 1
                    consecutive_failures = 0
                    backoff = float(RESIDENT_BACKOFF_INITIAL_SECONDS)
                else:
                    last_failure_code = code
                    last_failure_error = str(error or "runtime_io")
                    consecutive_failures += 1
                    if max_rounds is not None and round_number >= max_rounds:
                        break
                    delay = min(float(RESIDENT_BACKOFF_MAX_SECONDS), backoff)
                    backoff = min(float(RESIDENT_BACKOFF_MAX_SECONDS), backoff * 2)
                    write_resident(state="backoff", last_error=error, consecutive_failures=consecutive_failures, next_poll_at=_future_iso(delay))
                    _resident_log(project_root, bot_id, "backoff", round=round_number, error=error, consecutive_failures=consecutive_failures, sleep_seconds=delay)
                    if stop_event.wait(delay):
                        stop_reason = requested_stop_reason()
                        break
                    continue

                if stop_event.is_set():
                    stop_reason = requested_stop_reason()
                    break
                if max_rounds is not None and round_number >= max_rounds:
                    stop_reason = "max_rounds"
                    break
                write_resident(state="sleeping", next_poll_at=_future_iso(float(interval_seconds)), last_error=None)
                if stop_event.wait(float(interval_seconds)):
                    stop_reason = requested_stop_reason()
                    break

            if (
                stop_reason == "max_rounds"
                and successful_rounds == 0
                and last_failure_code
            ):
                finish_failed(last_failure_error or "runtime_io", last_failure_code)
                emit(
                    {
                        "bot_id": bot_id,
                        "status": "failed",
                        "rounds": round_number,
                        "stop_reason": "max_rounds_failed",
                        "error": last_failure_error or "runtime_io",
                        "agent_calls": 0,
                    }
                )
                return last_failure_code

            finish_stopped(stop_reason)
            emit({"bot_id": bot_id, "status": "stopped", "rounds": round_number, "stop_reason": stop_reason, "agent_calls": 0})
            return 0
        finally:
            stop_heartbeat()
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)


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
        state.pop("auth_required", None)
        state.pop("approval_invalidated", None)
        state.pop("paused_at", None)
        state.pop("last_error", None)
        state.pop("last_run_at", None)
        write_json(project_root, state_path(project_root, bot_id), state)
        append_lifecycle(project_root, bot_id, "resumed")
        return state
