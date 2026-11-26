"""Deterministic local adapter used by stage-one tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base import Adapter, AdapterError, PollResult


class FakeAdapter(Adapter):
    def poll(
        self,
        *,
        cursor: Any,
        config: dict[str, Any],
        project_root: Path,
        workspace_root: Path | None = None,
        read_scope: list[str] | None = None,
    ) -> PollResult:
        scenario = config.get("scenario", "ok")
        if scenario == "auth_expired":
            raise AdapterError("authentication_expired", "fake adapter: authentication expired; re-authenticate before retrying", exit_code=6)
        if scenario == "partial_failure":
            raise AdapterError("partial_failure", "fake adapter: partial failure; cursor is not advanced", exit_code=5)
        if scenario == "upstream_error":
            raise AdapterError("upstream_error", "fake adapter: server unreachable", exit_code=5)
        if scenario != "ok":
            raise AdapterError("unsupported_scenario", f"fake adapter: unsupported scenario {scenario!r}", exit_code=5)

        fixture_ref = config.get("fixture_path")
        if not isinstance(fixture_ref, str) or not fixture_ref:
            raise AdapterError("invalid_config", "fake adapter: fixture_path is required", exit_code=2)
        fixture_path = (project_root / fixture_ref).resolve()
        if not fixture_path.is_relative_to(project_root.resolve()):
            raise AdapterError("invalid_config", "fake adapter: fixture_path escapes the project root", exit_code=2)
        allowed_roots = [workspace_root.resolve()] if workspace_root else []
        allowed_roots.extend((project_root / item).resolve() for item in (read_scope or []))
        if allowed_roots and not any(fixture_path.is_relative_to(root) for root in allowed_roots):
            raise AdapterError("read_scope_denied", "fake adapter: fixture_path is outside workspace/read_scope", exit_code=5)
        try:
            payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise AdapterError("invalid_fixture", f"fake adapter: cannot read fixture: {exc}", exit_code=5) from exc
        except json.JSONDecodeError as exc:
            raise AdapterError("invalid_fixture", f"fake adapter: invalid fixture JSON: {exc}", exit_code=5) from exc
        if not isinstance(payload, dict):
            raise AdapterError("invalid_fixture", "fake adapter: fixture must be a JSON object", exit_code=5)
        events = payload.get("events", [])
        if not isinstance(events, list):
            raise AdapterError("invalid_fixture", "fake adapter: events must be a list", exit_code=5)
        for event in events:
            event_id = event.get("event_id") if isinstance(event, dict) else None
            if not isinstance(event_id, str) or not event_id.strip() or len(event_id) > 256:
                raise AdapterError("invalid_fixture", "fake adapter: every event needs a non-empty event_id", exit_code=5)
        return PollResult(events=events, next_cursor=payload.get("next_cursor"))
