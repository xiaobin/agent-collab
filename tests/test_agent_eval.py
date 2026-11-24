import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "agent_eval.py"
TEMPLATE = ROOT / "templates" / "agent-evaluation.json"
FIXTURES = ROOT / "tests" / "fixtures"
ROLLUP_FIXTURE = FIXTURES / "agent-eval-rollup.jsonl"


def load_fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class EvaluationCliTestCase(unittest.TestCase):
    def setUp(self):
        self.template = json.loads(TEMPLATE.read_text(encoding="utf-8"))

    def run_check(self, record, final=False):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "record.json"
            if isinstance(record, str):
                path.write_text(record, encoding="utf-8")
            else:
                path.write_text(json.dumps(record), encoding="utf-8")
            command = [sys.executable, str(SCRIPT), "check", str(path)]
            if final:
                command.append("--final")
            return subprocess.run(
                command, capture_output=True, text=True, check=False
            )

    def checked(self, record, final=True):
        result = self.run_check(record, final=final)
        output = json.loads(result.stdout) if result.stdout.strip() else None
        return result, output

    def run_rollup(self, records, min_samples=1):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evaluations.jsonl"
            lines = []
            for record in records:
                lines.append(
                    record if isinstance(record, str) else json.dumps(record)
                )
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "rollup",
                    str(path),
                    "--min-samples",
                    str(min_samples),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            output = json.loads(result.stdout) if result.stdout.strip() else None
            return result, output

    def final_record(self, evaluation_id="eval-final-test-001", **updates):
        record = copy.deepcopy(self.template)
        record["evaluation_id"] = evaluation_id
        record.update(updates)
        return record

    @staticmethod
    def group_for(output, agent="backend-1", task_type="backend"):
        matches = [
            group
            for group in output["groups"]
            if group["agent"] == agent and group["task_type"] == task_type
        ]
        if len(matches) != 1:
            raise AssertionError(f"expected one group, found {matches}")
        return matches[0]


