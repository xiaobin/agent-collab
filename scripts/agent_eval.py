#!/usr/bin/env python3
"""Validate agent evaluation records and roll them up using only the stdlib.

Commands:
  check RECORD.json [--final]
  rollup RECORDS.jsonl [--min-samples N]

``check`` exit codes:
  0  the record is structurally valid and its quality outcome is ``pass``
  1  the record is structurally valid but its outcome is ``fail`` or
     ``insufficient_evidence``
  2  malformed JSON, schema violations, unreadable input, or CLI errors

``rollup`` exit codes:
  0  a rollup was produced (including an empty one)
  2  malformed JSONL, schema violations, duplicate ``evaluation_id``, invalid
     ``correction_of`` links, a ``fail`` record without findings, or CLI errors

Corrections are append-only: a record with ``correction_of`` points at the
record it supersedes.  Rollup resolves each correction chain and only counts
the newest record in every chain.

The frozen structural contract lives in ``templates/agent-evaluation.json``.
Optional metrics that are absent stay ``null`` in the output; they are never
zero-filled.
"""

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path

SCHEMA_VERSION = 1
MODES = ("project", "benchmark")
STAGES = ("attempt", "final")
GATES = (
    "authorization",
    "external_impact",
    "write_scope",
    "evidence_integrity",
    "coordinator_rerun",
    "independent_review",
    "business_acceptance",
)
GATE_STATES = ("pass", "fail", "unknown", "pending")
DIMENSIONS = (
    "goal_alignment",
    "implementation_correctness",
    "verification_discipline",
    "communication_handoff",
    "recovery",
)
SEVERITIES = ("low", "medium", "high", "critical")
CATEGORIES = (
    "authorization",
    "external_impact",
    "security",
    "scope",
    "integrity",
    "correctness",
    "other",
)
ATTRIBUTIONS = ("agent", "dispatch", "requirements", "environment", "mixed", "unknown")
IDENTITY_FIELDS = (
    "evaluation_id",
    "task_id",
    "attempt_id",
    "goal_ref",
    "agent",
    "evaluator",
    "task_type",
    "complexity",
)
EVIDENCE_REFS = (
    "dispatch_ref",
    "result_ref",
    "review_ref",
    "business_acceptance_ref",
)
VERIFICATION_FIELDS = (
    "acceptance_id",
    "command",
    "cwd",
    "exit_code",
    "test_count",
    "output_ref",
)
REQUIRED_FIELDS = (
    "schema_version",
    "evaluation_id",
    "mode",
    "stage",
    "task_id",
    "attempt_id",
    "goal_ref",
    "agent",
    "evaluator",
    "reviewer",
    "task_type",
    "complexity",
    "created_at",
    "evidence",
    "gates",
    "dimensions",
    "findings",
    "metrics",
    "benchmark",
)


class RecordError(Exception):
    """Raised when an input file cannot be read or parsed."""


def _nonempty_string(value):
    return isinstance(value, str) and bool(value.strip())


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if isinstance(value, int):
        return True
    return math.isfinite(value)


