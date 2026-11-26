#!/usr/bin/env python3
"""Deterministic triage batches, result validation, and human-readable reports."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from bot_runtime import (
    FileLock,
    LockedError,
    append_jsonl,
    bot_dir,
    events_path,
    health_path,
    load_state,
    now_iso,
    read_bytes,
    read_json,
    state_dir,
    write_json,
    write_text,
)
from bot_schema import validate_definition

POLICY_VERSION = "triage-v2"
CLASSIFICATIONS = {
    "correction_of_delivered_work",
    "answer_to_my_question",
    "clarification_request",
    "handoff_or_notice",
    "scope_or_contract_change",
    "needs_more_context",
}
RESULT_SOURCES = {"agent", "heuristic"}
URGENCIES = {"high": 0, "normal": 1, "low": 2}
GROUP_ORDER = [
    ("correction_of_delivered_work", "需返工"),
    ("clarification_request", "需人决策"),
    ("scope_or_contract_change", "需确认契约/范围"),
    ("answer_to_my_question", "已回答/知会"),
    ("handoff_or_notice", "仅知会"),
    ("needs_more_context", "待补上下文"),
]
DISPOSITION_ACTIONS = {"accepted", "dismissed", "deferred", "noted", "handed_off"}
TRIAGE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
ATTACHMENT_MARKER = "[附件]"
BATCH_ID_PATTERN = re.compile(r"^tb-[A-Za-z0-9:-]{1,96}$")


class TriageError(Exception):
    def __init__(self, message: str, exit_code: int = 2) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def triage_dir(project_root: Path, bot_id: str) -> Path:
    return state_dir(project_root, bot_id) / "triage"


def batches_dir(project_root: Path, bot_id: str) -> Path:
    return triage_dir(project_root, bot_id) / "batches"


def results_path(project_root: Path, bot_id: str) -> Path:
    return triage_dir(project_root, bot_id) / "results.jsonl"


def dispositions_path(project_root: Path, bot_id: str) -> Path:
    return triage_dir(project_root, bot_id) / "dispositions.jsonl"


def ready_path(project_root: Path, bot_id: str) -> Path:
    return triage_dir(project_root, bot_id) / "ready.jsonl"


def lock_path(project_root: Path, bot_id: str) -> Path:
    return triage_dir(project_root, bot_id) / "lock"


def _triage_lock(project_root: Path, bot_id: str) -> FileLock:
    return FileLock(project_root, lock_path(project_root, bot_id))


def _definition_path(project_root: Path, bot_id: str) -> Path:
    return bot_dir(project_root, bot_id) / "definition.json"


def load_definition(project_root: Path, bot_id: str) -> dict[str, Any]:
    path = _definition_path(project_root, bot_id)
    try:
        definition = read_json(project_root, path)
    except FileNotFoundError as exc:
        raise TriageError(f"definition not found: {path}") from exc
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        raise TriageError(f"cannot read definition: {exc}") from exc
    if not isinstance(definition, dict):
        raise TriageError("definition must be a JSON object")
    validation = validate_definition(path, project_root)
    if not validation.ok:
        raise TriageError("definition invalid: " + "; ".join(validation.errors))
    return definition


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _read_jsonl(project_root: Path, path: Path) -> list[dict[str, Any]]:
    try:
        raw = read_bytes(project_root, path).decode("utf-8")
    except FileNotFoundError:
        return []
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise TriageError(f"{path}: invalid JSON on line {number}: {exc}") from exc
        if not isinstance(value, dict):
            raise TriageError(f"{path}: line {number} must be an object")
        rows.append(value)
    return rows


def _load_events(project_root: Path, bot_id: str) -> list[dict[str, Any]]:
    path = state_dir(project_root, bot_id) / "events.jsonl"
    events = _read_jsonl(project_root, path)
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for event in events:
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id.strip():
            raise TriageError("events.jsonl contains an event without a valid event_id")
        if event_id in seen:
            continue
        seen.add(event_id)
        unique.append(event)
    return unique


def _batched_ids(project_root: Path, bot_id: str) -> set[str]:
    root = batches_dir(project_root, bot_id)
    if not root.exists():
        return set()
    ids: set[str] = set()
    for directory in sorted(root.iterdir()):
        if not directory.is_dir():
            continue
        try:
            batch = read_json(project_root, directory / "batch.json")
        except (FileNotFoundError, OSError, RuntimeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(batch, dict):
            continue
        for event_id in batch.get("event_ids", []):
            if isinstance(event_id, str):
                ids.add(event_id)
    return ids


def _check_runtime_health(project_root: Path, bot_id: str) -> None:
    state = load_state(project_root, bot_id)
    try:
        raw = read_bytes(project_root, health_path(project_root, bot_id))
    except FileNotFoundError:
        health: dict[str, Any] = {}
    except (OSError, RuntimeError) as exc:
        raise TriageError(f"collector health is unreadable; no triage batch created: {exc}", 5) from exc
    else:
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TriageError(f"collector health is unreadable; no triage batch created: {exc}", 5) from exc
        if not isinstance(parsed, dict):
            raise TriageError("collector health is invalid; no triage batch created", 5)
        health = parsed

    state_name = state.get("state")
    auth_status = health.get("adapter_auth_status")
    auth_errors = {"authentication_expired", "auth_required", "unauthorized"}
    auth_states = {"expired", "unauthorized"}
    if (
        state.get("auth_required") is True
        or state.get("last_error") in auth_errors
        or state_name in {"auth_required"}
        or (state_name == "paused" and state.get("last_error") in auth_errors)
        or auth_status in auth_errors
        or auth_status in auth_states
    ):
        raise TriageError("authentication required; no triage batch created", 6)
    if not health:
        try:
            event_bytes = read_bytes(project_root, events_path(project_root, bot_id))
        except FileNotFoundError:
            event_bytes = b""
        except (OSError, RuntimeError) as exc:
            raise TriageError(f"collector events are unreadable; no triage batch created: {exc}", 5) from exc
        if event_bytes.strip():
            raise TriageError("collector health is missing; no triage batch created", 5)
    failures = health.get("consecutive_failures")
    try:
        failures = int(failures or 0)
    except (TypeError, ValueError):
        failures = 1
    if failures > 0 or health.get("state") == "degraded":
        raise TriageError("collector is degraded; no triage batch created", 5)
    if state_name == "degraded":
        raise TriageError("collector is degraded; no triage batch created", 5)


def _batch_id(events: list[dict[str, Any]], now: datetime) -> str:
    digest = hashlib.sha256(
        "\n".join(str(event["event_id"]) for event in events).encode("utf-8")
    ).hexdigest()[:8]
    return f"tb-{now.strftime('%Y%m%dT%H%M%SZ')}-{digest}"


def _make_batch(
    definition: dict[str, Any],
    events: list[dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    triage = definition.get("triage") if isinstance(definition.get("triage"), dict) else {}
    return {
        "schema_version": 1,
        "batch_id": _batch_id(events, now),
        "bot_id": definition.get("bot_id"),
        "goal_ref": definition.get("goal_ref"),
        "created_at": now.isoformat(),
        "status": "pending",
        "attempt_id": None,
        "claimed_at": None,
        "completed_at": None,
        "triage_trigger": triage.get("triage_trigger", "manual"),
        "triage_policy_version": POLICY_VERSION,
        "event_count": len(events),
        "event_ids": [event["event_id"] for event in events],
        "events": events,
    }


def _ensure_ready(project_root: Path, bot_id: str, current: datetime) -> None:
    ready_ids = {
        str(row.get("batch_id"))
        for row in _read_jsonl(project_root, ready_path(project_root, bot_id))
    }
    root = batches_dir(project_root, bot_id)
    if not root.exists():
        return
    for directory in sorted(root.iterdir()):
        if not directory.is_dir():
            continue
        try:
            batch = read_json(project_root, directory / "batch.json")
        except (FileNotFoundError, OSError, RuntimeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(batch, dict):
            continue
        batch_id = str(batch.get("batch_id"))
        if batch_id in ready_ids:
            continue
        append_jsonl(
            project_root,
            ready_path(project_root, bot_id),
            {
                "schema_version": 1,
                "event": "TRIAGE_READY",
                "batch_id": batch_id,
                "event_count": batch.get("event_count", 0),
                "triage_trigger": batch.get("triage_trigger", "manual"),
                "path": str(directory / "batch.json"),
                "at": current.isoformat(),
            },
        )
        ready_ids.add(batch_id)


def prepare_batches(
    project_root: Path,
    bot_id: str,
    *,
    now: datetime | None = None,
    force: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    try:
        with _triage_lock(project_root, bot_id):
            return _prepare_batches_locked(project_root, bot_id, now=now, force=force)
    except LockedError as exc:
        raise TriageError("triage state is busy; no batch created", 4) from exc


def _prepare_batches_locked(
    project_root: Path,
    bot_id: str,
    *,
    now: datetime | None = None,
    force: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    definition = load_definition(project_root, bot_id)
    _check_runtime_health(project_root, bot_id)
    current = now or datetime.now(timezone.utc)
    config = definition.get("triage") if isinstance(definition.get("triage"), dict) else {}
    interval = int(config.get("batch_interval_seconds", 1800))
    maximum = int(config.get("max_batch_events", 20))
    try:
        _ensure_ready(project_root, bot_id, current)
    except (OSError, RuntimeError) as exc:
        raise TriageError(f"cannot repair TRIAGE_READY notifications: {exc}", 5) from exc
    events = _load_events(project_root, bot_id)
    batched = _batched_ids(project_root, bot_id)
    pending = [event for event in events if event["event_id"] not in batched]
    if not pending:
        return [], 0

    batches: list[dict[str, Any]] = []
    while pending:
        chunk = pending[:maximum]
        first_time = _parse_time(chunk[0].get("received_at"))
        due = force or len(pending) >= maximum
        if first_time is not None:
            due = due or current - first_time >= timedelta(seconds=interval)
        if not due and not batches:
            return [], len(pending)
        if not due:
            break
        batch = _make_batch(definition, chunk, current)
        directory = batches_dir(project_root, bot_id) / batch["batch_id"]
        write_json(project_root, directory / "batch.json", batch, exclusive=True)
        try:
            append_jsonl(
                project_root,
                ready_path(project_root, bot_id),
                {
                    "schema_version": 1,
                    "event": "TRIAGE_READY",
                    "batch_id": batch["batch_id"],
                    "event_count": batch["event_count"],
                    "triage_trigger": batch["triage_trigger"],
                    "path": str(directory / "batch.json"),
                    "at": current.isoformat(),
                },
            )
        except (OSError, RuntimeError) as exc:
            raise TriageError(
                "batch was created but TRIAGE_READY was not written; rerun prepare to repair", 5
            ) from exc
        batches.append(batch)
        pending = pending[maximum:]
        if not force:
            # A full remainder is split immediately; only a short tail waits.
            if pending and len(pending) < maximum:
                first_time = _parse_time(pending[0].get("received_at"))
                if first_time is not None and current - first_time < timedelta(seconds=interval):
                    break
    return batches, len(pending)


def _batch_path(project_root: Path, bot_id: str, batch_id: str) -> Path:
    if not isinstance(batch_id, str) or not BATCH_ID_PATTERN.match(batch_id):
        raise TriageError("batch_id is invalid")
    return batches_dir(project_root, bot_id) / batch_id / "batch.json"


def claim_batch(project_root: Path, bot_id: str, batch_id: str, attempt_id: str) -> dict[str, Any]:
    if not TRIAGE_ID_PATTERN.match(attempt_id):
        raise TriageError("attempt_id is invalid")
    try:
        with _triage_lock(project_root, bot_id):
            path = _batch_path(project_root, bot_id, batch_id)
            try:
                batch = read_json(project_root, path)
            except FileNotFoundError as exc:
                raise TriageError(f"batch not found: {batch_id}") from exc
            if batch.get("status") != "pending":
                raise TriageError(f"batch is not pending: {batch.get('status')}")
            batch["status"] = "claimed"
            batch["attempt_id"] = attempt_id
            batch["claimed_at"] = now_iso()
            write_json(project_root, path, batch)
            return batch
    except LockedError as exc:
        raise TriageError("triage state is busy; batch not claimed", 4) from exc


def _load_results(project_root: Path, bot_id: str) -> list[dict[str, Any]]:
    rows = _read_jsonl(project_root, results_path(project_root, bot_id))
    for index, row in enumerate(rows):
        for field in ("evidence_refs", "uncertainties"):
            if not isinstance(row.get(field), list):
                raise TriageError(
                    f"results[{index}].{field}: must be a JSON array, not a string"
                )
    return rows


def _effective_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    superseded = {
        result.get("supersedes")
        for result in results
        if isinstance(result.get("supersedes"), str)
    }
    return [result for result in results if result.get("triage_id") not in superseded]


def _validate_result(result: Any, batch: dict[str, Any], index: int) -> dict[str, Any]:
    where = f"results[{index}]"
    if not isinstance(result, dict):
        raise TriageError(f"{where}: must be an object")
    required = {
        "event_id",
        "triage_id",
        "batch_id",
        "triage_policy_version",
        "classification",
        "source",
        "urgency",
        "rationale",
        "evidence_refs",
        "recommended_action",
        "required_authorization",
        "uncertainties",
        "status",
    }
    missing = sorted(required - set(result))
    if missing:
        raise TriageError(f"{where}: missing {', '.join(missing)}")
    for field in ("event_id", "triage_id", "batch_id", "triage_policy_version"):
        if not isinstance(result[field], str) or not result[field].strip():
            raise TriageError(f"{where}.{field}: must be a non-empty string")
    if not TRIAGE_ID_PATTERN.match(result["triage_id"]):
        raise TriageError(f"{where}.triage_id: invalid format")
    if result["batch_id"] != batch["batch_id"]:
        raise TriageError(f"{where}.batch_id: does not match the batch")
    if result["triage_policy_version"] != batch.get("triage_policy_version", POLICY_VERSION):
        raise TriageError(f"{where}.triage_policy_version: does not match the batch")
    if result["event_id"] not in batch.get("event_ids", []):
        raise TriageError(f"{where}.event_id: not in the batch")
    if result["classification"] not in CLASSIFICATIONS:
        raise TriageError(f"{where}.classification: unsupported value")
    if result["source"] not in RESULT_SOURCES:
        raise TriageError(f"{where}.source: must be agent or heuristic")
    if result["classification"] == "correction_of_delivered_work" and result["urgency"] != "high":
        raise TriageError(f"{where}.urgency: corrections of delivered work default to high")
    if result["urgency"] not in URGENCIES:
        raise TriageError(f"{where}.urgency: must be high, normal, or low")
    for field in ("rationale", "recommended_action", "required_authorization"):
        if not isinstance(result[field], str) or not result[field].strip():
            raise TriageError(f"{where}.{field}: must be a non-empty string")
    if not isinstance(result["evidence_refs"], list) or not all(
        isinstance(item, str) and item.strip() for item in result["evidence_refs"]
    ):
        raise TriageError(f"{where}.evidence_refs: must be a list of non-empty strings")
    if not isinstance(result["uncertainties"], list) or not all(
        isinstance(item, str) for item in result["uncertainties"]
    ):
        raise TriageError(f"{where}.uncertainties: must be a list of strings")
    if result["status"] != "proposed":
        raise TriageError(f"{where}.status: first version only accepts proposed")
    question = result.get("question")
    if question is not None and not isinstance(question, str):
        raise TriageError(f"{where}.question: must be a string when present")
    if "supersedes" in result and not isinstance(result["supersedes"], str):
        raise TriageError(f"{where}.supersedes: must be a string when present")
    return result


def _append_results_atomic(
    project_root: Path, bot_id: str, rows: list[dict[str, Any]]
) -> None:
    if not rows:
        return
    path = results_path(project_root, bot_id)
    try:
        existing = read_bytes(project_root, path)
    except FileNotFoundError:
        existing = b""
    if existing and not existing.endswith(b"\n"):
        existing += b"\n"
    addition = b"".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n"
        for row in rows
    )
    write_text(project_root, path, (existing + addition).decode("utf-8"))


def apply_results(
    project_root: Path,
    bot_id: str,
    batch_id: str,
    incoming: list[dict[str, Any]],
) -> dict[str, Any]:
    try:
        with _triage_lock(project_root, bot_id):
            return _apply_results_locked(project_root, bot_id, batch_id, incoming)
    except LockedError as exc:
        raise TriageError("triage state is busy; results not applied", 4) from exc


def _apply_results_locked(
    project_root: Path,
    bot_id: str,
    batch_id: str,
    incoming: list[dict[str, Any]],
) -> dict[str, Any]:
    path = _batch_path(project_root, bot_id, batch_id)
    try:
        batch = read_json(project_root, path)
    except FileNotFoundError as exc:
        raise TriageError(f"batch not found: {batch_id}") from exc
    status = batch.get("status")
    correction_only = status == "done"
    if correction_only:
        if not incoming or not all(
            isinstance(item, dict) and item.get("supersedes") for item in incoming
        ):
            raise TriageError("batch is done; only superseding corrections are accepted")
    elif status not in {"pending", "claimed", "applying"}:
        raise TriageError(f"batch cannot accept results in status {status}")
    existing = _load_results(project_root, bot_id)
    effective = {
        (row.get("event_id"), row.get("triage_policy_version")): row
        for row in _effective_results(existing)
    }
    by_id = {str(row.get("triage_id")): row for row in existing}
    seen_ids = set(by_id)
    by_event: dict[str, dict[str, Any]] = {}
    already: set[str] = set()
    for index, raw in enumerate(incoming):
        result = _validate_result(raw, batch, index)
        triage_id = result["triage_id"]
        if triage_id in by_id:
            prior = by_id[triage_id]
            if prior.get("event_id") != result["event_id"]:
                raise TriageError(f"duplicate triage_id: {triage_id}")
            if any(
                prior.get(field) != result.get(field)
                for field in (
                    "batch_id",
                    "triage_policy_version",
                    "classification",
                    "source",
                    "urgency",
                    "rationale",
                    "evidence_refs",
                    "recommended_action",
                    "required_authorization",
                    "uncertainties",
                    "status",
                    "question",
                    "supersedes",
                )
            ):
                raise TriageError(f"duplicate triage_id with different content: {triage_id}")
            already.add(result["event_id"])
            continue
        if triage_id in seen_ids:
            raise TriageError(f"duplicate triage_id: {triage_id}")
        seen_ids.add(triage_id)
        key = (result["event_id"], result["triage_policy_version"])
        if key in by_event:
            raise TriageError(f"duplicate result for event: {result['event_id']}")
        previous = effective.get(key)
        supersedes = result.get("supersedes")
        if previous is not None:
            if supersedes != previous.get("triage_id"):
                raise TriageError(
                    f"event already triaged; correction must supersede {previous.get('triage_id')}"
                )
        elif supersedes:
            raise TriageError(f"supersedes does not identify an effective result: {supersedes}")
        by_event[result["event_id"]] = result
    if not correction_only:
        expected = set(batch.get("event_ids", []))
        covered = set(by_event) | already
        missing = sorted(expected - covered)
        if missing:
            raise TriageError("results do not cover every event: " + ", ".join(missing))
    to_append = [result for result in incoming if result["event_id"] not in already]
    event_authors: dict[str, str] = {}
    for event in batch.get("events", []):
        if not isinstance(event, dict):
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        author = payload.get("creator_name") or payload.get("creator_user_key") or "unknown"
        event_authors[str(event.get("event_id", ""))] = str(author)
    for result in to_append:
        if not result.get("author"):
            result["author"] = event_authors.get(str(result.get("event_id")), "unknown")
    created_at = now_iso()
    if to_append:
        if not correction_only:
            batch["status"] = "applying"
            write_json(project_root, path, batch)
        _append_results_atomic(
            project_root,
            bot_id,
            [
                {
                    **result,
                    "schema_version": 1,
                    "created_at": created_at,
                }
                for result in to_append
            ],
        )
    if not correction_only:
        batch["status"] = "done"
        batch["completed_at"] = created_at
    all_results = _load_results(project_root, bot_id)
    batch["result_count"] = sum(
        1
        for row in _effective_results(all_results)
        if row.get("batch_id") == batch_id
    )
    write_json(project_root, path, batch)
    return {
        "batch_id": batch_id,
        "accepted": len(to_append),
        "already_applied": len(already),
        "status": batch["status"],
        "correction": correction_only,
    }


def add_disposition(
    project_root: Path,
    bot_id: str,
    triage_id: str,
    action: str,
    actor: str,
    note: str | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    if action not in DISPOSITION_ACTIONS:
        raise TriageError(f"unsupported disposition action: {action}")
    if not actor.strip():
        raise TriageError("actor is required")
    try:
        with _triage_lock(project_root, bot_id):
            results = _effective_results(_load_results(project_root, bot_id))
            target = next((row for row in results if row.get("triage_id") == triage_id), None)
            if target is None:
                raise TriageError(f"effective triage result not found: {triage_id}")
            existing = _read_jsonl(project_root, dispositions_path(project_root, bot_id))
            if any(
                row.get("triage_id") == triage_id and row.get("action") == action
                for row in existing
            ):
                raise TriageError(f"disposition already exists for {triage_id}: {action}")
            action_id = hashlib.sha256(
                f"{triage_id}|{action}|{now_iso()}".encode("utf-8")
            ).hexdigest()[:16]
            payload = {
                "schema_version": 1,
                "action_id": action_id,
                "event_id": target.get("event_id"),
                "triage_id": triage_id,
                "action": action,
                "actor": actor,
                "note": note,
                "task_id": task_id,
                "created_at": now_iso(),
            }
            append_jsonl(project_root, dispositions_path(project_root, bot_id), payload)
            return payload
    except LockedError as exc:
        raise TriageError("triage state is busy; disposition not recorded", 4) from exc


def _work_item_index(events: list[dict[str, Any]]) -> dict[str, str]:
    index: dict[str, str] = {}
    for event in events:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
        detail = snapshot.get("detail") if isinstance(snapshot.get("detail"), dict) else {}
        attributes = detail.get("work_item_attribute") if isinstance(detail.get("work_item_attribute"), dict) else {}
        work_item_id = attributes.get("work_item_id") or payload.get("work_item_id")
        name = attributes.get("work_item_name")
        if isinstance(work_item_id, str) and isinstance(name, str) and name:
            index[work_item_id] = name
    return index


def _event_meta(event: dict[str, Any], work_items: dict[str, str]) -> dict[str, str]:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    work_item_id = payload.get("work_item_id")
    if not isinstance(work_item_id, str):
        source = event.get("source") if isinstance(event.get("source"), dict) else {}
        work_item_id = source.get("work_item_id")
    work_item_id = work_item_id if isinstance(work_item_id, str) else ""
    title = work_items.get(work_item_id, work_item_id or "未知工作项")
    author = payload.get("creator_name") or payload.get("creator_user_key") or "unknown"
    created = payload.get("created_at") or event.get("received_at") or ""
    content = payload.get("content") if isinstance(payload.get("content"), str) else ""
    attachments = payload.get("attachments") if isinstance(payload.get("attachments"), list) else []
    names = []
    for attachment in attachments:
        if isinstance(attachment, dict):
            name = attachment.get("name")
            if isinstance(name, str) and name:
                names.append(name)
            else:
                names.append("未提供附件名")
    return {
        "work_item_id": work_item_id,
        "title": title,
        "author": str(author),
        "created": str(created),
        "content": content.strip(),
        "attachments": ", ".join(names),
        "event_id": str(event.get("event_id", "")),
    }


def _short(text: str, limit: int) -> str:
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def _result_lines(result: dict[str, Any], meta: dict[str, str]) -> list[str]:
    urgency = result.get("urgency", "normal")
    marker = "[高]" if urgency == "high" else ""
    question = result.get("question") or _short(meta["content"], 120) or "未提供明确问句"
    evidence = meta["event_id"]
    if meta["attachments"]:
        evidence += "；附件：" + meta["attachments"]
    return [
        f"### {marker} {meta['title']} · {meta['author']} · {meta['created']}".strip(),
        f"- 发生了什么：{_short(meta['content'], 180) or '状态或字段发生变化'}",
        f"- 核心内容：{_short(question, 180)}",
        f"- 判定依据：{_short(str(result.get('rationale', '')), 180)}",
        f"- 来源：{result.get('source', 'unknown')}",
        f"- 建议动作：{_short(str(result.get('recommended_action', '')), 180)}",
        f"- 需要谁授权：{_short(str(result.get('required_authorization', '')), 120)}",
        f"- 证据：{evidence}",
    ]


def _panel_line(result: dict[str, Any], meta: dict[str, str]) -> str:
    marker = "[高] " if result.get("urgency") == "high" else ""
    question = result.get("question") or _short(meta["content"], 60) or "见完整报告"
    return (
        f"- {marker}{_short(meta['title'], 28)} · {_short(meta['author'], 12)}："
        f"{_short(question, 60)}"
        f" → {_short(str(result.get('recommended_action', '')), 60)}"
        f"（授权：{_short(str(result.get('required_authorization', '')), 24)}）"
    )


def _report_groups(
    results: list[dict[str, Any]], events_by_id: dict[str, dict[str, Any]], work_items: dict[str, str]
) -> dict[str, list[tuple[dict[str, Any], dict[str, str]]]]:
    grouped: dict[str, list[tuple[dict[str, Any], dict[str, str]]]] = defaultdict(list)
    for result in results:
        event = events_by_id.get(result.get("event_id"))
        if event is None:
            continue
        meta = _event_meta(event, work_items)
        grouped[str(result.get("classification"))].append((result, meta))
    for rows in grouped.values():
        rows.sort(
            key=lambda pair: (
                URGENCIES.get(str(pair[0].get("urgency")), 9),
                pair[1]["created"],
            )
        )
    return grouped


def _report_paths(definition: dict[str, Any], project_root: Path) -> Path:
    for output in definition.get("outputs", []):
        if isinstance(output, dict) and output.get("type") == "local_file":
            path = output.get("path")
            if isinstance(path, str) and path:
                return project_root / path / "triage"
    return state_dir(project_root, str(definition.get("bot_id"))) / "reports" / "triage"


def render_report(
    project_root: Path,
    bot_id: str,
    batch_ids: list[str],
    *,
    only_batch: bool,
    source: str = "agent",
) -> str | None:
    definition = load_definition(project_root, bot_id)
    _check_runtime_health(project_root, bot_id)
    batches: list[dict[str, Any]] = []
    for batch_id in batch_ids:
        try:
            batch = read_json(project_root, _batch_path(project_root, bot_id, batch_id))
        except FileNotFoundError:
            raise TriageError(f"batch not found: {batch_id}")
        batches.append(batch)
    if only_batch and not batches:
        raise TriageError("batch is required")
    if not only_batch and not batches:
        all_batches = batches_dir(project_root, bot_id)
        if all_batches.exists():
            for directory in sorted(all_batches.iterdir()):
                try:
                    batches.append(read_json(project_root, directory / "batch.json"))
                except (FileNotFoundError, OSError, RuntimeError, ValueError, json.JSONDecodeError):
                    continue
    all_results = _effective_results(_load_results(project_root, bot_id))
    current = [
        row for row in all_results if row.get("triage_policy_version") == POLICY_VERSION
    ]
    results = [row for row in current if row.get("source") == source]
    excluded_heuristic = sum(1 for row in current if row.get("source") == "heuristic")
    done_ids = {
        str(batch.get("batch_id")) for batch in batches if batch.get("status") == "done"
    }
    if only_batch:
        results = [
            row
            for row in results
            if row.get("batch_id") in set(batch_ids) and row.get("batch_id") in done_ids
        ]
    else:
        results = [row for row in results if str(row.get("batch_id")) in done_ids]
    pending_batches = [batch for batch in batches if batch.get("status") != "done"]
    if not results:
        if pending_batches:
            ids = ",".join(str(batch.get("batch_id")) for batch in pending_batches)
            return (
                f"TRIAGE_PENDING batches={len(pending_batches)} "
                f"events={sum(int(batch.get('event_count', 0) or 0) for batch in pending_batches)} "
                f"batch_ids={ids}"
            )
        return None
    events: list[dict[str, Any]] = []
    for batch in batches:
        for event in batch.get("events", []):
            if isinstance(event, dict):
                events.append(event)
    events_by_id = {str(event.get("event_id")): event for event in events}
    work_items = _work_item_index(events)
    grouped = _report_groups(results, events_by_id, work_items)
    now = datetime.now(timezone.utc)
    latest = batches[-1].get("batch_id") if batches else "unknown"
    name = f"all-{latest}" if not only_batch else str(latest)
    name = f"{name}.{source}"
    report_dir = _report_paths(definition, project_root)
    md_path = report_dir / f"{name}.md"
    panel_path = report_dir / f"{name}.panel.txt"
    config = definition.get("triage") if isinstance(definition.get("triage"), dict) else {}
    max_items = int(config.get("human_report_max_items_per_group", 5))
    max_lines = int(config.get("human_report_max_lines", 40))
    md: list[str] = [
        "# 飞书评论分诊报告",
        "",
        f"- 生成时间：{now.isoformat()}",
        f"- 批次数：{len(batches)}",
        f"- 结论数：{len(results)}（来源：{source}）",
    ]
    if source == "agent" and excluded_heuristic:
        md.append(f"- 启发式结论：{excluded_heuristic} 条，未混入本报告；请用 `--source heuristic` 单独渲染")
    if pending_batches:
        md.append(
            "- 待分诊批次："
            + "、".join(
                f"{batch.get('batch_id')}（{batch.get('event_count', 0)} 条）"
                for batch in pending_batches
            )
        )
    md.append("")
    for classification, label in GROUP_ORDER:
        rows = grouped.get(classification, [])
        if not rows:
            continue
        md.append(f"## {label}（{len(rows)}）")
        md.append("")
        for result, meta in rows:
            md.extend(_result_lines(result, meta))
            md.append("")
    report_text = "\n".join(md).rstrip() + "\n"
    write_text(project_root, md_path, report_text)

    high_rows = [
        (result, meta)
        for rows in grouped.values()
        for result, meta in rows
        if result.get("urgency") == "high"
    ]
    high_rows.sort(key=lambda pair: pair[1]["created"])
    panel: list[str] = [
        f"[分诊] 批次={len(batches)} 结论={len(results)} 高优先级={len(high_rows)}"
    ]
    if pending_batches:
        panel.append(
            "待分诊："
            + "、".join(str(batch.get("batch_id")) for batch in pending_batches)
        )

    def add_section(header: str, lines: list[str], other: int) -> bool:
        body = lines[:max_items]
        section = [header, *body]
        if len(lines) > len(body):
            section.append(f"…另有 {len(lines) - len(body)} 项")
        elif other:
            section.append(f"…另有 {other} 项")
        if len(panel) + len(section) > max_lines - 1:
            return False
        panel.extend(section)
        return True

    if high_rows:
        high_by_label: dict[str, int] = defaultdict(int)
        for result, _meta in high_rows:
            high_by_label[str(result.get("classification"))] += 1
        parts = []
        for classification, label in GROUP_ORDER:
            count = high_by_label.get(classification, 0)
            if count:
                parts.append(f"{label} {count}")
        panel.append("高优先级分布：" + "、".join(parts))

    skipped: list[str] = []
    for classification, label in GROUP_ORDER:
        rows = grouped.get(classification, [])
        if not rows:
            continue
        lines = [_panel_line(result, meta) for result, meta in rows]
        high_note = sum(1 for result, _meta in rows if result.get("urgency") == "high")
        header = f"{label}（{len(rows)}）"
        if high_note:
            header += f" 其中 {high_note} 项标 [高]"
        if not add_section(header, lines, 0):
            skipped.append(f"{label} {len(rows)}")

    if source == "agent" and excluded_heuristic:
        note = f"启发式结果另 {excluded_heuristic} 条未混入（--source heuristic）"
        if len(panel) + 2 <= max_lines:
            panel.append(note)
    tail = f"完整报告：{md_path}"
    if skipped:
        tail = "…未显示：" + "、".join(skipped) + "；" + tail
    panel.append(tail)
    if len(panel) > max_lines:
        # Keep the header and the report path; drop whole group sections only.
        panel = panel[: max(1, max_lines - 1)] + [tail]
    panel_text = "\n".join(panel[:max_lines]) + "\n"
    write_text(project_root, panel_path, panel_text)
    return panel_text.rstrip()


def parse_results_file(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        value = None
    if isinstance(value, dict) and isinstance(value.get("results"), list):
        return value["results"]
    if isinstance(value, list):
        return value
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise TriageError(f"invalid JSONL on line {number}: {exc}") from exc
        if not isinstance(row, dict):
            raise TriageError(f"line {number} must be an object")
        rows.append(row)
    return rows


def cmd_prepare(args: argparse.Namespace) -> int:
    try:
        batches, pending = prepare_batches(
            args.project_root.resolve(), args.bot_id, force=args.force
        )
    except TriageError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return exc.exit_code
    if not batches:
        if pending:
            print(f"TRIAGE_WAITING pending={pending}")
        return 0
    for batch in batches:
        print(
            f"TRIAGE_READY batch_id={batch['batch_id']} events={batch['event_count']} "
            f"path={batches_dir(args.project_root.resolve(), args.bot_id) / batch['batch_id'] / 'batch.json'}"
        )
    return 0


def cmd_claim(args: argparse.Namespace) -> int:
    try:
        batch = claim_batch(args.project_root.resolve(), args.bot_id, args.batch_id, args.attempt_id)
    except TriageError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return exc.exit_code
    print(json.dumps({"batch_id": batch["batch_id"], "status": batch["status"], "attempt_id": batch["attempt_id"]}, ensure_ascii=False))
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    try:
        incoming = parse_results_file(args.file)
        summary = apply_results(args.project_root.resolve(), args.bot_id, args.batch_id, incoming)
    except TriageError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return exc.exit_code
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


def cmd_dispose(args: argparse.Namespace) -> int:
    try:
        payload = add_disposition(
            args.project_root.resolve(),
            args.bot_id,
            args.triage_id,
            args.action,
            args.actor,
            args.note,
            args.task_id,
        )
    except TriageError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return exc.exit_code
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    batch_ids = args.batch_id or []
    try:
        text = render_report(
            args.project_root.resolve(),
            args.bot_id,
            batch_ids,
            only_batch=bool(batch_ids),
            source=args.source,
        )
    except TriageError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return exc.exit_code
    if text:
        print(text)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Triage batches, results, and human-readable reports.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="create batches from unbatched events")
    prepare.add_argument("--project-root", default=Path("."), type=Path)
    prepare.add_argument("--bot-id", required=True)
    prepare.add_argument("--force", action="store_true")
    prepare.set_defaults(func=cmd_prepare)

    claim = subparsers.add_parser("claim", help="claim a pending batch")
    claim.add_argument("--project-root", default=Path("."), type=Path)
    claim.add_argument("--bot-id", required=True)
    claim.add_argument("--batch-id", required=True)
    claim.add_argument("--attempt-id", required=True)
    claim.set_defaults(func=cmd_claim)

    apply = subparsers.add_parser("apply", help="validate and append results for a batch")
    apply.add_argument("--project-root", default=Path("."), type=Path)
    apply.add_argument("--bot-id", required=True)
    apply.add_argument("--batch-id", required=True)
    apply.add_argument("--file", required=True, type=Path)
    apply.set_defaults(func=cmd_apply)

    dispose = subparsers.add_parser("dispose", help="append a disposition for a triage result")
    dispose.add_argument("--project-root", default=Path("."), type=Path)
    dispose.add_argument("--bot-id", required=True)
    dispose.add_argument("--triage-id", required=True)
    dispose.add_argument("--action", required=True, choices=sorted(DISPOSITION_ACTIONS))
    dispose.add_argument("--actor", required=True)
    dispose.add_argument("--note", default=None)
    dispose.add_argument("--task-id", default=None)
    dispose.set_defaults(func=cmd_dispose)

    report = subparsers.add_parser("report", help="render a human-readable report")
    report.add_argument("--project-root", default=Path("."), type=Path)
    report.add_argument("--bot-id", required=True)
    report.add_argument("--batch-id", action="append", default=None)
    report.add_argument("--source", choices=sorted(RESULT_SOURCES), default="agent")
    report.set_defaults(func=cmd_report)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())