class CheckOutcomeTest(EvaluationCliTestCase):
    def test_template_final_passes(self):
        result, output = self.checked(self.template)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output["outcome"], "pass")
        self.assertTrue(output["final_requirements"])

    def test_valid_final_fixture_passes(self):
        result, output = self.checked(
            load_fixture("agent-eval-project-final-valid.json")
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output["stage"], "final")

    def test_attempt_without_final_requirements_needs_more_evidence(self):
        result, output = self.checked(
            load_fixture("agent-eval-project-attempt-valid.json"), final=False
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(output["outcome"], "insufficient_evidence")
        self.assertFalse(output["final_requirements"])

    def test_attempt_cannot_be_checked_as_a_final_record(self):
        result, output = self.checked(
            load_fixture("agent-eval-project-attempt-valid.json"), final=True
        )
        self.assertEqual(result.returncode, 2)
        self.assertIsNone(output)
        self.assertIn("--final requires a final record", result.stderr)

    def test_each_failed_gate_is_a_hard_failure(self):
        gates = (
            "authorization",
            "external_impact",
            "write_scope",
            "evidence_integrity",
            "coordinator_rerun",
            "independent_review",
            "business_acceptance",
        )
        for gate in gates:
            with self.subTest(gate=gate):
                record = self.final_record()
                record["gates"][gate] = "fail"
                record["findings"] = [
                    {
                        "severity": "high",
                        "category": "other",
                        "summary": f"The {gate} gate failed.",
                        "evidence_ref": f"review#{gate}",
                        "attribution": "unknown",
                        "blocking": False,
                        "gate": gate,
                    }
                ]
                result, output = self.checked(record)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(output["outcome"], "fail")
                self.assertTrue(output["hard_failure"])
                self.assertIn(f"gate {gate}: failed", output["reasons"])

    def test_pending_and_unknown_gates_only_make_evidence_insufficient(self):
        for state in ("pending", "unknown"):
            with self.subTest(state=state):
                record = self.final_record()
                record["gates"]["business_acceptance"] = state
                result, output = self.checked(record)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(output["outcome"], "insufficient_evidence")
                self.assertFalse(output["hard_failure"])

    def test_final_business_pending_cannot_pass(self):
        record = self.final_record()
        record["gates"]["business_acceptance"] = "pending"
        result, output = self.checked(record)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(output["outcome"], "insufficient_evidence")
        self.assertIn(
            "gate business_acceptance: pending", output["reasons"]
        )

    def test_blocking_finding_is_a_hard_failure_despite_high_dimensions(self):
        record = self.final_record()
        for dimension in record["dimensions"].values():
            dimension["level"] = 3
            dimension["rationale"] = "Strong evidence."
            dimension["evidence_refs"] = [".herdr/dispatch-ledger.jsonl#review-001"]
        record["findings"] = [
            {
                "severity": "critical",
                "category": "correctness",
                "summary": "The write path reports success without writing.",
                "evidence_ref": ".herdr/dispatch-ledger.jsonl#review-001",
                "attribution": "agent",
                "blocking": True,
                "gate": "independent_review",
            }
        ]
        result, output = self.checked(record)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(output["outcome"], "fail")
        self.assertTrue(output["hard_failure"])

    def test_nonzero_verification_exit_is_a_hard_failure(self):
        record = self.final_record()
        record["evidence"]["verification"][0]["exit_code"] = 1
        record["findings"] = [
            {
                "severity": "high",
                "category": "integrity",
                "summary": "The acceptance command exited 1.",
                "evidence_ref": "review#verify-exit",
                "attribution": "mixed",
                "blocking": False,
                "gate": "coordinator_rerun",
            }
        ]
        result, output = self.checked(record)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(output["outcome"], "fail")
        self.assertTrue(output["hard_failure"])

    def test_null_dimensions_do_not_block_a_supported_pass(self):
        record = self.final_record()
        for dimension in record["dimensions"].values():
            dimension["level"] = None
            dimension["rationale"] = "No observation."
            dimension["evidence_refs"] = []
        result, output = self.checked(record)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output["outcome"], "pass")
        self.assertTrue(all(level is None for level in output["dimensions"].values()))

    def test_missing_final_evidence_ref_is_insufficient(self):
        for field in (
            "dispatch_ref",
            "result_ref",
            "review_ref",
            "business_acceptance_ref",
        ):
            with self.subTest(field=field):
                record = self.final_record()
                record["evidence"][field] = ""
                result, output = self.checked(record)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(output["outcome"], "insufficient_evidence")
                self.assertTrue(
                    any(field in reason for reason in output["reasons"])
                )

    def test_final_verification_needs_cwd_and_output_ref(self):
        for field in ("cwd", "output_ref"):
            with self.subTest(field=field):
                record = self.final_record()
                record["evidence"]["verification"][0][field] = ""
                result, output = self.checked(record)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(output["outcome"], "insufficient_evidence")

    def test_final_without_named_reviewer_is_insufficient(self):
        record = self.final_record()
        record["reviewer"] = None
        record["gates"]["independent_review"] = "unknown"
        result, output = self.checked(record)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(output["outcome"], "insufficient_evidence")
        self.assertTrue(
            any("reviewer" in reason for reason in output["reasons"])
        )

    def test_unknown_optional_metrics_stay_null(self):
        result, output = self.checked(self.template)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(output["metrics"]["duration_seconds"])
        self.assertIsNone(output["metrics"]["token_usage"])


