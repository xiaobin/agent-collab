#!/usr/bin/env python3
"""Validation helpers for the stage-one bot definition and project policy."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BOT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
SCHEMA_VERSION = 1
AUTONOMY_LEVELS = {"L0": 0, "L1": 1, "L2": 2, "L3": 3, "L4": 4}
RUNTIME_KINDS = {"process", "agent_batch"}
ADAPTER_NAMES = {"fake", "feishu"}
OUTPUT_TYPES = {"herdr_panel", "local_file"}
AGENT_KINDS = {"claude", "pi", "codex"}
CONCURRENCY_MODES = {"forbid", "queue", "replace"}
MEMORY_MODES = {"private", "shared", "team"}
TRIAGE_MODES = {"suggest_only"}
TRIAGE_TRIGGERS = {"manual", "herdr_batch"}
DISPATCH_KEYS = {
    "mode",
    "workspace_label",
    "cwd_marker",
    "herdr_bin",
    "ack_timeout_seconds",
    "output_dir",
    "exclude_panes",
    "allow_pane_labels",
}

DEFINITION_KEYS = {
    "schema_version",
    "bot_id",
    "revision",
    "goal_ref",
    "display_name",
    "description",
    "identity",
    "adapter",
    "runtime",
    "workspace",
    "permissions",
    "triggers",
    "triage",
    "outputs",
    "memory",
    "health",
    "escalation",
}
DEFINITION_OPTIONAL_KEYS = {
    # How the operator wants this bot to run: a long-lived --resident process,
    # or one --once pass that exits. Recorded in the definition so the choice
    # is covered by the approval snapshot.
    "run_mode",
}
RUN_MODES = {"once", "resident"}
POLICY_KEYS = {
    "schema_version",
    "allowed_adapters",
    "allowed_runtimes",
    "allowed_agent_kinds",
    "allowed_outputs",
    "max_bots",
    "max_active_bots",
    "min_interval_seconds",
    "max_autonomy",
    "require_approval_for_start",
    "allow_network_read",
    "allow_network_write",
    "data_classification",
    "state_retention_default_days",
}


@dataclass
class ValidationResult:
    definition: dict[str, Any] | None = None
    policy: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def definition_digest(definition: dict[str, Any]) -> str:
    payload = json.dumps(
        definition, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _load_json(path: Path, label: str, result: ValidationResult) -> dict[str, Any] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        result.errors.append(f"{label}: cannot read {path}: {exc}")
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        result.errors.append(f"{label}: invalid JSON at line {exc.lineno}: {exc.msg}")
        return None
    if not isinstance(value, dict):
        result.errors.append(f"{label}: top-level value must be an object")
        return None
    return value


def _prefix(where: str) -> str:
    return f"{where}." if where else ""


def _check_keys(
    value: dict[str, Any], allowed: set[str], where: str, result: ValidationResult
) -> None:
    for key in sorted(set(value) - allowed):
        result.errors.append(f"{_prefix(where)}{key}: unknown field")


def _require(
    value: dict[str, Any], keys: list[str], where: str, result: ValidationResult
) -> bool:
    ok = True
    for key in keys:
        if key not in value:
            result.errors.append(f"{_prefix(where)}{key}: field is required")
            ok = False
    return ok


def _string(
    value: dict[str, Any],
    key: str,
    where: str,
    result: ValidationResult,
    *,
    allow_empty: bool = False,
    allow_null: bool = False,
) -> None:
    if key not in value:
        return
    item = value[key]
    if item is None and allow_null:
        return
    if not isinstance(item, str):
        result.errors.append(f"{where}.{key}: must be a string")
    elif not allow_empty and not item.strip():
        result.errors.append(f"{where}.{key}: must not be empty")


def _integer(
    value: dict[str, Any],
    key: str,
    where: str,
    result: ValidationResult,
    *,
    minimum: int | None = None,
) -> None:
    if key not in value:
        return
    item = value[key]
    if isinstance(item, bool) or not isinstance(item, int):
        result.errors.append(f"{where}.{key}: must be an integer")
    elif minimum is not None and item < minimum:
        result.errors.append(f"{where}.{key}: must be >= {minimum}")


def _boolean(value: dict[str, Any], key: str, where: str, result: ValidationResult) -> None:
    if key in value and not isinstance(value[key], bool):
        result.errors.append(f"{where}.{key}: must be true or false")


def _list_of_strings(value: Any, where: str, result: ValidationResult) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        result.errors.append(f"{where}: must be a list of strings")
        return []
    return value


def _relative_project_path(
    project_root: Path, value: str, where: str, result: ValidationResult
) -> Path | None:
    candidate = Path(value)
    if candidate.is_absolute():
        result.errors.append(f"{where}: must be project-relative, not absolute")
        return None
    root = project_root.resolve()
    target = (root / candidate).resolve()
    if not target.is_relative_to(root):
        result.errors.append(f"{where}: path escapes the project root")
        return None
    return target


def _inside(parent: Path, child: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _validate_identity(value: Any, result: ValidationResult) -> None:
    if not isinstance(value, dict):
        result.errors.append("identity: must be an object")
        return
    _check_keys(value, {"instructions_ref", "owner_ref", "tags"}, "identity", result)
    _require(value, ["owner_ref", "tags"], "identity", result)
    _string(value, "instructions_ref", "identity", result, allow_null=True)
    _string(value, "owner_ref", "identity", result)
    if "tags" in value:
        _list_of_strings(value["tags"], "identity.tags", result)


def _validate_adapter(
    value: Any, project_root: Path, result: ValidationResult
) -> None:
    if not isinstance(value, dict):
        result.errors.append("adapter: must be an object")
        return
    _check_keys(value, {"name", "version", "config_ref"}, "adapter", result)
    if not _require(value, ["name", "version", "config_ref"], "adapter", result):
        return
    _string(value, "name", "adapter", result)
    _string(value, "version", "adapter", result)
    _string(value, "config_ref", "adapter", result)
    name = value.get("name")
    if isinstance(name, str) and name not in ADAPTER_NAMES:
        result.errors.append(
            f"adapter.name: unsupported adapter {name!r}; allowed: {sorted(ADAPTER_NAMES)}"
        )
    config_ref = value.get("config_ref")
    if isinstance(config_ref, str) and config_ref:
        target = _relative_project_path(
            project_root, config_ref, "adapter.config_ref", result
        )
        if target is not None and not target.is_file():
            result.errors.append(f"adapter.config_ref: file not found: {config_ref}")
        elif target is not None and target.is_file():
            adapter_config = _load_json(target, "adapter.config_ref", result)
            if adapter_config is not None and name == "fake" and "scenario" not in adapter_config:
                result.errors.append("adapter.config_ref: scenario is required for the fake adapter")


def _validate_runtime(value: Any, result: ValidationResult) -> None:
    if not isinstance(value, dict):
        result.errors.append("runtime: must be an object")
        return
    _check_keys(
        value,
        {
            "kind",
            "agent",
            "concurrency",
            "lock_ttl_seconds",
            "heartbeat_seconds",
        },
        "runtime",
        result,
    )
    if not _require(
        value,
        [
            "kind",
            "agent",
            "concurrency",
            "lock_ttl_seconds",
            "heartbeat_seconds",
        ],
        "runtime",
        result,
    ):
        return
    kind = value.get("kind")
    if kind not in RUNTIME_KINDS:
        result.errors.append(
            f"runtime.kind: must be one of {sorted(RUNTIME_KINDS)}"
        )
    concurrency = value.get("concurrency")
    if concurrency not in CONCURRENCY_MODES:
        result.errors.append(
            f"runtime.concurrency: must be one of {sorted(CONCURRENCY_MODES)}"
        )
    _integer(value, "lock_ttl_seconds", "runtime", result, minimum=1)
    _integer(value, "heartbeat_seconds", "runtime", result, minimum=1)
    agent = value.get("agent")
    if kind == "agent_batch":
        if not isinstance(agent, dict):
            result.errors.append(
                "runtime.agent: required when runtime.kind is agent_batch"
            )
        else:
            _validate_agent(agent, result)
    elif agent is not None:
        result.errors.append("runtime.agent: only allowed for agent_batch")


def _validate_agent(value: dict[str, Any], result: ValidationResult) -> None:
    _check_keys(
        value,
        {
            "kind",
            "model_profile_ref",
            "session_mode",
            "timeout_seconds",
            "max_agent_calls_per_day",
            "output_schema_ref",
            "permission_profile",
        },
        "runtime.agent",
        result,
    )
    if not _require(
        value,
        [
            "kind",
            "model_profile_ref",
            "session_mode",
            "timeout_seconds",
            "max_agent_calls_per_day",
            "permission_profile",
        ],
        "runtime.agent",
        result,
    ):
        return
    if value.get("kind") not in AGENT_KINDS:
        result.errors.append(
            f"runtime.agent.kind: must be one of {sorted(AGENT_KINDS)}"
        )
    _string(value, "model_profile_ref", "runtime.agent", result)
    _integer(value, "timeout_seconds", "runtime.agent", result, minimum=1)
    _integer(value, "max_agent_calls_per_day", "runtime.agent", result, minimum=1)


def _validate_workspace(
    value: Any,
    project_root: Path,
    bot_id: str,
    result: ValidationResult,
) -> None:
    if not isinstance(value, dict):
        result.errors.append("workspace: must be an object")
        return
    _check_keys(value, {"root", "read_scope", "write_scope"}, "workspace", result)
    if not _require(value, ["root", "read_scope", "write_scope"], "workspace", result):
        return
    _string(value, "root", "workspace", result)
    root_text = value.get("root")
    if isinstance(root_text, str) and root_text:
        root = _relative_project_path(project_root, root_text, "workspace.root", result)
        allowed_root = (project_root / ".herdr" / "bots" / bot_id).resolve()
        if root is not None and not _inside(allowed_root, root):
            result.errors.append(
                f"workspace.root: must be under .herdr/bots/{bot_id}"
            )
    for key in ("read_scope", "write_scope"):
        if key not in value:
            continue
        scope = _list_of_strings(value[key], f"workspace.{key}", result)
        for index, path in enumerate(scope):
            _relative_project_path(
                project_root, path, f"workspace.{key}[{index}]", result
            )


def _validate_permissions(
    value: Any, project_root: Path, result: ValidationResult
) -> None:
    if not isinstance(value, dict):
        result.errors.append("permissions: must be an object")
        return
    _check_keys(
        value,
        {
            "autonomy_ceiling",
            "project_write_scope",
            "network_read",
            "network_write",
            "external_actions",
            "bot_administration",
            "commands",
            "secret_refs",
        },
        "permissions",
        result,
    )
    if not _require(
        value,
        [
            "autonomy_ceiling",
            "project_write_scope",
            "network_read",
            "network_write",
            "external_actions",
            "bot_administration",
            "commands",
            "secret_refs",
        ],
        "permissions",
        result,
    ):
        return
    if value.get("autonomy_ceiling") not in AUTONOMY_LEVELS:
        result.errors.append("permissions.autonomy_ceiling: must be one of L0-L4")
    scope = _list_of_strings(
        value.get("project_write_scope"), "permissions.project_write_scope", result
    )
    for index, path in enumerate(scope):
        _relative_project_path(
            project_root,
            path,
            f"permissions.project_write_scope[{index}]",
            result,
        )
    _boolean(value, "network_read", "permissions", result)
    _boolean(value, "network_write", "permissions", result)
    if value.get("external_actions") not in {
        "deny",
        "allowlisted",
        "require_user_approval",
    }:
        result.errors.append(
            "permissions.external_actions: must be deny, allowlisted, or require_user_approval"
        )
    if value.get("bot_administration") not in {"deny", "allowlisted"}:
        result.errors.append(
            "permissions.bot_administration: must be deny or allowlisted"
        )
    _list_of_strings(value.get("commands"), "permissions.commands", result)
    _list_of_strings(value.get("secret_refs"), "permissions.secret_refs", result)


def _validate_triggers(value: Any, result: ValidationResult) -> None:
    if not isinstance(value, list) or not value:
        result.errors.append("triggers: must be a non-empty list")
        return
    for index, trigger in enumerate(value):
        where = f"triggers[{index}]"
        if not isinstance(trigger, dict):
            result.errors.append(f"{where}: must be an object")
            continue
        _check_keys(trigger, {"type", "interval_seconds", "skip_if_running"}, where, result)
        if not _require(trigger, ["type"], where, result):
            continue
        if trigger.get("type") not in {"manual", "interval", "cron"}:
            result.errors.append(f"{where}.type: must be manual, interval, or cron")
        if trigger.get("type") == "interval":
            _integer(trigger, "interval_seconds", where, result, minimum=1)
        _boolean(trigger, "skip_if_running", where, result)


def _validate_triage(value: Any, result: ValidationResult) -> None:
    if not isinstance(value, dict):
        result.errors.append("triage: must be an object")
        return
    _check_keys(
        value,
        {
            "mode",
            "batch_interval_seconds",
            "max_batch_events",
            "max_context_events",
            "triage_trigger",
            "human_report_max_items_per_group",
            "human_report_max_lines",
            "dispatch",
        },
        "triage",
        result,
    )
    if value.get("mode") not in TRIAGE_MODES:
        result.errors.append("triage.mode: stage one supports only suggest_only")
    _integer(value, "batch_interval_seconds", "triage", result, minimum=60)
    _integer(value, "max_batch_events", "triage", result, minimum=1)
    _integer(value, "max_context_events", "triage", result, minimum=1)
    trigger = value.get("triage_trigger")
    if trigger is not None and trigger not in TRIAGE_TRIGGERS:
        result.errors.append("triage.triage_trigger: must be manual or herdr_batch")
    _integer(value, "human_report_max_items_per_group", "triage", result, minimum=1)
    _integer(value, "human_report_max_lines", "triage", result, minimum=1)
    if "dispatch" in value:
        _validate_dispatch(value["dispatch"], result)


def _validate_dispatch(value: Any, result: ValidationResult) -> None:
    if not isinstance(value, dict):
        result.errors.append("triage.dispatch: must be an object")
        return
    _check_keys(value, DISPATCH_KEYS, "triage.dispatch", result)
    if not _require(
        value, ["mode", "workspace_label", "cwd_marker"], "triage.dispatch", result
    ):
        return
    if value.get("mode") != "herdr":
        result.errors.append("triage.dispatch.mode: must be herdr")
    label = value.get("workspace_label")
    if isinstance(label, str):
        if not label.strip():
            result.errors.append("triage.dispatch.workspace_label: must not be empty")
        if ":" in label or re.search(r"(?i)\bw\d+:p\d+\b", label):
            result.errors.append(
                "triage.dispatch.workspace_label: use a workspace label, not a pane id"
            )
    else:
        result.errors.append("triage.dispatch.workspace_label: must be a string")
    _string(value, "cwd_marker", "triage.dispatch", result)
    _string(value, "herdr_bin", "triage.dispatch", result, allow_empty=False)
    _string(value, "output_dir", "triage.dispatch", result, allow_empty=False)
    output_dir = value.get("output_dir")
    if isinstance(output_dir, str) and output_dir:
        candidate = Path(output_dir)
        if candidate.is_absolute() or ".." in candidate.parts:
            result.errors.append(
                "triage.dispatch.output_dir: must be a relative path inside the bot directory"
            )
    _integer(value, "ack_timeout_seconds", "triage.dispatch", result, minimum=1)
    for key in ("exclude_panes", "allow_pane_labels"):
        item = value.get(key)
        if item is None:
            continue
        if not isinstance(item, list) or not all(
            isinstance(entry, str) and entry.strip() for entry in item
        ):
            result.errors.append(f"triage.dispatch.{key}: must be a list of non-empty strings")


def _workspace_root(definition: dict[str, Any]) -> str | None:
    workspace = definition.get("workspace")
    if not isinstance(workspace, dict):
        return None
    root = workspace.get("root")
    return root if isinstance(root, str) and root else None


def _validate_outputs(
    value: Any, project_root: Path, workspace_root: str | None, result: ValidationResult
) -> None:
    if not isinstance(value, list) or not value:
        result.errors.append("outputs: must be a non-empty list")
        return
    for index, output in enumerate(value):
        where = f"outputs[{index}]"
        if not isinstance(output, dict):
            result.errors.append(f"{where}: must be an object")
            continue
        _check_keys(output, {"type", "format", "path"}, where, result)
        if not _require(output, ["type"], where, result):
            continue
        output_type = output.get("type")
        if output_type not in OUTPUT_TYPES:
            result.errors.append(f"{where}.type: must be herdr_panel or local_file")
        _string(output, "format", where, result)
        if output_type == "local_file":
            if not _require(output, ["path"], where, result):
                continue
            _string(output, "path", where, result)
            path_text = output.get("path")
            if isinstance(path_text, str) and path_text and workspace_root:
                target = _relative_project_path(
                    project_root, path_text, f"{where}.path", result
                )
                workspace = (project_root / workspace_root).resolve()
                if target is not None and not _inside(workspace, target):
                    result.errors.append(f"{where}.path: must be inside workspace.root")


def _validate_memory(
    value: Any, project_root: Path, workspace_root: str | None, result: ValidationResult
) -> None:
    if not isinstance(value, dict):
        result.errors.append("memory: must be an object")
        return
    _check_keys(value, {"mode", "path", "retention_days", "max_bytes"}, "memory", result)
    if not _require(value, ["mode", "path", "retention_days", "max_bytes"], "memory", result):
        return
    if value.get("mode") not in MEMORY_MODES:
        result.errors.append("memory.mode: must be private, shared, or team")
    _string(value, "path", "memory", result)
    _integer(value, "retention_days", "memory", result, minimum=1)
    _integer(value, "max_bytes", "memory", result, minimum=1)
    path_text = value.get("path")
    if isinstance(path_text, str) and path_text and workspace_root:
        target = _relative_project_path(project_root, path_text, "memory.path", result)
        workspace = (project_root / workspace_root).resolve()
        if target is not None and not _inside(workspace, target):
            result.errors.append("memory.path: must be inside workspace.root")


def _validate_health(value: Any, result: ValidationResult) -> None:
    if not isinstance(value, dict):
        result.errors.append("health: must be an object")
        return
    _check_keys(
        value, {"stale_after_seconds", "max_consecutive_failures"}, "health", result
    )
    _integer(value, "stale_after_seconds", "health", result, minimum=1)
    _integer(value, "max_consecutive_failures", "health", result, minimum=1)


def _validate_escalation(value: Any, result: ValidationResult) -> None:
    if not isinstance(value, dict):
        result.errors.append("escalation: must be an object")
        return
    _check_keys(
        value,
        {"needs_user_route", "quiet_hours", "max_items_per_group"},
        "escalation",
        result,
    )
    if value.get("needs_user_route") not in {"herdr_panel", "local_file", "none"}:
        result.errors.append(
            "escalation.needs_user_route: must be herdr_panel, local_file, or none"
        )
    quiet = value.get("quiet_hours")
    if quiet is not None and not isinstance(quiet, dict):
        result.errors.append("escalation.quiet_hours: must be null or an object")
    _integer(value, "max_items_per_group", "escalation", result, minimum=1)


def _validate_policy(
    policy: dict[str, Any], definition: dict[str, Any], result: ValidationResult
) -> None:
    _check_keys(policy, POLICY_KEYS, "policy", result)
    _require(policy, sorted(POLICY_KEYS), "policy", result)
    for key in (
        "allowed_adapters",
        "allowed_runtimes",
        "allowed_agent_kinds",
        "allowed_outputs",
    ):
        if key in policy and (
            not isinstance(policy[key], list)
            or any(not isinstance(item, str) or not item.strip() for item in policy[key])
        ):
            result.errors.append(f"policy.{key}: must be a list of non-empty strings")
    if policy.get("max_autonomy") not in AUTONOMY_LEVELS:
        result.errors.append("policy.max_autonomy: must be one of L0, L1, L2, L3, L4")
    for key in (
        "max_bots",
        "max_active_bots",
        "min_interval_seconds",
        "state_retention_default_days",
    ):
        _integer(policy, key, "policy", result, minimum=0)
    _boolean(policy, "require_approval_for_start", "policy", result)
    _boolean(policy, "allow_network_read", "policy", result)
    _boolean(policy, "allow_network_write", "policy", result)
    if result.errors:
        return
    adapter = definition.get("adapter", {})
    runtime = definition.get("runtime", {})
    permissions = definition.get("permissions", {})
    if adapter.get("name") not in policy.get("allowed_adapters", []):
        result.errors.append(
            f"adapter.name: {adapter.get('name')!r} is not allowed by bot-policy"
        )
    if runtime.get("kind") not in policy.get("allowed_runtimes", []):
        result.errors.append(
            f"runtime.kind: {runtime.get('kind')!r} is not allowed by bot-policy"
        )
    for index, output in enumerate(definition.get("outputs", [])):
        if not isinstance(output, dict):
            continue
        if output.get("type") not in policy.get("allowed_outputs", []):
            result.errors.append(
                f"outputs[{index}].type: {output.get('type')!r} is not allowed by bot-policy"
            )
    agent = runtime.get("agent")
    if isinstance(agent, dict) and agent.get("kind") not in policy.get(
        "allowed_agent_kinds", []
    ):
        result.errors.append(
            f"runtime.agent.kind: {agent.get('kind')!r} is not allowed by bot-policy"
        )
    ceiling = permissions.get("autonomy_ceiling")
    max_autonomy = policy.get("max_autonomy")
    if (
        ceiling in AUTONOMY_LEVELS
        and max_autonomy in AUTONOMY_LEVELS
        and AUTONOMY_LEVELS[ceiling] > AUTONOMY_LEVELS[max_autonomy]
    ):
        result.errors.append(
            f"permissions.autonomy_ceiling: {ceiling} exceeds policy maximum {max_autonomy}"
        )
    if permissions.get("network_read") and not policy.get("allow_network_read"):
        result.errors.append("permissions.network_read: is not allowed by bot-policy")
    if permissions.get("network_write") and not policy.get("allow_network_write"):
        result.errors.append("permissions.network_write: is not allowed by bot-policy")
    minimum = policy.get("min_interval_seconds")
    if isinstance(minimum, int):
        for index, trigger in enumerate(definition.get("triggers", [])):
            if not isinstance(trigger, dict):
                continue
            interval = trigger.get("interval_seconds")
            if isinstance(interval, int) and interval < minimum:
                result.errors.append(
                    f"triggers[{index}].interval_seconds: below policy minimum of {minimum}"
                )


def validate_definition(
    definition_path: Path, project_root: Path
) -> ValidationResult:
    result = ValidationResult()
    definition = _load_json(definition_path, "definition", result)
    if definition is None:
        return result
    result.definition = definition

    _check_keys(definition, DEFINITION_KEYS | DEFINITION_OPTIONAL_KEYS, "", result)
    if not _require(
        definition,
        [
            "schema_version",
            "bot_id",
            "revision",
            "goal_ref",
            "display_name",
            "adapter",
            "runtime",
            "workspace",
            "permissions",
            "triggers",
            "triage",
            "outputs",
            "memory",
            "health",
            "escalation",
        ],
        "",
        result,
    ):
        return result
    if definition.get("schema_version") != SCHEMA_VERSION:
        result.errors.append(
            f"schema_version: must be {SCHEMA_VERSION}"
        )
    _string(definition, "bot_id", "", result)
    bot_id = definition.get("bot_id")
    if isinstance(bot_id, str) and bot_id and not BOT_ID_PATTERN.match(bot_id):
        result.errors.append(
            "bot_id: must use lowercase letters, digits, and hyphens only"
        )
    _integer(definition, "revision", "", result, minimum=1)
    _string(definition, "goal_ref", "", result)
    _string(definition, "display_name", "", result)
    _string(definition, "description", "", result)
    if "run_mode" in definition and definition["run_mode"] not in RUN_MODES:
        result.errors.append("run_mode: must be once or resident")

    if "identity" in definition:
        _validate_identity(definition["identity"], result)
    _validate_adapter(definition.get("adapter"), project_root, result)
    _validate_runtime(definition.get("runtime"), result)
    _validate_workspace(
        definition.get("workspace"), project_root, str(bot_id or ""), result
    )
    _validate_permissions(definition.get("permissions"), project_root, result)
    _validate_triggers(definition.get("triggers"), result)
    _validate_triage(definition.get("triage"), result)
    workspace_root = _workspace_root(definition)
    _validate_outputs(
        definition.get("outputs"),
        project_root,
        workspace_root,
        result,
    )
    _validate_memory(
        definition.get("memory"),
        project_root,
        workspace_root,
        result,
    )
    _validate_health(definition.get("health"), result)
    _validate_escalation(definition.get("escalation"), result)

    policy_path = project_root / ".herdr" / "bot-policy.json"
    if not policy_path.is_file():
        result.warnings.append(
            f"policy not found at {policy_path}; structural validation only"
        )
        return result
    policy = _load_json(policy_path, "policy", result)
    if policy is None:
        return result
    result.policy = policy
    if not result.errors:
        _validate_policy(policy, definition, result)
    return result