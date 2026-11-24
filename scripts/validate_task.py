#!/usr/bin/env python3
"""Validate an agent-collab current-task.json file using only the stdlib."""

import argparse
import json
import sys
from pathlib import Path

SCHEMA_VERSION = 1
STATUSES = {"draft", "confirmed", "in_progress", "blocked", "pending_acceptance", "done"}
DISPATCH_STATUSES = {"confirmed", "in_progress"}
ACCEPTANCE_KINDS = {"technical", "business"}


def _nonempty_string(value):
    return isinstance(value, str) and bool(value.strip())


def _require_nonempty(data, field, errors):
    if not _nonempty_string(data.get(field)):
        errors.append(f"{field}: must be a non-empty string")


def _validate_string_list(data, field, errors):
    value = data.get(field)
    if not isinstance(value, list):
        errors.append(f"{field}: must be an array")
        return
    for index, item in enumerate(value):
        if not isinstance(item, str):
            errors.append(f"{field}[{index}]: must be a string")
        elif not item.strip():
            errors.append(f"{field}[{index}]: must be a non-empty string")


def validate_task(data, phase="draft"):
    """Return a list of human-readable validation errors."""
    errors = []
    if not isinstance(data, dict):
        return ["root: must be a JSON object"]

    required = {
        "schema_version",
        "task_id",
        "goal_ref",
        "goal",
        "scope",
        "non_goals",
        "acceptance",
        "decision_boundaries",
        "dependencies",
        "risks",
        "status",
    }
    for field in sorted(required - set(data)):
        errors.append(f"{field}: required field is missing")

    schema_version = data.get("schema_version")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != SCHEMA_VERSION
    ):
        errors.append(f"schema_version: must be the integer {SCHEMA_VERSION}")

    for field in ("task_id", "goal_ref", "goal", "status"):
        if field in data and not isinstance(data[field], str):
            errors.append(f"{field}: must be a string")

    status = data.get("status")
    if isinstance(status, str) and status not in STATUSES:
        errors.append(f"status: must be one of {', '.join(sorted(STATUSES))}")

    for field in ("scope", "non_goals", "dependencies", "risks"):
        if field in data:
            _validate_string_list(data, field, errors)

    boundaries = data.get("decision_boundaries")
    if not isinstance(boundaries, dict):
        errors.append("decision_boundaries: must be an object")
    else:
        for field in ("autonomous", "requires_user"):
            if field not in boundaries:
                errors.append(f"decision_boundaries.{field}: required field is missing")
            else:
                value = boundaries[field]
                if not isinstance(value, list):
                    errors.append(f"decision_boundaries.{field}: must be an array")
                else:
                    for index, item in enumerate(value):
                        if not isinstance(item, str):
                            errors.append(
                                f"decision_boundaries.{field}[{index}]: must be a string"
                            )
                        elif not item.strip():
                            errors.append(
                                f"decision_boundaries.{field}[{index}]: "
                                "must be a non-empty string"
                            )

    acceptance = data.get("acceptance")
    acceptance_ids = set()
    if not isinstance(acceptance, list):
        errors.append("acceptance: must be an array")
        acceptance = []

    for index, item in enumerate(acceptance):
        prefix = f"acceptance[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{prefix}: must be an object")
            continue
        for field in ("id", "statement", "kind", "expected", "owner"):
            if field not in item:
                errors.append(f"{prefix}.{field}: required field is missing")
            elif not isinstance(item[field], str):
                errors.append(f"{prefix}.{field}: must be a string")
        item_id = item.get("id")
        if _nonempty_string(item_id):
            if item_id in acceptance_ids:
                errors.append(f"{prefix}.id: duplicate acceptance id {item_id!r}")
            acceptance_ids.add(item_id)
        kind = item.get("kind")
        if isinstance(kind, str) and kind not in ACCEPTANCE_KINDS:
            errors.append(f"{prefix}.kind: must be technical or business")
        if "command" in item and not isinstance(item["command"], str):
            errors.append(f"{prefix}.command: must be a string")

    if phase == "dispatch":
        for field in ("task_id", "goal_ref", "goal"):
            _require_nonempty(data, field, errors)
        if status not in DISPATCH_STATUSES:
            errors.append("status: dispatch requires confirmed or in_progress")
        scope = data.get("scope")
        if not isinstance(scope, list) or not any(
            _nonempty_string(entry) for entry in scope
        ):
            errors.append("scope: dispatch requires at least one non-blank scope entry")
        if not acceptance:
            errors.append("acceptance: dispatch requires at least one acceptance item")
        elif not any(
            isinstance(item, dict) and item.get("kind") == "business"
            for item in acceptance
        ):
            errors.append(
                "acceptance: dispatch requires at least one business acceptance item"
            )
        for index, item in enumerate(acceptance):
            if not isinstance(item, dict):
                continue
            prefix = f"acceptance[{index}]"
            for field in ("id", "statement", "expected", "owner"):
                if not _nonempty_string(item.get(field)):
                    errors.append(f"{prefix}.{field}: dispatch requires a non-empty string")
            if item.get("kind") == "technical" and not _nonempty_string(item.get("command")):
                errors.append(f"{prefix}.command: technical acceptance requires an executable command")

    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="Path to current-task.json")
    parser.add_argument(
        "--phase",
        choices=("draft", "dispatch"),
        default="draft",
        help="draft checks structure; dispatch also checks goal, scope, and acceptance readiness",
    )
    args = parser.parse_args(argv)

    try:
        with args.path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {args.path}: {exc}", file=sys.stderr)
        return 2

    errors = validate_task(data, args.phase)
    if errors:
        for error in errors:
            print(f"ERROR: {args.path}: {error}", file=sys.stderr)
        return 1

    print(f"OK: {args.path} ({args.phase})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