class CheckValidationTest(EvaluationCliTestCase):
    def test_schema_version_must_be_the_integer_one(self):
        for value in (True, 1.0, 2, "1"):
            with self.subTest(value=value):
                record = self.final_record()
                record["schema_version"] = value
                result = self.run_check(record)
                self.assertEqual(result.returncode, 2)
                self.assertIn("schema_version", result.stderr)

    def test_invalid_gate_state_is_rejected(self):
        record = self.final_record()
        record["gates"]["authorization"] = "ok"
        result = self.run_check(record)
        self.assertEqual(result.returncode, 2)
        self.assertIn("gates.authorization", result.stderr)

    def test_missing_and_unexpected_gates_are_rejected(self):
        record = self.final_record()
        del record["gates"]["write_scope"]
        record["gates"]["extra_gate"] = "pass"
        result = self.run_check(record)
        self.assertEqual(result.returncode, 2)
        self.assertIn("gates.write_scope: required", result.stderr)
        self.assertIn("unexpected gate 'extra_gate'", result.stderr)

    def test_dimension_levels_are_null_or_integers_zero_to_three(self):
        for value in (True, 1.0, -1, 4, "3"):
            with self.subTest(value=value):
                record = self.final_record()
                record["dimensions"]["goal_alignment"]["level"] = value
                result = self.run_check(record)
                self.assertEqual(result.returncode, 2)
                self.assertIn("goal_alignment.level", result.stderr)

    def test_set_dimension_needs_rationale_and_evidence(self):
        record = self.final_record()
        record["dimensions"]["goal_alignment"]["rationale"] = " "
        record["dimensions"]["goal_alignment"]["evidence_refs"] = []
        result = self.run_check(record)
        self.assertEqual(result.returncode, 2)
        self.assertIn("rationale", result.stderr)
        self.assertIn("evidence_refs", result.stderr)

    def test_missing_and_unexpected_dimensions_are_rejected(self):
        record = self.final_record()
        del record["dimensions"]["recovery"]
        record["dimensions"]["speed"] = {
            "level": None,
            "rationale": "",
            "evidence_refs": [],
        }
        result = self.run_check(record)
        self.assertEqual(result.returncode, 2)
        self.assertIn("dimensions.recovery: required", result.stderr)
        self.assertIn("unexpected dimension 'speed'", result.stderr)

    def test_blocking_must_be_a_real_boolean(self):
        record = self.final_record()
        record["findings"] = [
            {
                "severity": "high",
                "category": "correctness",
                "summary": "A blocking defect.",
                "evidence_ref": "review#1",
                "attribution": "agent",
                "blocking": "true",
                "gate": "independent_review",
            }
        ]
        result = self.run_check(record)
        self.assertEqual(result.returncode, 2)
        self.assertIn("blocking: must be a boolean", result.stderr)

    def test_invalid_attribution_and_finding_gate_are_rejected(self):
        record = self.final_record()
        record["findings"] = [
            {
                "severity": "high",
                "category": "correctness",
                "summary": "A blocking defect.",
                "evidence_ref": "review#1",
                "attribution": "someone",
                "blocking": True,
                "gate": "speed",
            }
        ]
        result = self.run_check(record)
        self.assertEqual(result.returncode, 2)
        self.assertIn("findings[0].attribution", result.stderr)
        self.assertIn("findings[0].gate", result.stderr)

    def test_repair_rounds_must_be_a_non_negative_integer(self):
        for value in (-1, True, 1.0, "1"):
            with self.subTest(value=value):
                record = self.final_record()
                record["metrics"]["repair_rounds"] = value
                result = self.run_check(record)
                self.assertEqual(result.returncode, 2)
                self.assertIn("repair_rounds", result.stderr)

    def test_non_finite_and_negative_optional_metrics_are_rejected(self):
        raw = TEMPLATE.read_text(encoding="utf-8").replace(
            '"duration_seconds": null', '"duration_seconds": NaN', 1
        )
        result = self.run_check(raw)
        self.assertEqual(result.returncode, 2)
        self.assertIn("non-finite", result.stderr)

        record = self.final_record()
        record["metrics"]["token_usage"] = -1
        result = self.run_check(record)
        self.assertEqual(result.returncode, 2)
        self.assertIn("token_usage", result.stderr)

    def test_duplicate_acceptance_ids_are_rejected(self):
        record = self.final_record()
        record["evidence"]["verification"].append(
            copy.deepcopy(record["evidence"]["verification"][0])
        )
        result = self.run_check(record)
        self.assertEqual(result.returncode, 2)
        self.assertIn("duplicate acceptance id", result.stderr)

    def test_evaluator_and_reviewer_cannot_be_the_agent(self):
        record = self.final_record()
        record["evaluator"] = record["agent"]
        result = self.run_check(record)
        self.assertEqual(result.returncode, 2)
        self.assertIn("self-evaluation", result.stderr)

        record = self.final_record()
        record["reviewer"] = record["agent"]
        result = self.run_check(record)
        self.assertEqual(result.returncode, 2)
        self.assertIn("self-review", result.stderr)

    def test_reviewer_must_differ_from_evaluator(self):
        record = self.final_record()
        record["reviewer"] = record["evaluator"]
        result = self.run_check(record)
        self.assertEqual(result.returncode, 2)
        self.assertIn("must not equal evaluator", result.stderr)

    def test_reviewer_is_required_when_independent_review_ran(self):
        record = self.final_record()
        record["reviewer"] = None
        result = self.run_check(record)
        self.assertEqual(result.returncode, 2)
        self.assertIn("reviewer: required", result.stderr)

    def test_reviewer_may_be_null_while_review_is_pending(self):
        record = self.final_record()
        record["reviewer"] = None
        record["gates"]["independent_review"] = "pending"
        result, output = self.checked(record)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(output["outcome"], "insufficient_evidence")
        self.assertIn("gate independent_review: pending", output["reasons"])

    def test_fail_without_any_finding_is_a_structural_error(self):
        record = self.final_record()
        record["gates"]["write_scope"] = "fail"
        record["findings"] = []
        result = self.run_check(record)
        self.assertEqual(result.returncode, 2)
        self.assertIn("at least one finding", result.stderr)

    def test_correction_of_must_be_a_string_or_null(self):
        for value in ("", " ", 7, [], {}):
            with self.subTest(value=value):
                record = self.final_record()
                record["correction_of"] = value
                result = self.run_check(record)
                self.assertEqual(result.returncode, 2)
                self.assertIn("correction_of", result.stderr)

    def test_check_rejects_a_self_correction(self):
        record = self.final_record("eval-self-check")
        record["correction_of"] = "eval-self-check"
        result = self.run_check(record)
        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot correct itself", result.stderr)

    def test_created_at_needs_a_timezone(self):
        for value in ("2026-11-24T00:00:00", "not-a-time", ""):
            with self.subTest(value=value):
                record = self.final_record()
                record["created_at"] = value
                result = self.run_check(record)
                self.assertEqual(result.returncode, 2)
                self.assertIn("created_at", result.stderr)

    def test_benchmark_contract_is_checked_both_ways(self):
        record = self.final_record()
        record["mode"] = "project"
        record["benchmark"] = {
            "benchmark_id": "suite",
            "case_version": "1",
            "trial_id": "trial-1",
        }
        result = self.run_check(record)
        self.assertEqual(result.returncode, 2)
        self.assertIn("project mode requires null", result.stderr)

        record = self.final_record()
        record["mode"] = "benchmark"
        record["benchmark"] = {"benchmark_id": "suite", "trial_id": "trial-1"}
        result = self.run_check(record)
        self.assertEqual(result.returncode, 2)
        self.assertIn("benchmark.case_version", result.stderr)

    def test_invalid_json_and_duplicate_keys_are_rejected(self):
        result = self.run_check("{not json")
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid JSON", result.stderr)

        result = self.run_check('{"schema_version": 1, "schema_version": 1}')
        self.assertEqual(result.returncode, 2)
        self.assertIn("duplicate object key", result.stderr)

    def test_missing_input_file_is_a_cli_error(self):
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "check",
                str(FIXTURES / "does-not-exist.json"),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("ERROR:", result.stderr)