def parse_created_at(value):
    """Return a timezone-aware datetime, or None when the value is invalid."""
    if not _nonempty_string(value):
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _validate_evidence(data, errors):
    evidence = data.get("evidence")
    if not isinstance(evidence, dict):
        errors.append("evidence: must be an object")
        return

    for field in EVIDENCE_REFS:
        if field in evidence:
            value = evidence[field]
            if value is not None and not isinstance(value, str):
                errors.append(f"evidence.{field}: must be a string or null")

    verification = evidence.get("verification", [])
    if not isinstance(verification, list):
        errors.append("evidence.verification: must be an array")
        return

    seen_ids = set()
    for index, entry in enumerate(verification):
        prefix = f"evidence.verification[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{prefix}: must be an object")
            continue
        for field in VERIFICATION_FIELDS:
            if field not in entry:
                errors.append(f"{prefix}.{field}: required field is missing")

        acceptance_id = entry.get("acceptance_id")
        if "acceptance_id" in entry:
            if not _nonempty_string(acceptance_id):
                errors.append(f"{prefix}.acceptance_id: must be a non-empty string")
            elif acceptance_id in seen_ids:
                errors.append(
                    f"{prefix}.acceptance_id: duplicate acceptance id {acceptance_id!r}"
                )
            else:
                seen_ids.add(acceptance_id)

        if "command" in entry and not _nonempty_string(entry.get("command")):
            errors.append(f"{prefix}.command: must be a non-empty string")

        if "cwd" in entry and not isinstance(entry.get("cwd"), str):
            errors.append(f"{prefix}.cwd: must be a string")

        if "exit_code" in entry and not _is_int(entry.get("exit_code")):
            errors.append(f"{prefix}.exit_code: must be an integer")

        test_count = entry.get("test_count")
        if "test_count" in entry and test_count is not None:
            if not (_is_int(test_count) and test_count >= 0):
                errors.append(
                    f"{prefix}.test_count: must be null or a non-negative integer"
                )

        if "output_ref" in entry and not isinstance(entry.get("output_ref"), str):
            errors.append(f"{prefix}.output_ref: must be a string")


def _validate_gates(data, errors):
    gates = data.get("gates")
    if not isinstance(gates, dict):
        errors.append("gates: must be an object")
        return
    for gate in GATES:
        if gate not in gates:
            errors.append(f"gates.{gate}: required field is missing")
        else:
            state = gates[gate]
            if not isinstance(state, str) or state not in GATE_STATES:
                errors.append(
                    f"gates.{gate}: must be one of {', '.join(GATE_STATES)}"
                )
    for gate in sorted(set(gates) - set(GATES)):
        errors.append(f"gates: unexpected gate {gate!r}")


def _validate_dimensions(data, errors):
    dimensions = data.get("dimensions")
    if not isinstance(dimensions, dict):
        errors.append("dimensions: must be an object")
        return
    for name in DIMENSIONS:
        if name not in dimensions:
            errors.append(f"dimensions.{name}: required field is missing")
    for name in sorted(set(dimensions) - set(DIMENSIONS)):
        errors.append(f"dimensions: unexpected dimension {name!r}")

    for name in DIMENSIONS:
        if name not in dimensions:
            continue
        dimension = dimensions[name]
        prefix = f"dimensions.{name}"
        if not isinstance(dimension, dict):
            errors.append(f"{prefix}: must be an object")
            continue
        for field in ("level", "rationale", "evidence_refs"):
            if field not in dimension:
                errors.append(f"{prefix}.{field}: required field is missing")

        level_is_set = False
        if "level" in dimension:
            level = dimension["level"]
            if level is None:
                pass
            elif _is_int(level) and 0 <= level <= 3:
                level_is_set = True
            else:
                errors.append(f"{prefix}.level: must be null or an integer from 0 to 3")

        if "rationale" in dimension:
            rationale = dimension["rationale"]
            if not isinstance(rationale, str):
                errors.append(f"{prefix}.rationale: must be a string")
            elif level_is_set and not _nonempty_string(rationale):
                errors.append(
                    f"{prefix}.rationale: must be a non-empty string when level is set"
                )

        if "evidence_refs" in dimension:
            refs = dimension["evidence_refs"]
            if not isinstance(refs, list):
                errors.append(f"{prefix}.evidence_refs: must be an array")
            else:
                nonempty_refs = 0
                for index, ref in enumerate(refs):
                    if not _nonempty_string(ref):
                        errors.append(
                            f"{prefix}.evidence_refs[{index}]: "
                            "must be a non-empty string"
                        )
                    else:
                        nonempty_refs += 1
                if level_is_set and nonempty_refs == 0:
                    errors.append(
                        f"{prefix}.evidence_refs: requires at least one evidence "
                        "reference when level is set"
                    )


