import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_task.py"
FIXTURE = ROOT / "tests" / "fixtures" / "valid-task.json"
TEMPLATE = ROOT / "templates" / "current-task.json"


class ValidateTaskCliTest(unittest.TestCase):
    def setUp(self):
        self.valid = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def run_card(self, card, phase="dispatch"):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "current-task.json"
            if isinstance(card, str):
                path.write_text(card, encoding="utf-8")
            else:
                path.write_text(json.dumps(card), encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(SCRIPT), str(path), "--phase", phase],
                capture_output=True,
                text=True,
                check=False,
            )

    def test_valid_dispatch_card(self):
        result = self.run_card(self.valid)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("(dispatch)", result.stdout)

    def test_empty_template_is_a_structurally_valid_draft(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(TEMPLATE)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("(draft)", result.stdout)

    def test_dispatch_rejects_empty_goal(self):
        card = copy.deepcopy(self.valid)
        card["goal"] = ""
        result = self.run_card(card)
        self.assertEqual(result.returncode, 1)
        self.assertIn("goal: must be a non-empty string", result.stderr)

    def test_dispatch_rejects_empty_scope(self):
        card = copy.deepcopy(self.valid)
        card["scope"] = []
        result = self.run_card(card)
        self.assertEqual(result.returncode, 1)
        self.assertIn("scope: dispatch requires", result.stderr)

    def test_technical_acceptance_requires_command(self):
        card = copy.deepcopy(self.valid)
        del card["acceptance"][0]["command"]
        result = self.run_card(card)
        self.assertEqual(result.returncode, 1)
        self.assertIn("technical acceptance requires an executable command", result.stderr)

    def test_business_acceptance_does_not_require_command(self):
        card = copy.deepcopy(self.valid)
        card["acceptance"] = [card["acceptance"][1]]
        result = self.run_card(card)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_duplicate_acceptance_ids_are_rejected(self):
        card = copy.deepcopy(self.valid)
        card["acceptance"][1]["id"] = card["acceptance"][0]["id"]
        result = self.run_card(card)
        self.assertEqual(result.returncode, 1)
        self.assertIn("duplicate acceptance id", result.stderr)

    def test_draft_status_cannot_dispatch(self):
        card = copy.deepcopy(self.valid)
        card["status"] = "draft"
        result = self.run_card(card)
        self.assertEqual(result.returncode, 1)
        self.assertIn("dispatch requires confirmed or in_progress", result.stderr)

    def test_malformed_json_is_an_input_error(self):
        result = self.run_card("{not-json")
        self.assertEqual(result.returncode, 2)
        self.assertIn("ERROR:", result.stderr)

    def test_whitespace_only_scope_entry_is_rejected(self):
        card = copy.deepcopy(self.valid)
        card["scope"] = ["   ", "\t"]
        result = self.run_card(card)
        self.assertEqual(result.returncode, 1)
        self.assertIn("scope[0]: must be a non-empty string", result.stderr)
        self.assertIn(
            "scope: dispatch requires at least one non-blank scope entry", result.stderr
        )

    def test_whitespace_only_list_entries_are_rejected(self):
        card = copy.deepcopy(self.valid)
        card["non_goals"] = ["  "]
        card["dependencies"] = ["\t"]
        card["risks"] = [""]
        result = self.run_card(card, phase="draft")
        self.assertEqual(result.returncode, 1)
        self.assertIn("non_goals[0]: must be a non-empty string", result.stderr)
        self.assertIn("dependencies[0]: must be a non-empty string", result.stderr)
        self.assertIn("risks[0]: must be a non-empty string", result.stderr)

    def test_whitespace_only_decision_boundary_entry_is_rejected(self):
        card = copy.deepcopy(self.valid)
        card["decision_boundaries"]["autonomous"] = ["   "]
        card["decision_boundaries"]["requires_user"] = ["\t"]
        result = self.run_card(card, phase="draft")
        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "decision_boundaries.autonomous[0]: must be a non-empty string",
            result.stderr,
        )
        self.assertIn(
            "decision_boundaries.requires_user[0]: must be a non-empty string",
            result.stderr,
        )

    def test_dispatch_requires_business_acceptance(self):
        card = copy.deepcopy(self.valid)
        card["acceptance"] = [card["acceptance"][0]]
        result = self.run_card(card)
        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "acceptance: dispatch requires at least one business acceptance item",
            result.stderr,
        )

    def test_business_only_dispatch_succeeds(self):
        card = copy.deepcopy(self.valid)
        card["acceptance"] = [card["acceptance"][1]]
        self.assertNotIn("command", card["acceptance"][0])
        result = self.run_card(card)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_schema_version_true_is_rejected(self):
        card = copy.deepcopy(self.valid)
        card["schema_version"] = True
        result = self.run_card(card)
        self.assertEqual(result.returncode, 1)
        self.assertIn("schema_version: must be the integer 1", result.stderr)

    def test_schema_version_float_one_is_rejected(self):
        card = copy.deepcopy(self.valid)
        card["schema_version"] = 1.0
        result = self.run_card(card)
        self.assertEqual(result.returncode, 1)
        self.assertIn("schema_version: must be the integer 1", result.stderr)

    def test_schema_version_integer_one_is_accepted(self):
        card = copy.deepcopy(self.valid)
        card["schema_version"] = 1
        result = self.run_card(card)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