class FixtureCliTest(EvaluationCliTestCase):
    def test_benchmark_attempt_fixture_is_valid_but_not_final(self):
        record = load_fixture("agent-eval-benchmark-attempt-valid.json")
        result, output = self.checked(record, final=False)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(output["outcome"], "insufficient_evidence")
        self.assertNotIn("ERROR:", result.stderr)

    def test_benchmark_invalid_missing_id_fixture_is_rejected(self):
        record = load_fixture("agent-eval-benchmark-invalid-missing-id.json")
        result, output = self.checked(record)
        self.assertEqual(result.returncode, 2)
        self.assertIsNone(output)
        self.assertIn("benchmark.case_version", result.stderr)

    def test_project_self_eval_fixture_is_rejected(self):
        record = load_fixture("agent-eval-project-final-invalid-self-eval.json")
        result, output = self.checked(record)
        self.assertEqual(result.returncode, 2)
        self.assertIsNone(output)
        self.assertIn("self-evaluation", result.stderr)


class RollupTest(EvaluationCliTestCase):
    def test_rollup_fixture_skips_attempt_records(self):
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "rollup",
                str(ROLLUP_FIXTURE),
                "--min-samples",
                "1",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["record_count"], 2)
        self.assertEqual(output["final_record_count"], 1)
        self.assertEqual(output["skipped_attempt_records"], 1)
        self.assertEqual(self.group_for(output)["sample_count"], 1)

    def test_rollup_groups_task_type_and_complexity_separately(self):
        first = self.final_record("eval-a")
        second = self.final_record("eval-b", task_type="frontend")
        third = self.final_record("eval-c", complexity="high")
        result, output = self.run_rollup([first, second, third])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(output["groups"]), 3)

    def test_benchmark_trials_group_but_versions_do_not_mix(self):
        first = load_fixture("agent-eval-benchmark-final-valid.json")
        second = copy.deepcopy(first)
        second["evaluation_id"] = "eval-benchmark-final-002"
        second["benchmark"]["trial_id"] = "trial-002"
        third = copy.deepcopy(first)
        third["evaluation_id"] = "eval-benchmark-final-003"
        third["benchmark"]["case_version"] = "2.0.0"
        result, output = self.run_rollup([first, second, third])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(output["groups"]), 2)
        versions = {
            group["case_version"]: group["sample_count"]
            for group in output["groups"]
        }
        self.assertEqual(versions, {"1.2.0": 2, "2.0.0": 1})

    def test_min_samples_controls_observed_status(self):
        record = self.final_record("eval-small")
        result, output = self.run_rollup([record], min_samples=3)
        self.assertEqual(result.returncode, 0, result.stderr)
        group = self.group_for(output)
        self.assertEqual(group["status"], "insufficient_data")
        self.assertEqual(group["sample_count"], 1)
        self.assertIsNone(group["pass_rate"])
        self.assertEqual(group["task_outcome_counts"]["pass"], 1)
        self.assertEqual(group["agent_outcome_counts"]["pass"], 1)

        result, output = self.run_rollup([record], min_samples=1)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.group_for(output)["status"], "observed")
        self.assertEqual(self.group_for(output)["pass_rate"], 1.0)

    def test_environment_failure_is_excluded_from_agent_pass_rate(self):
        valid = load_fixture("agent-eval-project-final-valid.json")
        environment = load_fixture(
            "agent-eval-project-final-environment-failure.json"
        )
        result, output = self.run_rollup([valid, environment])
        self.assertEqual(result.returncode, 0, result.stderr)
        group = self.group_for(output)
        self.assertEqual(group["sample_count"], 2)
        self.assertEqual(group["task_outcome_counts"]["pass"], 1)
        self.assertEqual(group["task_outcome_counts"]["fail"], 1)
        self.assertEqual(group["agent_outcome_counts"]["pass"], 1)
        self.assertEqual(group["agent_outcome_counts"]["hard_failure"], 0)
        self.assertEqual(group["excluded_outcome_counts"]["fail"], 1)
        self.assertEqual(group["pass_rate"], 1.0)

    def test_agent_blocking_failure_enters_the_denominator(self):
        valid = load_fixture("agent-eval-project-final-valid.json")
        failure = load_fixture(
            "agent-eval-project-final-invalid-blocking.json"
        )
        result, output = self.run_rollup([valid, failure])
        self.assertEqual(result.returncode, 0, result.stderr)
        group = self.group_for(output)
        self.assertEqual(group["agent_outcome_counts"]["hard_failure"], 1)
        self.assertEqual(group["pass_rate"], 0.5)

    def test_mixed_attribution_counts_as_agent_hard_failure(self):
        valid = load_fixture("agent-eval-project-final-valid.json")
        failure = load_fixture(
            "agent-eval-project-final-invalid-blocking.json"
        )
        failure["evaluation_id"] = "eval-mixed"
        failure["findings"][0]["attribution"] = "mixed"
        result, output = self.run_rollup([valid, failure])
        self.assertEqual(result.returncode, 0, result.stderr)
        group = self.group_for(output)
        self.assertEqual(group["agent_outcome_counts"]["hard_failure"], 1)
        self.assertEqual(group["pass_rate"], 0.5)

    def test_non_agent_attributions_are_all_excluded(self):
        for attribution in ("environment", "dispatch", "requirements", "unknown"):
            with self.subTest(attribution=attribution):
                failure = load_fixture(
                    "agent-eval-project-final-invalid-blocking.json"
                )
                failure["evaluation_id"] = f"eval-{attribution}"
                failure["findings"][0]["attribution"] = attribution
                result, output = self.run_rollup([failure])
                self.assertEqual(result.returncode, 0, result.stderr)
                group = self.group_for(output)
                self.assertEqual(group["agent_outcome_counts"]["hard_failure"], 0)
                self.assertEqual(group["agent_outcome_counts"]["excluded"], 1)
                self.assertIsNone(group["pass_rate"])

    def test_failed_gate_with_nonblocking_agent_finding_is_a_hard_failure(self):
        record = self.final_record()
        record["gates"]["business_acceptance"] = "fail"
        record["findings"] = [
            {
                "severity": "low",
                "category": "other",
                "summary": "The agent missed a business rule.",
                "evidence_ref": "review#nonblocking",
                "attribution": "agent",
                "blocking": False,
                "gate": "business_acceptance",
            }
        ]
        result, output = self.run_rollup([record])
        self.assertEqual(result.returncode, 0, result.stderr)
        group = self.group_for(output)
        self.assertEqual(group["task_outcome_counts"]["fail"], 1)
        self.assertEqual(group["agent_outcome_counts"]["hard_failure"], 1)
        self.assertEqual(group["agent_outcome_counts"]["excluded"], 0)
        self.assertEqual(group["pass_rate"], 0.0)

    def test_blocking_findings_decide_attribution_when_they_exist(self):
        record = self.final_record()
        record["gates"]["business_acceptance"] = "fail"
        record["findings"] = [
            {
                "severity": "high",
                "category": "other",
                "summary": "The environment was unavailable.",
                "evidence_ref": "review#blocking",
                "attribution": "environment",
                "blocking": True,
                "gate": "business_acceptance",
            },
            {
                "severity": "low",
                "category": "other",
                "summary": "A non-blocking style issue.",
                "evidence_ref": "review#nonblocking",
                "attribution": "agent",
                "blocking": False,
                "gate": None,
            },
        ]
        result, output = self.run_rollup([record])
        self.assertEqual(result.returncode, 0, result.stderr)
        group = self.group_for(output)
        self.assertEqual(group["agent_outcome_counts"]["hard_failure"], 0)
        self.assertEqual(group["agent_outcome_counts"]["excluded"], 1)
        self.assertEqual(group["excluded_outcome_counts"]["fail"], 1)
        self.assertIsNone(group["pass_rate"])

    def test_average_repair_rounds_uses_known_values(self):
        first = self.final_record("eval-repair-1")
        first["metrics"]["repair_rounds"] = 0
        second = self.final_record("eval-repair-2")
        second["metrics"]["repair_rounds"] = 2
        result, output = self.run_rollup([first, second])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.group_for(output)["average_repair_rounds"], 1.0)

    def test_unknown_dimension_is_counted_as_unknown(self):
        result, output = self.run_rollup([self.final_record()])
        self.assertEqual(result.returncode, 0, result.stderr)
        group = self.group_for(output)
        self.assertEqual(group["dimensions"]["recovery"]["unknown"], 1)

    def test_latest_timestamp_compares_timezones(self):
        earlier = self.final_record(
            "eval-time-1", created_at="2026-01-01T00:00:00Z"
        )
        later = self.final_record(
            "eval-time-2", created_at="2025-12-31T20:00:00-05:00"
        )
        result, output = self.run_rollup([earlier, later])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.group_for(output)["latest_evaluation_at"],
            "2025-12-31T20:00:00-05:00",
        )

    def test_empty_input_produces_an_empty_rollup(self):
        result, output = self.run_rollup([])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output["record_count"], 0)
        self.assertEqual(output["final_record_count"], 0)
        self.assertEqual(output["groups"], [])

    def test_duplicate_evaluation_ids_are_rejected(self):
        record = self.final_record("eval-duplicate")
        result, output = self.run_rollup([record, copy.deepcopy(record)])
        self.assertEqual(result.returncode, 2)
        self.assertIn("duplicate evaluation_id", result.stderr)

    def test_fail_without_findings_cannot_inflate_pass_rate(self):
        passing = load_fixture("agent-eval-project-final-valid.json")
        failing = self.final_record("eval-fail-no-findings")
        failing["gates"]["write_scope"] = "fail"
        failing["findings"] = []
        result, output = self.run_rollup([passing, failing])
        self.assertEqual(result.returncode, 2)
        self.assertIsNone(output)
        self.assertIn("at least one finding", result.stderr)

    def test_malformed_jsonl_reports_the_line(self):
        result, output = self.run_rollup(["", "{broken"])
        self.assertEqual(result.returncode, 2)
        self.assertIn("line 2", result.stderr)

    def test_invalid_record_in_jsonl_is_rejected(self):
        broken = self.final_record()
        broken["schema_version"] = True
        result, output = self.run_rollup([broken])
        self.assertEqual(result.returncode, 2)
        self.assertIn("line 1", result.stderr)
        self.assertIn("schema_version", result.stderr)

    def test_min_samples_below_one_is_rejected(self):
        result, output = self.run_rollup([self.final_record()], min_samples=0)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--min-samples", result.stderr)


