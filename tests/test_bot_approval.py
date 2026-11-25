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


class BotApprovalCliTest(unittest.TestCase):
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

    def approve(self, **overrides):
        args = {
            "--definition": str(self.definition_path),
            "--project-root": str(self.project),
            "--approval-id": "apr-test-1",
            "--approver-ref": "local-user",
            "--source-ref": "message-test-1",
        }
        args.update(overrides)
        command = [sys.executable, str(SCRIPT), "approve"]
        for key, value in args.items():
            command.extend([key, value])
        return subprocess.run(
            command,
            cwd=self.project,
            capture_output=True,
            text=True,
            check=False,
        )

    def approved_path(self, revision=1):
        return (
            self.project
            / ".herdr"
            / "bots"
            / "approved"
            / "test-bot"
            / f"{revision}.json"
        )

    def test_approve_writes_snapshot_and_lifecycle(self):
        result = self.approve()
        self.assertEqual(result.returncode, 0, result.stderr)
        path = self.approved_path()
        self.assertTrue(path.is_file())
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(snapshot["approval"]["approval_id"], "apr-test-1")
        self.assertEqual(snapshot["approval"]["approver_ref"], "local-user")
        self.assertEqual(snapshot["definition"]["bot_id"], "test-bot")
        self.assertEqual(len(snapshot["definition_sha256"]), 64)
        lifecycle = (
            self.project / ".herdr" / "bots" / "lifecycle.jsonl"
        ).read_text(encoding="utf-8").splitlines()
        self.assertEqual(json.loads(lifecycle[-1])["event"], "approved")

    def test_approve_requires_policy(self):
        self.policy_path.unlink()
        result = self.approve()
        self.assertEqual(result.returncode, 3)
        self.assertIn("bot-policy.json", result.stderr)
        self.assertFalse(self.approved_path().exists())

    def test_approve_rejects_invalid_definition(self):
        definition = copy.deepcopy(self.definition)
        definition["goal_ref"] = ""
        self.write_definition(definition)
        result = self.approve()
        self.assertEqual(result.returncode, 2)
        self.assertIn("goal_ref", result.stderr)
        self.assertFalse(self.approved_path().exists())

    def test_approve_requires_approver_and_id(self):
        result = self.approve(**{"--approver-ref": ""})
        self.assertEqual(result.returncode, 2)
        self.assertIn("approver-ref", result.stderr)
        result = self.approve(**{"--approval-id": ""})
        self.assertEqual(result.returncode, 2)
        self.assertIn("approval-id", result.stderr)

    def test_second_approval_of_same_revision_is_rejected(self):
        first = self.approve()
        self.assertEqual(first.returncode, 0, first.stderr)
        second = self.approve(**{"--approval-id": "apr-test-2"})
        self.assertEqual(second.returncode, 2)
        self.assertIn("already approved", second.stderr)
        snapshot = json.loads(self.approved_path().read_text(encoding="utf-8"))
        self.assertEqual(snapshot["approval"]["approval_id"], "apr-test-1")

    def test_new_revision_has_a_different_digest(self):
        first = self.approve()
        self.assertEqual(first.returncode, 0, first.stderr)
        first_digest = json.loads(
            self.approved_path().read_text(encoding="utf-8")
        )["definition_sha256"]
        definition = copy.deepcopy(self.definition)
        definition["revision"] = 2
        definition["description"] = "Changed behavior."
        self.write_definition(definition)
        second = self.approve(**{"--approval-id": "apr-test-2"})
        self.assertEqual(second.returncode, 0, second.stderr)
        second_digest = json.loads(
            self.approved_path(2).read_text(encoding="utf-8")
        )["definition_sha256"]
        self.assertNotEqual(first_digest, second_digest)

    def test_expired_approval_is_rejected(self):
        result = self.approve(**{"--expires-at": "2000-01-01T00:00:00+00:00"})
        self.assertEqual(result.returncode, 2)
        self.assertIn("expires-at", result.stderr)
        self.assertFalse(self.approved_path().exists())

    def test_snapshot_keeps_the_definition_immutable(self):
        result = self.approve()
        self.assertEqual(result.returncode, 0, result.stderr)
        snapshot = json.loads(self.approved_path().read_text(encoding="utf-8"))
        definition = copy.deepcopy(self.definition)
        definition["goal_ref"] = "CHANGED-AFTER-APPROVAL"
        self.write_definition(definition)
        still = json.loads(self.approved_path().read_text(encoding="utf-8"))
        self.assertEqual(
            still["definition"]["goal_ref"],
            snapshot["definition"]["goal_ref"],
        )


if __name__ == "__main__":
    unittest.main()