def _validate_findings(data, errors):
    findings = data.get("findings")
    if not isinstance(findings, list):
        errors.append("findings: must be an array")
        return
    required = (
        "severity",
        "category",
        "summary",
        "evidence_ref",
        "attribution",
        "blocking",
        "gate",
    )
    for index, finding in enumerate(findings):
        prefix = f"findings[{index}]"
        if not isinstance(finding, dict):
            errors.append(f"{prefix}: must be an object")
            continue
        for field in required:
            if field not in finding:
                errors.append(f"{prefix}.{field}: required field is missing")

        severity = finding.get("severity")
        if "severity" in finding and (
            not isinstance(severity, str) or severity not in SEVERITIES
        ):
            errors.append(f"{prefix}.severity: must be one of {', '.join(SEVERITIES)}")

        category = finding.get("category")
        if "category" in finding and (
            not isinstance(category, str) or category not in CATEGORIES
        ):
            errors.append(f"{prefix}.category: must be one of {', '.join(CATEGORIES)}")

        if "summary" in finding and not _nonempty_string(finding.get("summary")):
            errors.append(f"{prefix}.summary: must be a non-empty string")

        if "evidence_ref" in finding and not _nonempty_string(
            finding.get("evidence_ref")
        ):
            errors.append(f"{prefix}.evidence_ref: must be a non-empty string")

        attribution = finding.get("attribution")
        if "attribution" in finding and (
            not isinstance(attribution, str) or attribution not in ATTRIBUTIONS
        ):
            errors.append(
                f"{prefix}.attribution: must be one of {', '.join(ATTRIBUTIONS)}"
            )

        if "blocking" in finding and not isinstance(finding.get("blocking"), bool):
            errors.append(f"{prefix}.blocking: must be a boolean")

        gate = finding.get("gate")
        if "gate" in finding and gate is not None:
            if not isinstance(gate, str) or gate not in GATES:
                errors.append(
                    f"{prefix}.gate: must be null or one of the seven gate names"
                )


def _validate_metrics(data, errors):
    metrics = data.get("metrics")
    if not isinstance(metrics, dict):
        errors.append("metrics: must be an object")
        return

    if "repair_rounds" not in metrics:
        errors.append("metrics.repair_rounds: required field is missing")
    else:
        repair_rounds = metrics["repair_rounds"]
        if not (_is_int(repair_rounds) and repair_rounds >= 0):
            errors.append("metrics.repair_rounds: must be a non-negative integer")

    if "duration_seconds" in metrics and metrics["duration_seconds"] is not None:
        duration = metrics["duration_seconds"]
        if not _is_finite_number(duration) or duration < 0:
            errors.append(
                "metrics.duration_seconds: must be null or a finite non-negative number"
            )

    if "token_usage" in metrics and metrics["token_usage"] is not None:
        token_usage = metrics["token_usage"]
        if not (_is_int(token_usage) and token_usage >= 0):
            errors.append(
                "metrics.token_usage: must be null or a non-negative integer"
            )


def _validate_benchmark(data, errors):
    if "benchmark" not in data:
        return
    benchmark = data["benchmark"]
    mode = data.get("mode")
    if mode == "project":
        if benchmark is not None:
            errors.append("benchmark: project mode requires null")
    elif mode == "benchmark":
        if not isinstance(benchmark, dict):
            errors.append(
                "benchmark: benchmark mode requires an object with "
                "benchmark_id, case_version, trial_id"
            )
            return
        for field in ("benchmark_id", "case_version", "trial_id"):
            if field not in benchmark:
                errors.append(f"benchmark.{field}: required field is missing")
            elif not _nonempty_string(benchmark[field]):
                errors.append(f"benchmark.{field}: must be a non-empty string")


def _validate_cross_field(data, errors):
    agent = data.get("agent")
    evaluator = data.get("evaluator")
    reviewer = data.get("reviewer")
    if _nonempty_string(agent):
        if _nonempty_string(evaluator) and evaluator == agent:
            errors.append(
                "evaluator: must not equal agent (self-evaluation is not allowed)"
            )
        if _nonempty_string(reviewer) and reviewer == agent:
            errors.append("reviewer: must not equal agent (self-review is not allowed)")
    if (
        _nonempty_string(evaluator)
        and _nonempty_string(reviewer)
        and evaluator == reviewer
    ):
        errors.append("reviewer: must not equal evaluator (independent review required)")

    if "reviewer" in data and data["reviewer"] is None:
        gates = data.get("gates")
        if isinstance(gates, dict) and gates.get("independent_review") in (
            "pass",
            "fail",
        ):
            errors.append(
                "reviewer: required when gates.independent_review is pass or fail"
            )


