#!/usr/bin/env python3
"""Dispatch ready triage batches to an idle Herdr agent pane."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bot_runtime import (
    FileLock,
    append_jsonl,
    bot_dir,
    read_bytes,
    read_json,
    state_dir,
    write_text,
)
from bot_schema import AGENT_KINDS
from bot_triage import (
    POLICY_VERSION,
    TriageError,
    batches_dir,
    claim_batch,
    apply_results,
    load_definition,
    lock_path,
    parse_results_file,
    render_report,
    triage_dir,
)

DEFAULT_ACK_TIMEOUT = 300
DEFAULT_LEASE_SECONDS = 900
ACTIVE_RECORD_STATES = {"sending", "sent", "applied", "timeout", "retry_sent"}


def dispatch_path(project_root: Path, bot_id: str) -> Path:
    return triage_dir(project_root, bot_id) / "dispatch.jsonl"


def _record(project_root: Path, bot_id: str, payload: dict[str, Any]) -> None:
    rows = _load_records(project_root, bot_id)
    if rows:
        prior = rows[-1]
        if all(
            prior.get(key) == payload.get(key)
            for key in ("batch_id", "state", "reason", "attempt_id", "pane_id")
        ):
            return
    append_jsonl(project_root, dispatch_path(project_root, bot_id), payload)


def _load_records(project_root: Path, bot_id: str) -> list[dict[str, Any]]:
    path = dispatch_path(project_root, bot_id)
    try:
        raw = read_bytes(project_root, path).decode("utf-8")
    except FileNotFoundError:
        return []
    rows: list[dict[str, Any]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _latest_records(project_root: Path, bot_id: str) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in _load_records(project_root, bot_id):
        batch_id = row.get("batch_id")
        if isinstance(batch_id, str):
            latest[batch_id] = row
    return latest


def _run_json(command: list[str]) -> tuple[dict[str, Any] | None, str]:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)
    if result.returncode != 0:
        return None, (result.stderr.strip() or f"exit {result.returncode}")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc}"
    if not isinstance(value, dict):
        return None, "JSON response is not an object"
    return value, ""


def _pane_labels(row: dict[str, Any]) -> set[str]:
    labels: set[str] = set()
    for key in ("label", "terminal_title_stripped", "terminal_title", "pane_id"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            labels.add(value.strip())
    return labels




def _pane_pids(herdr_bin: str, pane_id: str) -> set[int]:
    data, _ = _run_json([herdr_bin, "pane", "process-info", "--pane", pane_id])
    if data is None:
        return set()
    info = data.get("result", {}).get("process_info", {})
    if not isinstance(info, dict):
        return set()
    pids: set[int] = set()
    group = info.get("foreground_process_group_id")
    if isinstance(group, int) and not isinstance(group, bool):
        pids.add(group)
    for process in info.get("foreground_processes", []):
        if isinstance(process, dict):
            pid = process.get("pid")
            if isinstance(pid, int) and not isinstance(pid, bool):
                pids.add(pid)
    return pids


def _self_pane_ids(
    project_root: Path,
    bot_id: str,
    herdr_bin: str,
    candidates: list[dict[str, Any]],
) -> set[str]:
    self_ids: set[str] = set()
    env_pane = os.environ.get("HERDR_PANE_ID")
    if isinstance(env_pane, str) and env_pane.strip():
        self_ids.add(env_pane.strip())
    pane_pid: int | None = None
    try:
        raw = read_bytes(project_root, state_dir(project_root, bot_id) / "pane.pid")
        pane_pid = int(raw.decode("utf-8").strip())
    except (FileNotFoundError, OSError, RuntimeError, ValueError):
        pane_pid = None
    if pane_pid is not None:
        for row in candidates:
            pane_id = row.get("pane_id")
            if isinstance(pane_id, str) and pane_pid in _pane_pids(herdr_bin, pane_id):
                self_ids.add(pane_id)
    return self_ids


def _resolve_target(
    herdr_bin: str,
    workspace_label: str,
    cwd_marker: str,
    *,
    project_root: Path,
    bot_id: str,
    exclude_panes: list[str] | None = None,
    allow_pane_labels: list[str] | None = None,
) -> dict[str, Any]:
    workspaces, error = _run_json([herdr_bin, "workspace", "list"])
    if workspaces is None:
        return {"state": "unavailable", "reason": f"herdr unavailable: {error}"}
    workspace_rows = workspaces.get("result", {}).get("workspaces", [])
    if not isinstance(workspace_rows, list):
        return {"state": "unavailable", "reason": "herdr workspace list is malformed"}
    workspace_id = None
    for row in workspace_rows:
        if isinstance(row, dict) and row.get("label") == workspace_label:
            workspace_id = row.get("workspace_id")
            break
    if not isinstance(workspace_id, str) or not workspace_id:
        return {"state": "absent", "reason": f"workspace label not found: {workspace_label}"}

    panes, error = _run_json([herdr_bin, "pane", "list"])
    if panes is None:
        return {"state": "unavailable", "reason": f"herdr unavailable: {error}"}
    pane_rows = panes.get("result", {}).get("panes", [])
    if not isinstance(pane_rows, list):
        return {"state": "unavailable", "reason": "herdr pane list is malformed"}

    marker = Path(os.path.abspath(cwd_marker))
    matching: list[dict[str, Any]] = []
    for row in pane_rows:
        if not isinstance(row, dict) or row.get("workspace_id") != workspace_id:
            continue
        cwd = row.get("cwd")
        if not isinstance(cwd, str):
            continue
        try:
            pane_cwd = Path(os.path.abspath(cwd))
            matches = pane_cwd == marker or marker in pane_cwd.parents
        except OSError:
            matches = False
        if matches:
            matching.append(row)

    self_ids = _self_pane_ids(project_root, bot_id, herdr_bin, matching)
    excluded = {str(item).strip() for item in (exclude_panes or []) if str(item).strip()}
    allow_labels = [item.strip() for item in (allow_pane_labels or []) if item.strip()]
    idle: list[str] = []
    working: list[str] = []
    skipped_self: list[str] = []
    skipped_excluded: list[str] = []
    skipped_allowlist: list[str] = []
    skipped_non_agent: list[str] = []
    for row in matching:
        pane_id = row.get("pane_id")
        if not isinstance(pane_id, str) or not pane_id:
            continue
        labels = _pane_labels(row)
        if pane_id in self_ids:
            skipped_self.append(pane_id)
            continue
        if pane_id in excluded or labels & excluded:
            skipped_excluded.append(pane_id)
            continue
        if allow_labels and pane_id not in allow_labels and not (labels & set(allow_labels)):
            skipped_allowlist.append(pane_id)
            continue
        if row.get("agent_status") == "working":
            working.append(pane_id)
            continue
        if row.get("agent_status") != "idle":
            continue
        agent = row.get("agent")
        if not isinstance(agent, str) or agent not in AGENT_KINDS:
            skipped_non_agent.append(pane_id)
            continue
        idle.append(pane_id)

    if idle:
        return {"state": "idle", "pane_id": sorted(idle)[0], "workspace_id": workspace_id}
    if working:
        return {
            "state": "busy",
            "reason": f"matching pane is working: {', '.join(sorted(working))}",
        }
    if skipped_self:
        extra = (
            f"; also excluded by configuration: {', '.join(sorted(skipped_excluded))}"
            if skipped_excluded
            else ""
        )
        return {
            "state": "self_only",
            "reason": (
                "only the dispatching pane is available; "
                f"self: {', '.join(sorted(skipped_self))}{extra}"
            ),
        }
    if skipped_excluded:
        return {
            "state": "absent",
            "reason": (
                "no eligible pane; excluded by configuration: "
                f"{', '.join(sorted(skipped_excluded))}"
            ),
        }
    if skipped_allowlist:
        return {
            "state": "absent",
            "reason": f"no idle pane matches allow_pane_labels: {', '.join(allow_labels)}",
        }
    if skipped_non_agent:
        return {
            "state": "absent",
            "reason": (
                "no actionable agent pane (expected one of "
                f"{', '.join(sorted(AGENT_KINDS))}): {', '.join(sorted(skipped_non_agent))}"
            ),
        }
    return {"state": "absent", "reason": "no matching pane for workspace label and cwd"}


def _output_path(project_root: Path, bot_id: str, config: dict[str, Any], batch_id: str) -> Path:
    output_dir = config.get("output_dir", "state/triage/output")
    if not isinstance(output_dir, str) or not output_dir:
        output_dir = "state/triage/output"
    root = bot_dir(project_root, bot_id)
    target = (root / output_dir).resolve()
    if not target.is_relative_to(root.resolve()):
        raise TriageError("triage.dispatch.output_dir: path escapes the bot directory", 2)
    return target / f"{batch_id}.jsonl"


def _prompt(batch: dict[str, Any], batch_file: Path, output_file: Path) -> str:
    return (
        f"TRIAGE_BATCH {batch['batch_id']}\n"
        f"batch_file: {batch_file}\n"
        f"output_file: {output_file}\n"
        f"event_count: {batch.get('event_count', 0)}\n"
        f"policy: {POLICY_VERSION}\n"
        "Read batch_file. For every event write exactly one JSON object to output_file "
        "(JSONL), using the existing triage result contract. Classify the comment intent "
        "against the delivered work: correction_of_delivered_work (points out a defect, "
        "regression, contract mismatch, or rework), answer_to_my_question (answers a "
        "question we asked, including Q1/Q2 replies), clarification_request (asks us for "
        "a decision or detail), handoff_or_notice (handoff, acknowledgement, announcement, "
        "completion report), scope_or_contract_change (changes scope, contract, API, or "
        "error codes), needs_more_context (cannot determine). Use source=agent, a concrete "
        "rationale, and urgency high only for corrections or contract changes. Do not write "
        "any other file and do not perform external writes."
    )


def _release_claim(project_root: Path, bot_id: str, batch_id: str, attempt_id: str, reason: str) -> None:
    from bot_triage import _batch_path, _triage_lock

    try:
        with _triage_lock(project_root, bot_id):
            path = _batch_path(project_root, bot_id, batch_id)
            batch = read_json(project_root, path)
            if batch.get("status") == "claimed" and batch.get("attempt_id") == attempt_id:
                batch["status"] = "pending"
                batch["last_attempt_id"] = attempt_id
                batch["last_claim_release_reason"] = reason
                batch["attempt_id"] = None
                batch["claimed_at"] = None
                from bot_runtime import write_json

                write_json(project_root, path, batch)
    except Exception:
        return


def _ready_batches(project_root: Path, bot_id: str) -> list[dict[str, Any]]:
    root = batches_dir(project_root, bot_id)
    if not root.is_dir():
        return []
    batches: list[dict[str, Any]] = []
    for directory in sorted(root.iterdir()):
        if not directory.is_dir():
            continue
        try:
            batch = read_json(project_root, directory / "batch.json")
        except (FileNotFoundError, OSError, RuntimeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(batch, dict):
            continue
        if batch.get("triage_trigger") != "herdr_batch":
            continue
        if batch.get("status") != "pending":
            continue
        batches.append(batch)
    return batches


def _missing_events(batch: dict[str, Any], output: Path, project_root: Path) -> list[str]:
    try:
        rows = parse_results_file(output)
    except (TriageError, OSError, ValueError, json.JSONDecodeError):
        return []
    seen = {str(row.get("event_id")) for row in rows if isinstance(row, dict)}
    return [str(event_id) for event_id in batch.get("event_ids", []) if str(event_id) not in seen]


def _wait_for_output(
    project_root: Path,
    output: Path,
    *,
    timeout_seconds: int,
    sleep_seconds: float = 0.2,
) -> tuple[bool, str]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            data = read_bytes(project_root, output)
        except FileNotFoundError:
            data = b""
        except (OSError, RuntimeError) as exc:
            return False, f"cannot read agent output: {exc}"
        if data.strip():
            return True, ""
        time.sleep(sleep_seconds)
    return False, "agent output did not arrive before ack timeout"


def _send(
    herdr_bin: str,
    pane_id: str,
    prompt: str,
) -> tuple[bool, str]:
    command = [herdr_bin, "pane", "run", pane_id, prompt]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    if result.returncode != 0:
        return False, result.stderr.strip() or f"pane run exit {result.returncode}"
    return True, ""


def dispatch_ready_batches(
    project_root: Path,
    bot_id: str,
    *,
    herdr_bin: str | None = None,
    now: datetime | None = None,
    sleep_seconds: float = 0.2,
) -> list[dict[str, Any]]:
    definition = load_definition(project_root, bot_id)
    triage = definition.get("triage") if isinstance(definition.get("triage"), dict) else {}
    if triage.get("triage_trigger") != "herdr_batch":
        return [{"state": "disabled", "reason": "triage_trigger is not herdr_batch"}]
    config = triage.get("dispatch")
    if not isinstance(config, dict):
        return [{"state": "pending", "reason": "triage.dispatch is not configured"}]

    current = now or datetime.now(timezone.utc)
    bin_name = herdr_bin or config.get("herdr_bin") or "herdr"
    label = config.get("workspace_label")
    cwd_marker = config.get("cwd_marker")
    if not isinstance(bin_name, str) or not isinstance(label, str) or not isinstance(cwd_marker, str):
        return [{"state": "pending", "reason": "dispatch configuration is incomplete"}]
    if not os.path.isabs(cwd_marker):
        cwd_marker = str(project_root / cwd_marker)
    try:
        target = _resolve_target(
            bin_name,
            label,
            cwd_marker,
            project_root=project_root,
            bot_id=bot_id,
            exclude_panes=config.get("exclude_panes"),
            allow_pane_labels=config.get("allow_pane_labels"),
        )
    except OSError as exc:
        target = {"state": "unavailable", "reason": f"herdr unavailable: {exc}"}

    latest = _latest_records(project_root, bot_id)
    ready = _ready_batches(project_root, bot_id)
    results: list[dict[str, Any]] = []
    if not ready:
        for batch_id, prior in sorted(latest.items()):
            if prior.get("state") in ACTIVE_RECORD_STATES:
                results.append(
                    {"batch_id": batch_id, "state": "skipped", "reason": "already dispatched"}
                )
        return results
    for batch in ready:
        batch_id = str(batch["batch_id"])
        prior = latest.get(batch_id)
        if prior and prior.get("state") in ACTIVE_RECORD_STATES:
            results.append({"batch_id": batch_id, "state": "skipped", "reason": "already dispatched"})
            continue
        if target["state"] != "idle":
            payload = {
                "batch_id": batch_id,
                "state": "pending",
                "reason": target.get("reason", target["state"]),
                "workspace_label": label,
                "at": current.isoformat(),
            }
            _record(project_root, bot_id, payload)
            results.append(payload)
            continue

        attempt_id = f"att-{batch_id[-32:]}-{int(current.timestamp())}"
        try:
            claimed = claim_batch(project_root, bot_id, batch_id, attempt_id)
        except TriageError as exc:
            payload = {
                "batch_id": batch_id,
                "state": "skipped",
                "reason": str(exc),
                "at": current.isoformat(),
            }
            _record(project_root, bot_id, payload)
            results.append(payload)
            continue

        batch_file = batches_dir(project_root, bot_id) / batch_id / "batch.json"
        output_file = _output_path(project_root, bot_id, config, batch_id)
        try:
            write_text(project_root, output_file, "")
        except (OSError, RuntimeError) as exc:
            _release_claim(project_root, bot_id, batch_id, attempt_id, "output path unavailable")
            payload = {
                "batch_id": batch_id,
                "attempt_id": attempt_id,
                "state": "failed",
                "reason": f"cannot create output file: {exc}",
                "at": current.isoformat(),
            }
            _record(project_root, bot_id, payload)
            results.append(payload)
            continue
        prompt = _prompt(claimed, batch_file, output_file)
        sent, error = _send(
            bin_name,
            str(target["pane_id"]),
            prompt,
        )
        if not sent:
            _release_claim(project_root, bot_id, batch_id, attempt_id, "pane run failed")
            payload = {
                "batch_id": batch_id,
                "attempt_id": attempt_id,
                "pane_id": target["pane_id"],
                "state": "failed",
                "reason": error,
                "at": current.isoformat(),
            }
            _record(project_root, bot_id, payload)
            results.append(payload)
            continue

        timeout_seconds = int(config.get("ack_timeout_seconds", DEFAULT_ACK_TIMEOUT))
        arrived, wait_error = _wait_for_output(
            project_root,
            output_file,
            timeout_seconds=timeout_seconds,
            sleep_seconds=sleep_seconds,
        )
        missing: list[str] = []
        retried = False
        if not arrived:
            missing = _missing_events(claimed, output_file, project_root)
            if missing:
                retry_prompt = (
                    _prompt(claimed, batch_file, output_file)
                    + "\nRETRY_ONLY_MISSING: "
                    + ", ".join(missing)
                    + "\nAppend only results for those event_ids to the same output_file; "
                    "do not repeat already written events and do not modify other files."
                )
                retry_sent, retry_error = _send(
                    bin_name,
                    str(target["pane_id"]),
                    retry_prompt,
                )
                if retry_sent:
                    retried = True
                    arrived, wait_error = _wait_for_output(
                        project_root,
                        output_file,
                        timeout_seconds=timeout_seconds,
                        sleep_seconds=sleep_seconds,
                    )
                else:
                    wait_error = f"retry dispatch failed: {retry_error}"
            if not arrived:
                payload = {
                    "batch_id": batch_id,
                    "attempt_id": attempt_id,
                    "pane_id": target["pane_id"],
                    "state": "timeout",
                    "reason": wait_error,
                    "missing_event_ids": missing,
                    "retry_sent": retried,
                    "at": current.isoformat(),
                }
                _record(project_root, bot_id, payload)
                results.append(payload)
                continue

        try:
            incoming = parse_results_file(output_file)
            summary = apply_results(project_root, bot_id, batch_id, incoming)
            report = render_report(project_root, bot_id, [batch_id], only_batch=True, source="agent")
            payload = {
                "batch_id": batch_id,
                "attempt_id": attempt_id,
                "pane_id": target["pane_id"],
                "state": "applied",
                "classification_counts": _counts(incoming),
                "result_count": summary.get("accepted", 0),
                "report_lines": len(report.splitlines()) if report else 0,
                "retried_missing": retried,
                "at": current.isoformat(),
            }
        except (TriageError, OSError, ValueError, json.JSONDecodeError) as exc:
            _release_claim(project_root, bot_id, batch_id, attempt_id, "invalid agent output")
            payload = {
                "batch_id": batch_id,
                "attempt_id": attempt_id,
                "pane_id": target["pane_id"],
                "state": "invalid_output",
                "reason": str(exc),
                "at": current.isoformat(),
            }
        _record(project_root, bot_id, payload)
        results.append(payload)
    return results


def _counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        key = str(row.get("classification"))
        counts[key] = counts.get(key, 0) + 1
    return counts


def cmd_dispatch(args: Any) -> int:
    while True:
        try:
            results = dispatch_ready_batches(
                args.project_root.resolve(),
                args.bot_id,
                herdr_bin=args.herdr_bin,
            )
        except TriageError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return exc.exit_code
        for row in results:
            print(json.dumps(row, ensure_ascii=False, sort_keys=True), flush=True)
        failed = any(
            row.get("state") in {"failed", "invalid_output", "timeout"} for row in results
        )
        if not args.watch:
            return 5 if failed else 0
        time.sleep(max(1.0, float(args.interval_seconds)))