class CorrectionTest(EvaluationCliTestCase):
    def failing_final(self, evaluation_id):
        record = self.final_record(evaluation_id)
        record["gates"]["business_acceptance"] = "fail"
        record["findings"] = [
            {
                "severity": "high",
                "category": "correctness",
                "summary": "The first evaluation missed a defect.",
                "evidence_ref": "review#correction",
                "attribution": "agent",
                "blocking": False,
                "gate": "business_acceptance",
            }
        ]
        return record

    def test_correction_replaces_the_earlier_record(self):
        old = self.failing_final("eval-corrected")
        new = self.final_record("eval-correction")
        new["correction_of"] = "eval-corrected"
        result, output = self.run_rollup([old, new])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output["record_count"], 2)
        self.assertEqual(output["final_record_count"], 1)
        self.assertEqual(output["corrected_record_count"], 1)
        group = self.group_for(output)
        self.assertEqual(group["sample_count"], 1)
        self.assertEqual(group["task_outcome_counts"]["pass"], 1)
        self.assertEqual(group["task_outcome_counts"]["fail"], 0)
        self.assertEqual(group["pass_rate"], 1.0)

    def test_correction_chain_counts_only_the_newest_record(self):
        first = self.failing_final("eval-chain-a")
        second = self.failing_final("eval-chain-b")
        second["correction_of"] = "eval-chain-a"
        third = self.final_record("eval-chain-c")
        third["correction_of"] = "eval-chain-b"
        result, output = self.run_rollup([first, second, third])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output["record_count"], 3)
        self.assertEqual(output["final_record_count"], 1)
        self.assertEqual(output["corrected_record_count"], 2)
        group = self.group_for(output)
        self.assertEqual(group["sample_count"], 1)
        self.assertEqual(group["task_outcome_counts"]["pass"], 1)
        self.assertEqual(group["pass_rate"], 1.0)

    def test_corrected_attempt_record_keeps_the_skip_semantics(self):
        original = load_fixture("agent-eval-project-attempt-valid.json")
        correction = copy.deepcopy(original)
        correction["evaluation_id"] = "eval-project-attempt-001-correction"
        correction["correction_of"] = original["evaluation_id"]
        final = load_fixture("agent-eval-project-final-valid.json")
        result, output = self.run_rollup([original, correction, final])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output["record_count"], 3)
        self.assertEqual(output["final_record_count"], 1)
        self.assertEqual(output["skipped_attempt_records"], 1)
        self.assertEqual(output["corrected_record_count"], 1)
        self.assertEqual(self.group_for(output)["sample_count"], 1)

    def test_correction_target_must_exist(self):
        correction = self.final_record("eval-correction")
        correction["correction_of"] = "eval-does-not-exist"
        result, output = self.run_rollup([correction])
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown evaluation_id", result.stderr)

    def test_correction_cannot_point_at_itself(self):
        record = self.final_record("eval-self-correction")
        record["correction_of"] = "eval-self-correction"
        result, output = self.run_rollup([record])
        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot correct itself", result.stderr)

    def test_correction_cycle_is_rejected(self):
        first = self.final_record("eval-cycle-a")
        second = self.final_record("eval-cycle-b")
        first["correction_of"] = "eval-cycle-b"
        second["correction_of"] = "eval-cycle-a"
        result, output = self.run_rollup([first, second])
        self.assertEqual(result.returncode, 2)
        self.assertIn("cycle detected", result.stderr)

    def test_a_corrected_record_can_have_only_one_direct_correction(self):
        old = self.failing_final("eval-duplicate-target")
        first = self.final_record("eval-correction-1")
        first["correction_of"] = "eval-duplicate-target"
        second = self.final_record("eval-correction-2")
        second["correction_of"] = "eval-duplicate-target"
        result, output = self.run_rollup([old, first, second])
        self.assertEqual(result.returncode, 2)
        self.assertIn("duplicate correction", result.stderr)

    def test_correction_must_keep_task_attempt_and_stage(self):
        cases = {
            "task_id": {"task_id": "OTHER-TASK"},
            "attempt_id": {"attempt_id": "attempt-002"},
            "stage": {"stage": "attempt"},
        }
        for field, update in cases.items():
            with self.subTest(field=field):
                old = self.failing_final("eval-original")
                correction = self.final_record("eval-correction")
                correction["correction_of"] = "eval-original"
                correction.update(update)
                result, output = self.run_rollup([old, correction])
                self.assertEqual(result.returncode, 2)
                self.assertIn(field, result.stderr)
                self.assertIn("must match the corrected record", result.stderr)


if __name__ == "__main__":
    unittest.main()