def validate_record(data):
    """Return a list of human-readable structural validation errors."""
    if not isinstance(data, dict):
        return ["root: must be a JSON object"]

    errors = []
    for field in REQUIRED_FIELDS:
        if field not in data:
            errors.append(f"{field}: required field is missing")

    if "schema_version" in data:
        schema_version = data["schema_version"]
        if not (_is_int(schema_version) and schema_version == SCHEMA_VERSION):
            errors.append(f"schema_version: must be the integer {SCHEMA_VERSION}")

    for field in IDENTITY_FIELDS:
        if field in data and not _nonempty_string(data[field]):
            errors.append(f"{field}: must be a non-empty string")

    if "mode" in data:
        mode = data["mode"]
        if not isinstance(mode, str) or mode not in MODES:
            errors.append(f"mode: must be one of {', '.join(MODES)}")

    if "stage" in data:
        stage = data["stage"]
        if not isinstance(stage, str) or stage not in STAGES:
            errors.append(f"stage: must be one of {', '.join(STAGES)}")

    if "created_at" in data and parse_created_at(data["created_at"]) is None:
        errors.append("created_at: must be an ISO 8601 timestamp with a timezone")

    if "reviewer" in data:
        reviewer = data["reviewer"]
        if reviewer is not None and not _nonempty_string(reviewer):
            errors.append("reviewer: must be a non-empty string or null")

    if "notes" in data:
        notes = data["notes"]
        if notes is not None and not isinstance(notes, str):
            errors.append("notes: must be a string or null")

    if "correction_of" in data:
        correction_of = data["correction_of"]
        if correction_of is not None and not _nonempty_string(correction_of):
            errors.append("correction_of: must be a non-empty string or null")
        elif (
            correction_of is not None
            and _nonempty_string(data.get("evaluation_id"))
            and correction_of == data["evaluation_id"]
        ):
            errors.append("correction_of: a record cannot correct itself")

    _validate_evidence(data, errors)
    _validate_gates(data, errors)
    _validate_dimensions(data, errors)
    _validate_findings(data, errors)
    _validate_metrics(data, errors)
    _validate_benchmark(data, errors)
    _validate_cross_field(data, errors)
    return errors


def _dedupe(items):
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _final_evidence_gaps(record):
    gaps = []
    evidence = record["evidence"]
    for field in EVIDENCE_REFS:
        if not _nonempty_string(evidence.get(field)):
            gaps.append(f"final evidence: evidence.{field} must be a non-empty string")

    verification = evidence.get("verification", [])
    has_full_entry = any(
        _nonempty_string(entry.get("cwd"))
        and _nonempty_string(entry.get("output_ref"))
        for entry in verification
        if isinstance(entry, dict)
    )
    if not has_full_entry:
        gaps.append(
            "final evidence: at least one verification entry must have a "
            "non-empty cwd and output_ref"
        )

    if not _nonempty_string(record.get("reviewer")):
        gaps.append("final evidence: reviewer must be named")
    return gaps


