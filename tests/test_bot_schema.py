import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "bot.py"
FIXTURE = ROOT / "tests" / "fixtures" / "bot" / "project"


class BotSchemaCliTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.project = Path(self.tempdir.name) / "project"
        shutil.copytree(FIXTURE, self.project)
        self.definition_path = (
            self.project / ".herdr" / "bots" / "test-bot" / "definition.json"
        )
        self.policy_path = self.project / ".herdr" / "bot-policy.json"
        self.definition = json.loads(
            self.definition_path.read_text(encoding="utf-8")
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def write_definition(self, definition):
        self.definition_path.write_text(
            json.dumps(definition, ensure_ascii=False), encoding="utf-8"
        )

    def write_policy(self, policy):
        self.policy_path.write_text(
            json.dumps(policy, ensure_ascii=False), encoding="utf-8"
        )

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=self.project,
            capture_output=True,
            text=True,
            check=False,
        )

    def validate(self):
        return self.run_cli(
            "validate",
            "--definition",
            str(self.definition_path),
            "--project-root",
            str(self.project),
        )

    def test_valid_definition_validates(self):
        result = self.validate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("OK", result.stdout)
        self.assertIn("test-bot", result.stdout)

    def test_missing_policy_allows_structural_validation_with_warning(self):
        self.policy_path.unlink()
        result = self.validate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("policy not found", result.stdout.lower())

    def test_missing_goal_ref_is_rejected(self):
        definition = copy.deepcopy(self.definition)
        definition["goal_ref"] = ""
        self.write_definition(definition)
        result = self.validate()
        self.assertEqual(result.returncode, 2)
        self.assertIn("goal_ref", result.stderr)

    def test_unknown_top_level_field_is_rejected(self):
        definition = copy.deepcopy(self.definition)
        definition["surprise"] = True
        self.write_definition(definition)
        result = self.validate()
        self.assertEqual(result.returncode, 2)
        self.assertIn("surprise", result.stderr)
        self.assertIn("unknown", result.stderr.lower())

    def test_workspace_path_escape_is_rejected(self):
        definition = copy.deepcopy(self.definition)
        definition["workspace"]["root"] = "../outside"
        self.write_definition(definition)
        result = self.validate()
        self.assertEqual(result.returncode, 2)
        self.assertIn("workspace.root", result.stderr)

    def test_adapter_must_be_allowed_by_policy(self):
        policy = json.loads(self.policy_path.read_text(encoding="utf-8"))
        policy["allowed_adapters"] = ["some-other-adapter"]
        self.write_policy(policy)
        result = self.validate()
        self.assertEqual(result.returncode, 2)
        self.assertIn("adapter.name", result.stderr)
        self.assertIn("not allowed", result.stderr)

    def test_autonomy_cannot_exceed_policy(self):
        policy = json.loads(self.policy_path.read_text(encoding="utf-8"))
        policy["max_autonomy"] = "L0"
        self.write_policy(policy)
        result = self.validate()
        self.assertEqual(result.returncode, 2)
        self.assertIn("autonomy_ceiling", result.stderr)

    def test_interval_below_policy_minimum_is_rejected(self):
        definition = copy.deepcopy(self.definition)
        definition["triggers"] = [
            {"type": "interval", "interval_seconds": 10, "skip_if_running": True}
        ]
        self.write_definition(definition)
        result = self.validate()
        self.assertEqual(result.returncode, 2)
        self.assertIn("interval_seconds", result.stderr)
        self.assertIn("minimum", result.stderr)

    def test_local_file_output_must_be_inside_workspace(self):
        definition = copy.deepcopy(self.definition)
        definition["outputs"][1]["path"] = ".herdr/other/reports"
        self.write_definition(definition)
        result = self.validate()
        self.assertEqual(result.returncode, 2)
        self.assertIn("outputs[1].path", result.stderr)

    def test_agent_batch_requires_agent_config(self):
        definition = copy.deepcopy(self.definition)
        definition["runtime"]["kind"] = "agent_batch"
        definition["runtime"]["agent"] = None
        self.write_definition(definition)
        result = self.validate()
        self.assertEqual(result.returncode, 2)
        self.assertIn("runtime.agent", result.stderr)

    def test_missing_adapter_config_file_is_rejected(self):
        (self.project / ".herdr" / "bots" / "test-bot" / "adapter-config.json").unlink()
        result = self.validate()
        self.assertEqual(result.returncode, 2)
        self.assertIn("adapter.config_ref", result.stderr)

    def test_malformed_json_is_an_input_error(self):
        self.definition_path.write_text("{not-json", encoding="utf-8")
        result = self.validate()
        self.assertEqual(result.returncode, 2)
        self.assertIn("ERROR", result.stderr)

    def test_non_object_workspace_is_reported_without_a_traceback(self):
        definition = copy.deepcopy(self.definition)
        definition["workspace"] = "not-an-object"
        self.write_definition(definition)
        result = self.validate()
        self.assertEqual(result.returncode, 2)
        self.assertIn("workspace", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_explain_prints_human_summary(self):
        result = self.run_cli(
            "explain",
            "--definition",
            str(self.definition_path),
            "--project-root",
            str(self.project),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("test-bot", result.stdout)
        self.assertIn("TEST-GOAL-1", result.stdout)
        self.assertIn("autonomy", result.stdout.lower())


if __name__ == "__main__":
    unittest.main()