def evaluate_record(record, force_final=False):
    """Evaluate a structurally valid record.

    Hard failures always win over dimension levels: any failed gate, blocking
    finding, or nonzero verification exit code yields ``fail``. Otherwise
    unknown/pending gates or missing final evidence yield
    ``insufficient_evidence``; everything else is ``pass``.
    """
    gates = record["gates"]
    findings = record["findings"]
    verification = record["evidence"].get("verification", [])
    final_required = force_final or record["stage"] == "final"

    hard_failures = []
    for gate in GATES:
        if gates[gate] == "fail":
            hard_failures.append(f"gate {gate}: failed")
    for index, finding in enumerate(findings):
        if finding["blocking"]:
            hard_failures.append(
                f"finding {index}: blocking {finding['severity']} finding "
                f"attributed to {finding['attribution']}: {finding['summary']}"
            )
    for entry in verification:
        if entry["exit_code"] != 0:
            hard_failures.append(
                f"verification {entry['acceptance_id']}: exit code "
                f"{entry['exit_code']} is nonzero"
            )

    evidence_gaps = []
    if final_required:
        evidence_gaps.extend(_final_evidence_gaps(record))
    for gate in GATES:
        if gates[gate] in ("unknown", "pending"):
            evidence_gaps.append(f"gate {gate}: {gates[gate]}")

    if hard_failures:
        outcome = "fail"
        reasons = _dedupe(hard_failures + evidence_gaps)
    elif evidence_gaps:
        outcome = "insufficient_evidence"
        reasons = _dedupe(evidence_gaps)
    else:
        outcome = "pass"
        if final_required:
            reasons = [
                "all seven gates passed, no blocking findings, and every "
                "verification exited 0; required final evidence is present"
            ]
        else:
            reasons = [
                "all gates passed, no blocking findings, and every "
                "verification exited 0"
            ]

    return {
        "schema_version": SCHEMA_VERSION,
        "evaluation_id": record["evaluation_id"],
        "mode": record["mode"],
        "stage": record["stage"],
        "final_requirements": final_required,
        "outcome": outcome,
        "hard_failure": bool(hard_failures),
        "gates": {gate: gates[gate] for gate in GATES},
        "dimensions": {
            name: record["dimensions"][name]["level"] for name in DIMENSIONS
        },
        "metrics": {
            "repair_rounds": record["metrics"]["repair_rounds"],
            "duration_seconds": record["metrics"].get("duration_seconds"),
            "token_usage": record["metrics"].get("token_usage"),
        },
        "reasons": reasons,
    }


def _group_key(record):
    if record["mode"] == "benchmark":
        benchmark = record["benchmark"]
        return (
            record["mode"],
            record["agent"],
            record["task_type"],
            record["complexity"],
            benchmark["benchmark_id"],
            benchmark["case_version"],
        )
    return (record["mode"], record["agent"], record["task_type"], record["complexity"], "", "")


def _is_agent_hard_failure(record, outcome):
    """Return True when an attributable failure should count against the agent.

    For ``fail`` records the blocking findings carry the attribution whenever
    any exist; non-blocking findings only decide attribution when no finding is
    blocking.  A record with another outcome never enters the denominator.
    """
    if outcome != "fail":
        return False
    blocking = [finding for finding in record["findings"] if finding["blocking"]]
    considered = blocking if blocking else record["findings"]
    return any(
        finding["attribution"] in ("agent", "mixed") for finding in considered
    )


def _fail_without_findings_error(record):
    """Return an error message when a ``fail`` outcome has no finding at all."""
    if evaluate_record(record)["outcome"] != "fail" or record["findings"]:
        return None
    return (
        "findings: outcome 'fail' requires at least one finding that "
        "explains the failure and its attribution"
    )


def _resolve_corrections(records):
    """Validate ``correction_of`` links and return (records_by_id, superseded).

    Every ``evaluation_id`` must be unique.  A correction must point at an
    existing record in the same input, may not point at itself, chains may not
    cycle, and each corrected record may have only one direct correction.
    The corrected record's ``task_id``, ``attempt_id`` and ``stage`` must match
    the original.
    """
    records_by_id = {}
    line_by_id = {}
    for line_number, record in records:
        evaluation_id = record["evaluation_id"]
        if evaluation_id in records_by_id:
            raise RecordError(
                f"line {line_number}: duplicate evaluation_id {evaluation_id!r} "
                f"(first seen on line {line_by_id[evaluation_id]})"
            )
        records_by_id[evaluation_id] = record
        line_by_id[evaluation_id] = line_number

    correction_lines = {}
    for line_number, record in records:
        target = record.get("correction_of")
        if target is None:
            continue
        evaluation_id = record["evaluation_id"]
        if target == evaluation_id:
            raise RecordError(
                f"line {line_number}: correction_of: a record cannot correct itself"
            )
        if target not in records_by_id:
            raise RecordError(
                f"line {line_number}: correction_of: unknown evaluation_id "
                f"{target!r} (correction target must exist in the same input)"
            )
        if target in correction_lines:
            raise RecordError(
                f"line {line_number}: correction_of: duplicate correction for "
                f"{target!r} (first seen on line {correction_lines[target]})"
            )
        correction_lines[target] = line_number

        original = records_by_id[target]
        for field in ("task_id", "attempt_id", "stage"):
            if record[field] != original[field]:
                raise RecordError(
                    f"line {line_number}: correction_of: {field} "
                    f"{record[field]!r} must match the corrected record "
                    f"{original[field]!r}"
                )

    for evaluation_id in records_by_id:
        seen = set()
        current = evaluation_id
        while current is not None:
            if current in seen:
                raise RecordError(
                    f"correction_of: cycle detected involving {current!r}"
                )
            seen.add(current)
            current = records_by_id[current].get("correction_of")

    return records_by_id, set(correction_lines)


def build_rollup(records, min_samples):
    """Aggregate final records into capability groups.

    ``records`` is a list of ``(line_number, record)`` pairs.  Correction chains
    are resolved first: only the newest record of each chain is counted, and
    superseded records are reported as ``corrected_record_count``.  Attempt
    records are counted as ``skipped_attempt_records`` and never inflate
    samples.
    """
    if min_samples < 1:
        raise RecordError("--min-samples must be at least 1")

    _, superseded = _resolve_corrections(records)

    for line_number, record in records:
        error = _fail_without_findings_error(record)
        if error:
            raise RecordError(f"line {line_number}: {error}")

    finals = []
    skipped_attempt_records = 0
    for line_number, record in records:
        if record["evaluation_id"] in superseded:
            continue
        if record["stage"] == "final":
            finals.append((line_number, record))
        else:
            skipped_attempt_records += 1

    grouped = {}
    for line_number, record in finals:
        grouped.setdefault(_group_key(record), []).append((line_number, record))

    groups = []
    for key in sorted(grouped):
        samples = grouped[key]
        task_counts = {"pass": 0, "fail": 0, "insufficient_evidence": 0}
        agent_counts = {"pass": 0, "hard_failure": 0, "excluded": 0}
        excluded_counts = {"fail": 0, "insufficient_evidence": 0}
        dimension_counts = {
            name: {"0": 0, "1": 0, "2": 0, "3": 0, "unknown": 0}
            for name in DIMENSIONS
        }
        repair_values = []
        latest = None

        for line_number, record in samples:
            result = evaluate_record(record)
            outcome = result["outcome"]
            task_counts[outcome] += 1
            if outcome == "pass":
                agent_counts["pass"] += 1
            elif _is_agent_hard_failure(record, outcome):
                agent_counts["hard_failure"] += 1
            else:
                agent_counts["excluded"] += 1
                excluded_counts[outcome] += 1

            for name in DIMENSIONS:
                level = record["dimensions"][name]["level"]
                dimension_counts[name]["unknown" if level is None else str(level)] += 1

            repair_rounds = record["metrics"].get("repair_rounds")
            if repair_rounds is not None:
                repair_values.append(repair_rounds)

            created = parse_created_at(record["created_at"])
            candidate = (created, record["evaluation_id"], record["created_at"])
            if latest is None or candidate > latest:
                latest = candidate

        attributable = agent_counts["pass"] + agent_counts["hard_failure"]
        status = "observed" if len(samples) >= min_samples else "insufficient_data"
        pass_rate = (
            round(agent_counts["pass"] / attributable, 4)
            if status == "observed" and attributable
            else None
        )
        average_repair_rounds = (
            round(sum(repair_values) / len(repair_values), 4)
            if repair_values
            else None
        )

        groups.append(
            {
                "mode": key[0],
                "agent": key[1],
                "task_type": key[2],
                "complexity": key[3],
                "benchmark_id": key[4] if key[0] == "benchmark" else None,
                "case_version": key[5] if key[0] == "benchmark" else None,
                "sample_count": len(samples),
                "task_outcome_counts": task_counts,
                "agent_outcome_counts": agent_counts,
                "excluded_outcome_counts": excluded_counts,
                "pass_rate": pass_rate,
                "status": status,
                "dimensions": dimension_counts,
                "average_repair_rounds": average_repair_rounds,
                "latest_evaluation_at": latest[2] if latest else None,
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "min_samples": min_samples,
        "record_count": len(records),
        "final_record_count": len(finals),
        "skipped_attempt_records": skipped_attempt_records,
        "corrected_record_count": len(superseded),
        "groups": groups,
    }


def _duplicate_guard(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate object key {key!r}")
        result[key] = value
    return result


def _reject_constant(token):
    raise ValueError(f"non-finite JSON number {token}")


def load_json_text(text):
    """Parse JSON, rejecting duplicate object keys and non-finite numbers."""
    try:
        return json.loads(
            text,
            object_pairs_hook=_duplicate_guard,
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise RecordError(f"invalid JSON: {exc}") from exc
    except ValueError as exc:
        raise RecordError(str(exc)) from exc


def load_json_file(path):
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RecordError(str(exc)) from exc
    return load_json_text(text)


def load_jsonl_file(path):
    """Load non-blank JSONL lines as ``(line_number, value)`` pairs."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RecordError(str(exc)) from exc

    records = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            data = load_json_text(line)
        except RecordError as exc:
            raise RecordError(f"line {line_number}: {exc}") from exc
        records.append((line_number, data))
    return records


def run_check(path, force_final):
    try:
        data = load_json_file(path)
    except RecordError as exc:
        print(f"ERROR: {path}: {exc}", file=sys.stderr)
        return 2

    errors = validate_record(data)
    if errors:
        for error in errors:
            print(f"ERROR: {path}: {error}", file=sys.stderr)
        return 2

    if force_final and data["stage"] != "final":
        print(
            f"ERROR: {path}: stage: --final requires a final record, "
            f"not {data['stage']!r}",
            file=sys.stderr,
        )
        return 2

    result = evaluate_record(data, force_final=force_final)
    outcome_error = _fail_without_findings_error(data)
    if outcome_error:
        print(f"ERROR: {path}: {outcome_error}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    return 0 if result["outcome"] == "pass" else 1


def run_rollup(path, min_samples):
    if min_samples < 1:
        print("ERROR: --min-samples must be at least 1", file=sys.stderr)
        return 2

    try:
        records = load_jsonl_file(path)
    except RecordError as exc:
        print(f"ERROR: {path}: {exc}", file=sys.stderr)
        return 2

    errors = []
    for line_number, data in records:
        for error in validate_record(data):
            errors.append(f"line {line_number}: {error}")
    if errors:
        for error in errors:
            print(f"ERROR: {path}: {error}", file=sys.stderr)
        return 2

    try:
        result = build_rollup(records, min_samples)
    except RecordError as exc:
        print(f"ERROR: {path}: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser(
        "check", help="evaluate one JSON record"
    )
    check_parser.add_argument("path", type=Path, help="Path to a record JSON file")
    check_parser.add_argument(
        "--final",
        action="store_true",
        help="require a stage: final record and enforce final evidence",
    )

    rollup_parser = subparsers.add_parser(
        "rollup", help="aggregate final records from a JSONL file"
    )
    rollup_parser.add_argument("path", type=Path, help="Path to a JSONL file")
    rollup_parser.add_argument(
        "--min-samples",
        type=int,
        default=3,
        help="minimum final samples before a group is observed (default: 3)",
    )

    args = parser.parse_args(argv)
    if args.command == "check":
        return run_check(args.path, args.final)
    return run_rollup(args.path, args.min_samples)


if __name__ == "__main__":
    sys.exit(main())